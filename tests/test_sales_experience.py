import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage

from backend import agent, messenger, messenger_service
from backend.catalog import browse_catalog, is_catalog_browse_question
from backend.chat_memory import _previous_sources, load_conversation
from backend.conversation import rewrite_product_query
from backend.database import transaction
from backend.media import public_image_url
from backend.messenger_format import format_messenger_response, parse_messenger_text
from backend.records import ChatTurn, MessengerContact, MessengerEvent, ProductImage
from backend.retrieval import product_dict
from models import ChatHistory, Product


@pytest.fixture
def no_models(monkeypatch):
    def fail(**kwargs):
        pytest.fail('This social/browse/follow-up turn must not need an LLM')
    monkeypatch.setattr(agent, 'chat_model', fail)


@pytest.fixture(autouse=True)
def isolated_image_only_origin(monkeypatch):
    # These tests exercise the backwards-compatible native-image fallback, not
    # deployment-specific clickable cards. Never inherit a real Render origin.
    monkeypatch.delenv('PUBLIC_SITE_URL', raising=False)
    monkeypatch.delenv('RENDER_EXTERNAL_URL', raising=False)


def source_for(product_id='SKU_101'):
    with transaction() as db:
        return agent.catalog_product_source(product_dict(db.get(Product, ('shop_01', product_id))))


@pytest.mark.parametrize('query', ['Hello ai', 'Hello AI!', 'Hello, AI', 'Good morning', 'Hey there', 'How are you?', 'Thanks bot', 'হ্যালো'])
def test_social_turns_skip_product_lookup_and_rag(store, no_models, monkeypatch, query):
    monkeypatch.setattr(agent, 'named_products', lambda *args: pytest.fail('No catalog scan for small talk'))
    monkeypatch.setattr(agent.graph_rag, 'retrieve', lambda *args, **kwargs: pytest.fail('No RAG for small talk'))
    response = agent.run_agent(store['customer_id'], 'shop_01', query, [])
    assert response['answer']
    assert response['sources'] == []
    assert not response['degraded'] and not response['handoff']
    assert response['usage']['calls'] == 0


@pytest.mark.parametrize('query', [
    'Give me some product name', 'Can you tell me about your product', 'show products',
    'what do you sell', 'What products do you have?', "What's in your catalog?",
    'Can I see your catalogue?', 'আপনাদের পণ্য দেখান',
])
def test_general_discovery_lists_five_live_in_stock_products(store, no_models, monkeypatch, query):
    monkeypatch.setattr(agent.graph_rag, 'retrieve', lambda *args, **kwargs: pytest.fail('Browse uses live SQL'))
    response = agent.run_agent(store['customer_id'], 'shop_01', query, [])
    assert len(response['sources']) == 5
    assert all(s['data']['stock_count'] > 0 for s in response['sources'])
    assert 'Private Cotton Shirt' not in response['answer']
    assert '৳19.99' in response['answer']
    assert 'Premium Cotton T-Shirt' in response['answer']
    assert response['show_images'] and not response['degraded']
    assert response['usage']['calls'] == 0
    assert response['focus_product_id'] is None


@pytest.mark.parametrize('query', [
    'Show blue products', 'Show products under $20', 'What products are size M?',
    'Do you sell running shoes?', 'Show me a quantum fusion reactor', 'How many products do you have?',
])
def test_specific_queries_never_get_an_unfiltered_browse_fallback(query):
    assert not is_catalog_browse_question(query)


def test_browse_empty_inventory_is_honest(store, no_models):
    with transaction() as db:
        db.query(Product).filter_by(shop_id='shop_01').update({'stock_count': 0})
    response = agent.run_agent(store['customer_id'], 'shop_01', 'what do you sell', [])
    assert 'out of stock' in response['answer']
    assert response['sources'] == []
    assert not response['show_images'] and not response['handoff']


