import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from backend import agent, messenger, messenger_service
from backend.database import transaction
from backend.messenger_format import format_messenger_response
from backend.product_links import product_path, public_product_url, public_order_url
from backend.records import MessengerEvent, ProductImage
from models import Order


@pytest.fixture(autouse=True)
def origin(monkeypatch):
    monkeypatch.setenv('PUBLIC_SITE_URL', 'https://shop.example.com')
    monkeypatch.delenv('RENDER_EXTERNAL_URL', raising=False)


def test_canonical_sku_link_and_escaping():
    assert product_path('SKU_183') == '/products/SKU_183'
    assert public_product_url('SKU_183') == 'https://shop.example.com/products/SKU_183'
    assert public_order_url('SKU_183') == 'https://shop.example.com/products/SKU_183#order-form'
    assert '?' not in product_path('SKU_183?next=https://evil.example')


def test_render_external_url_is_a_trusted_fallback(monkeypatch):
    monkeypatch.setenv('PUBLIC_SITE_URL', ' ')
    monkeypatch.setenv('RENDER_EXTERNAL_URL', ' https://omnicart.example.onrender.com/ ')
    assert public_product_url('SKU_101') == 'https://omnicart.example.onrender.com/products/SKU_101'


@pytest.mark.parametrize('origin', [
    '', 'http://shop.example.com', 'https://localhost', 'https://127.0.0.1',
    'https://shop.example.com/redirect', 'https://shop.example.com?next=https://evil.example',
    'https://shop.example.com#fragment', 'https://user:password@shop.example.com',
])
def test_unsafe_or_missing_public_origin_does_not_create_links(monkeypatch, origin):
    monkeypatch.setenv('PUBLIC_SITE_URL', origin)
    assert public_product_url('SKU_101') is None


def test_public_product_detail_is_exact_and_does_not_need_a_session(client):
    response = client.get('/api/public/products/SKU_183')
    assert response.status_code == 200
    data = response.json()
    assert data['product_id'] == 'SKU_183'
    assert data['name'] == 'Batteries Pack'
    assert data['image_url'] == '/api/products/SKU_183/image'
    assert data['image_kind'] == 'illustration'
    assert 'customer_id' not in data and 'shop_id' not in data
    assert client.get('/api/public/products/SKU_999999').status_code == 404
    assert client.get('/api/public/products/OTHER_SECRET').status_code == 404
    assert client.get('/api/profile').status_code == 401
    with transaction() as db:
        assert db.query(Order).count() == 0


def test_public_product_detail_uses_live_price_stock_and_saved_image_label(client):
    from models import Product
    with transaction() as db:
        product = db.get(Product, 'SKU_101')
        product.discounted_price = 17.25
        product.stock_count = 3
        db.add(ProductImage(product_id='SKU_101', shop_id='shop_01',
                            image_url='https://cdn.example.com/shirt.jpg', alt_text='T-Shirt representative image'))
    data = client.get('/api/public/products/SKU_101').json()
    assert data['effective_price'] == 17.25 and data['stock_count'] == 3
    assert data['image_kind'] == 'representative'


def product_card():
    return {'product_id': 'SKU_183', 'name': 'Batteries Pack', 'effective_price': 14.99, 'stock_count': 80,
            'image_url': 'https://cdn.example.com/batteries.jpg', 'alt_text': 'Product photo',
            'product_url': 'https://malicious.example/not-the-product'}


def test_generic_template_has_photo_price_stock_and_exact_web_link(monkeypatch):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', ' test-page-token\n')
    original = httpx.AsyncClient
    requests, options = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'message_id': 'card'})

    def factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(messenger.httpx, 'AsyncClient', factory)
    asyncio.run(messenger.send_product_cards('200', [product_card()]))
    body = json.loads(requests[0].content)
    assert body['recipient'] == {'id': '200'}
    assert body['messaging_type'] == 'RESPONSE'
    assert requests[0].headers['authorization'] == 'Bearer test-page-token'
    template = body['message']['attachment']['payload']
    assert template['template_type'] == 'generic'
    element = template['elements'][0]
    assert element['title'] == 'Batteries Pack'
    assert '৳14.99' in element['subtitle'] and '80 in stock' in element['subtitle']
    assert element['image_url'] == 'https://cdn.example.com/batteries.jpg'
    assert element['buttons'] == [{'type': 'web_url', 'title': 'Order now',
                                   'url': 'https://shop.example.com/products/SKU_183#order-form',
                                   'webview_height_ratio': 'full'},
                                  {'type': 'postback', 'title': 'Order in chat', 'payload': 'ORDER_NOW:SKU_183'},
                                  {'type': 'postback', 'title': 'More Details', 'payload': 'DETAILS:SKU_183'}]
    assert element['default_action']['url'] == 'https://shop.example.com/products/SKU_183'
    assert options[0]['http2'] is False and options[0]['timeout'] == 15.0


def test_cards_keep_native_ordering_when_no_public_site_is_configured(monkeypatch):
    monkeypatch.setenv('PUBLIC_SITE_URL', '')
    element = messenger.product_card(product_card())
    assert 'default_action' not in element
    assert all(button['type'] == 'postback' for button in element['buttons'])
    assert [button['payload'] for button in element['buttons']] == ['ORDER_NOW:SKU_183', 'DETAILS:SKU_183']


def test_formatter_hydrates_the_correct_photo_and_link_for_the_same_sku(store):
    with transaction() as db:
        db.add(ProductImage(product_id='SKU_183', shop_id='shop_01',
                            image_url='https://cdn.example.com/batteries.jpg', alt_text='Product photo'))
    reply = format_messenger_response({'answer': 'Batteries Pack [product:SKU_183]', 'show_images': True}, 'shop_01', 'image')
    assert reply.products[0]['product_url'] == 'https://shop.example.com/products/SKU_183'
    assert reply.products[0]['image_url'] == 'https://cdn.example.com/batteries.jpg'
    assert reply.text.startswith('Batteries Pack\n\nView product & fill in the order form:')
    assert 'https://shop.example.com/products/SKU_183#order-form' in reply.text


def test_messenger_sends_text_then_clickable_card_without_duplicate_delivery(store, monkeypatch):
    with transaction() as db:
        db.add(ProductImage(product_id='SKU_183', shop_id='shop_01',
                            image_url='https://cdn.example.com/batteries.jpg', alt_text='Product photo'))
    monkeypatch.setattr(agent, 'chat_model', lambda **kwargs: pytest.fail('Picture requests need no LLM'))
    delivered = []

    async def text(sender, message):
        delivered.append(('text', sender, message))

    async def cards(sender, products):
        delivered.append(('cards', sender, products))

    monkeypatch.setattr(messenger_service, 'send_text_reply', text)
    monkeypatch.setattr(messenger_service, 'send_product_cards', cards)
    monkeypatch.setattr(messenger_service, 'send_product_image', AsyncMock(side_effect=AssertionError('Use clickable cards')))
    for _ in range(2):
        asyncio.run(messenger_service.process_message('100', '200', 'linked-card-mid', 'Batteries Pack image'))
    assert [item[0] for item in delivered] == ['text', 'cards']
    assert delivered[1][2][0]['product_url'].endswith('/products/SKU_183')
    with transaction() as db:
        assert db.get(MessengerEvent, ('100', 'linked-card-mid')).status == 'sent'