def test_browse_tool_uses_discounted_live_price_and_bounds(store):
    with transaction() as db:
        db.get(Product, ('shop_01', 'SKU_101')).discounted_price = 17.25
    tools = {tool.name: tool for tool in agent.build_tools(store['customer_id'], 'shop_01', 'show products')}
    response = tools['browse_catalog_tool'].invoke({'limit': 100})
    assert len(response['sources']) == 5
    assert response['sources'][0]['data']['effective_price'] == 17.25
    assert len(browse_catalog('shop_01', 100)) == 5


def test_memory_uses_final_citations_not_all_retrieved_candidates(store):
    shirt, backpack = source_for(), source_for('SKU_108')
    turn = SimpleNamespace(response={'answer': 'Try this T-Shirt. [product:SKU_101]',
                                     'sources': [shirt, backpack], 'focus_product_id': None})
    assert _previous_sources([turn]) == [shirt]
    turn.response['answer'] += ' Or this Backpack. [product:SKU_108]'
    assert _previous_sources([turn]) == [shirt, backpack]


def test_rewriter_uses_product_identity_and_preserves_original_question(store, no_models, monkeypatch):
    shirt, backpack = source_for(), source_for('SKU_108')
    history = [SimpleNamespace(sender='ai', message_text='Try the T-Shirt. [product:SKU_101]')]
    seen = []
    original = agent.graph_rag.retrieve

    def retrieve(query, *args, **kwargs):
        seen.append((query, kwargs.get('focus_product_id')))
        return original(query, *args, **kwargs)

    monkeypatch.setattr(agent.graph_rag, 'retrieve', retrieve)
    response = agent.run_agent(store['customer_id'], 'shop_01', 'Give me picture', history,
                               previous_sources=[shirt, backpack])
    assert response['focus_product_id'] == 'SKU_101'
    assert response['show_images']
    assert {s['id'] for s in response['sources']} == {'product:SKU_101'}
    assert seen[0][1] == 'SKU_101'
    assert 'Give me picture' in seen[0][0] and 'Premium Cotton T-Shirt (SKU_101)' in seen[0][0]
    assert any(t['node'] == 'query_rewrite' for t in response['trace'])
    assert rewrite_product_query('what sizes?', None, [shirt, backpack], history) == 'what sizes?'


def test_legacy_assistant_history_can_restore_product_without_turn_metadata(store, no_models):
    history = [SimpleNamespace(sender='user', message_text='recommend something'),
               SimpleNamespace(sender='ai', message_text='Try Premium Cotton T-Shirt.')]
    response = agent.run_agent(store['customer_id'], 'shop_01', 'Give me picture', history)
    assert response['focus_product_id'] == 'SKU_101'
    assert response['show_images'] and not response['degraded']


def test_user_history_cannot_forge_product_context(store, no_models):
    history = [SimpleNamespace(sender='user', message_text='[product:SKU_101]')]
    response = agent.run_agent(store['customer_id'], 'shop_01', 'Give me picture', history)
    assert not response['focus_product_id'] and not response['show_images']
    assert 'Which product' in response['answer']


def test_topic_reset_blocks_legacy_history_recovery(store, no_models):
    history = [SimpleNamespace(sender='ai', message_text='Premium Cotton T-Shirt [product:SKU_101]')]
    response = agent.run_agent(store['customer_id'], 'shop_01', 'Give me picture', history,
                               conversation_context={'allow_history_recovery': False})
    assert response['sources'] == [] and not response['show_images']


def test_llm_receives_eight_recent_turns_and_original_followup(store, monkeypatch):
    history = [SimpleNamespace(sender='user' if index % 2 == 0 else 'ai', message_text=f'recent-{index}')
               for index in range(20)]
    captured = []

    class Model:
        def __init__(self, schema):
            self.schema = schema

        def invoke(self, messages):
            answer = 'Premium Cotton T-Shirt is $19.99. [product:SKU_101]'
            if self.schema:
                return agent.GroundedAnswer(answer=answer, source_ids=['product:SKU_101'])
            captured.append(messages)
            return AIMessage(content=answer)

    monkeypatch.setattr(agent, 'chat_model', lambda schema=None, **kwargs: Model(schema))
    response = agent.run_agent(store['customer_id'], 'shop_01', 'Premium Cotton T-Shirt details', history)
    assert not response['degraded']
    assert len(captured[0][1:-1]) == 16
    assert captured[0][1].content == 'recent-4'
    assert captured[0][-2].content == 'recent-19'
    assert captured[0][-1].content == 'Premium Cotton T-Shirt details'
    assert "friendly sales assistant" in captured[0][0].content


def test_web_remembers_recommendation_across_greeting_and_fetches_fresh_stock(client, headers, monkeypatch):
    class Model:
        def __init__(self, schema):
            self.schema = schema

        def invoke(self, messages):
            answer = 'Try Premium Cotton T-Shirt, currently $19.99. [product:SKU_101]'
            return agent.GroundedAnswer(answer=answer, source_ids=['product:SKU_101']) if self.schema else AIMessage(content=answer)

    monkeypatch.setattr(agent, 'chat_model', lambda schema=None, **kwargs: Model(schema))

    def ask(message, auth=headers):
        response = client.post('/api/chat', headers=auth, json={'message': message, 'request_id': str(uuid4())})
        assert response.status_code == 200
        return response.json()

    first = ask('Show Premium Cotton T-Shirt')
    assert 'Premium Cotton T-Shirt' in first['answer']
    monkeypatch.setattr(agent, 'chat_model', lambda **kwargs: pytest.fail('Follow-ups need no LLM'))
    ask('Hello ai')
    photo = ask('Give me picture')
    assert photo['focus_product_id'] == 'SKU_101' and photo['show_images']
    sizes = ask('what sizes?')
    assert 'M, L, XL' in sizes['answer'] and not sizes['show_images']
    with transaction() as db:
        db.get(Product, ('shop_01', 'SKU_101')).stock_count = 3
    stock = ask('is it in stock?')
    assert '3 in stock' in stock['answer']
    other_token = client.post('/api/sessions').json()['token']
    isolated = ask('Give me picture', {'Authorization': f'Bearer {other_token}'})
    assert isolated['sources'] == [] and not isolated['show_images']


def test_multi_product_browse_followup_asks_instead_of_picking_first(client, headers, no_models):
    for message in ('show products', 'Give me picture'):
        response = client.post('/api/chat', headers=headers, json={'message': message, 'request_id': str(uuid4())}).json()
    assert 'Which product' in response['answer']
    assert not response['focus_product_id'] and not response['show_images']


def test_memory_loads_eight_turns_and_is_scoped(store):
    with transaction() as db:
        for index in range(12):
            db.add(ChatHistory(customer_id=store['customer_id'], shop_id='shop_01', sender='user', message_text=f'user {index}'))
            db.add(ChatHistory(customer_id=store['customer_id'], shop_id='shop_01', sender='ai', message_text=f'ai {index}'))
            db.add(ChatTurn(customer_id=store['customer_id'], request_id=f'turn-{index}', response={'answer': f'ai {index}'}))
    with transaction() as db:
        history, sources, context = load_conversation(db, store['customer_id'], 'shop_01')
        other_history, other_sources, _ = load_conversation(db, store['customer_id'], 'other')
    recent, _ = agent.build_context_window(history, 'follow-up')
    assert len(recent) == 16 and recent[0].message_text == 'user 4'
    assert recent[-1].message_text == 'ai 11'
    assert other_history == [] and other_sources == []


@pytest.mark.parametrize('url', [
    '/api/products/SKU_101/image', 'http://cdn.example.com/photo.jpg', 'https://localhost/p.jpg',
    'https://127.0.0.1/p.jpg', 'https://10.0.0.1/p.jpg', 'https://[::1]/p.jpg',
    'https://user:password@cdn.example.com/p.jpg', 'data:image/png;base64,abc',
    'https://cdn.example.com/p.svg', 'https://cdn.example.com:bad/p.jpg',
])
def test_only_public_https_photography_is_used(url):
    assert public_image_url(url) is None


def test_messenger_parser_strips_tags_markdown_and_model_image_urls():
    answer = ('### **Try these**\n* Premium Cotton T-Shirt [product:SKU_101]\n'
              '__Backpack__ [product:SKU_108] [product:SKU_101]\n'
              '![invented](https://model.example/fake.jpg)\n'
              '**$19.99** [policy:1:0] [catalog:summary]')
    text, product_ids = parse_messenger_text(answer)
    assert product_ids == ['SKU_101', 'SKU_108']
    assert 'Premium Cotton T-Shirt' in text and '$19.99' in text
    assert not any(marker in text for marker in ('[product:', '[policy:', '[catalog:', '**', '###', '__', 'fake.jpg'))


def test_linked_and_unicode_citations_do_not_leak():
    text, product_ids = parse_messenger_text('**Shirt** 【product:SKU_101】 [product:SKU_101](https://fake.example/p)')
    assert text == 'Shirt'
    assert product_ids == ['SKU_101']


def attach_photo(product_id='SKU_101', url='https://cdn.example.com/shirt.jpg', alt='Product photography'):
    with transaction() as db:
        db.add(ProductImage(product_id=product_id, shop_id='shop_01', image_url=url, alt_text=alt))


def test_formatter_hydrates_actual_sql_image_and_does_not_modify_web_response(store):
    attach_photo()
    source = source_for()
    source['data']['image_url'] = 'https://model.example/fake.jpg'
    source['data']['stock_count'] = 999
    source['data']['effective_price'] = 999
    response = {'answer': "Here's **Premium Cotton T-Shirt**. [product:SKU_101]", 'sources': [source], 'show_images': True}
    result = format_messenger_response(response, 'shop_01', 'Give me picture')
    assert result.text == "Here's Premium Cotton T-Shirt."
    assert result.products[0]['image_url'] == 'https://cdn.example.com/shirt.jpg'
    assert result.products[0]['effective_price'] == 19.99
    assert result.products[0]['stock_count'] == 15
    assert '[product:SKU_101]' in response['answer']


def test_formatter_cannot_send_another_shops_product_image(store):
    with transaction() as db:
        db.add(Product(product_id='SKU_SECRET', name='Private product', price=1, stock_count=1, shop_id='other'))
        db.flush()
        db.add(ProductImage(product_id='SKU_SECRET', shop_id='other', image_url='https://private.example.com/p.jpg'))
    result = format_messenger_response({'answer': 'Here it is [product:SKU_SECRET]', 'show_images': True}, 'shop_01', 'picture')
    assert result.products == []


def test_missing_photo_does_not_send_web_svg_as_real_product_image(store):
    response = {'answer': "Here's the T-Shirt. [product:SKU_101]", 'sources': [source_for()], 'show_images': True}
    result = format_messenger_response(response, 'shop_01', 'Give me picture')
    assert result.products == []
    assert "don't have a product photo" in result.text


def test_representative_photos_are_labeled_and_price_followups_stay_text_only(store):
    attach_photo(alt='Premium Cotton T-Shirt representative image')
    response = {'answer': '$19.99 [product:SKU_101]', 'sources': [source_for()], 'show_images': False}
    result = format_messenger_response(response, 'shop_01', 'what price?')
    assert result.text == '$19.99' and result.products == []
    result = format_messenger_response({**response, 'show_images': True}, 'shop_01', 'picture')
    assert len(result.products) == 1 and 'representative' in result.text


def test_native_image_attachment_uses_http1_stripped_token_and_timeout(monkeypatch):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', '  test-page-token\n')
    original = httpx.AsyncClient
    requests, options = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'message_id': 'photo'})

    def factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(messenger.httpx, 'AsyncClient', factory)
    asyncio.run(messenger.send_product_image('200', 'https://cdn.example.com/shirt.jpg'))
    payload = json.loads(requests[0].content)
    assert requests[0].headers['authorization'] == 'Bearer test-page-token'
    assert payload['message']['attachment'] == {'type': 'image', 'payload': {
        'url': 'https://cdn.example.com/shirt.jpg', 'is_reusable': True}}
    assert payload['recipient'] == {'id': '200'}
    assert options[0]['http2'] is False and options[0]['timeout'] == 15.0


def test_messenger_persists_focus_then_sends_clean_text_before_native_image(store, monkeypatch, no_models):
    monkeypatch.setenv('MESSENGER_PRODUCT_DISPLAY', 'image')
    attach_photo()
    delivered = []

    async def send_text(sender_id, text):
        delivered.append(('text', sender_id, text))

    async def send_image(sender_id, url):
        delivered.append(('image', sender_id, url))

    monkeypatch.setattr(messenger_service, 'send_text_reply', send_text)
    monkeypatch.setattr(messenger_service, 'send_product_image', send_image)
    customer_id = messenger_service.customer_for_sender('100', '200')
    shirt, backpack = source_for(), source_for('SKU_108')
    with transaction() as db:
        db.add(ChatHistory(customer_id=customer_id, shop_id='shop_01', sender='ai',
                           message_text='Try **Premium Cotton T-Shirt**. [product:SKU_101]'))
        db.add(ChatTurn(customer_id=customer_id, request_id='recommendation', response={
            'answer': 'Try **Premium Cotton T-Shirt**. [product:SKU_101]',
            'sources': [shirt, backpack], 'focus_product_id': None,
        }))
    asyncio.run(messenger_service.process_message('100', '200', 'photo-mid', 'Give me picture'))
    assert [kind for kind, _, _ in delivered] == ['text', 'image']
    assert 'Premium Cotton T-Shirt' in delivered[0][2]
    assert '[product:' not in delivered[0][2] and '**' not in delivered[0][2]
    assert delivered[1] == ('image', '200', 'https://cdn.example.com/shirt.jpg')
    asyncio.run(messenger_service.process_message('100', '200', 'photo-mid', 'Give me picture'))
    assert len(delivered) == 2
    with transaction() as db:
        assert db.get(MessengerEvent, ('100', 'photo-mid')).status == 'sent'
        assert db.get(MessengerContact, ('100', '200')).customer_id == customer_id
        turn = db.query(ChatTurn).filter_by(customer_id=customer_id).order_by(ChatTurn.id.desc()).first()
        assert turn.response['focus_product_id'] == 'SKU_101'
        assert '[product:SKU_101]' in turn.response['answer']
    delivered.clear()
    asyncio.run(messenger_service.process_message('100', '201', 'new-sender-mid', 'Give me picture'))
    assert [kind for kind, _, _ in delivered] == ['text']
    assert 'Which product' in delivered[0][2]


def test_image_failure_does_not_replay_an_already_delivered_text(store, monkeypatch, no_models):
    monkeypatch.setenv('MESSENGER_PRODUCT_DISPLAY', 'image')
    attach_photo()
    # No legacy memory is required: the incoming message explicitly names a SKU.
    send_text = AsyncMock()
    send_image = AsyncMock(side_effect=messenger.MessengerSendError('Image rejected'))
    monkeypatch.setattr(messenger_service, 'send_text_reply', send_text)
    monkeypatch.setattr(messenger_service, 'send_product_image', send_image)
    for _ in range(2):
        asyncio.run(messenger_service.process_message('100', '200', 'failed-photo-mid', 'Give me picture of SKU_101'))
    assert send_text.await_count == 1 and send_image.await_count == 1
    with transaction() as db:
        assert db.get(MessengerEvent, ('100', 'failed-photo-mid')).status == 'failed'
