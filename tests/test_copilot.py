"""Copilot upgrade: persona fallbacks, visual search, Messenger cards + order flow,
sentiment logging, escalation context/alerts, order tracking and handover."""
import asyncio
import json

import httpx
import pytest
from langchain_core.messages import AIMessage

from backend import agent, escalation, messenger, messenger_service, order_flow, sentiment, vision
from backend.catalog import suggest_alternatives
from backend.database import transaction
from backend.order_tracking import track
from backend.records import (ConversationState, MessageSentiment, Notification, OrderDetail, SupportCase)
from models import ChatHistory, Customer, Order, Product

REAL_DELIVER_ALERT = escalation.deliver_alert


@pytest.fixture(autouse=True)
def offline_alerts(monkeypatch):
    """Background outbox delivery must never reach Telegram/webhooks from tests."""
    delivered = []
    monkeypatch.setattr(escalation, 'deliver_alert', lambda recipient, body: delivered.append((recipient, body)))
    return delivered


@pytest.fixture
def no_models(monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('No model call expected')))


class Outbox:
    """Records everything messenger_service would send to Meta."""

    def __init__(self, monkeypatch):
        self.sent = []

        async def text(sender_id, body):
            self.sent.append(('text', body, None))

        async def quick(sender_id, body, replies):
            self.sent.append(('quick', body, replies))

        async def cards(sender_id, products):
            self.sent.append(('cards', [p['product_id'] for p in products], None))

        async def image(sender_id, url):
            self.sent.append(('image', url, None))

        async def handover(sender_id, target, metadata=''):
            self.sent.append(('handover', target, metadata))

        for name, fn in (('send_text_reply', text), ('send_quick_replies', quick), ('send_product_cards', cards),
                         ('send_product_image', image), ('pass_thread_control', handover)):
            monkeypatch.setattr(messenger_service, name, fn)

    @property
    def last(self):
        return self.sent[-1]

    def texts(self):
        return [body for kind, body, _ in self.sent if kind in ('text', 'quick')]


def deliver(mid, text='', postback=None, image_url=None, sender='200'):
    asyncio.run(messenger_service.process_message('100', sender, mid, text, image_url=image_url, postback=postback))


# ------------------------------------------------------------------ sentiment

@pytest.mark.parametrize('text,label', [
    ('Thanks, I love it!', 'POSITIVE'),
    ('Where is my order?', 'NEUTRAL'),
    ('What is your refund policy?', 'NEUTRAL'),
    ('Is it damage resistant?', 'NEUTRAL'),
    ('The delivery was late and the box was damaged', 'NEGATIVE'),
    ('This is useless, worst service ever!!!', 'FRUSTRATED'),
    ('খুব খারাপ সার্ভিস', 'NEGATIVE'),
    ('not bad at all', 'NEUTRAL'),
])
def test_sentiment_labels(text, label):
    assert sentiment.classify(text).label == label


def test_escalation_rules_need_strong_or_persistent_negativity():
    mild = sentiment.classify('The delivery was a bit late')
    assert mild.label == 'NEGATIVE' and not sentiment.needs_escalation(mild, 1)
    assert sentiment.needs_escalation(mild, 3)
    assert sentiment.needs_escalation(sentiment.classify('You people are scammers'))
    assert sentiment.classify('I need it urgently').urgency == 'high'


def test_chat_logs_sentiment_and_shifts_rules_reply_to_empathy(client, headers, store, no_models):
    response = client.post('/api/chat', headers=headers, json={'message': 'ugh, the order was late. where is my order?',
                                                               'request_id': '00000000-0000-0000-0000-00000000a001'})
    body = response.json()
    assert body['sentiment']['label'] == 'NEGATIVE'
    assert body['answer'].startswith('Sorry about that.')
    with transaction() as db:
        row = db.query(MessageSentiment).one()
        assert row.label == 'NEGATIVE' and row.chat_message_id is not None
        assert db.query(ConversationState).one().negative_streak == 1


# ------------------------------------------------------------------ escalation

def test_frustrated_customer_is_escalated_with_triage_context(client, headers, store, no_models, monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'bot-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '42')
    response = client.post('/api/chat', headers=headers, json={
        'message': 'This is ridiculous, still no reply about my jacket!!!', 'request_id': '00000000-0000-0000-0000-00000000a002'}).json()
    assert response['route'] == 'human' and response['handoff']
    assert 'support specialists' in response['answer'] and 'join this chat shortly' in response['answer']
    with transaction() as db:
        detail = db.get(SupportCase, response['handoff']['case_id'])
        context = detail.context['escalation']
        assert detail.priority == 'urgent' and detail.category == 'complaint'
        assert context['sentiment']['label'] == 'FRUSTRATED'
        assert context['last_message'].startswith('This is ridiculous')
        assert db.query(ConversationState).one().status == 'ESCALATED'
        alert = db.query(Notification).filter_by(recipient='alert:telegram').one()
        assert json.loads(alert.body)['sentiment'] == 'FRUSTRATED'
    paused = client.post('/api/chat', headers=headers, json={'message': 'hello?', 'request_id': '00000000-0000-0000-0000-00000000a003'}).json()
    assert paused['status'] == 'queued_for_human' and paused['answer'] == ''


@pytest.mark.parametrize('message,reason', [
    ('I want a bulk order of 100 pieces of the cotton t-shirt', 'complex_request'),
    ('Can you print our company logo on 30 shirts?', 'complex_request'),
    ('My parcel arrived damaged', 'after_sales_request'),
    ('I was double charged for my order', 'authority_exceeded'),
    ('Can I talk to a human?', 'customer_request'),
])
def test_complex_and_out_of_scope_requests_trigger_handoff(message, reason):
    assert agent.handoff_trigger(message) == reason


def test_regular_quantities_are_not_bulk_orders():
    assert agent.handoff_trigger('I need 3 pcs') is None
    assert agent.handoff_trigger('shirts under 100 dollars') is None


def test_admin_alert_feed_and_sentiment_overview(client, headers, admin_headers, store, no_models):
    client.post('/api/chat', headers=headers, json={'message': 'I want to talk to a human please',
                                                    'request_id': '00000000-0000-0000-0000-00000000a004'})
    alerts = client.get('/api/admin/escalations', headers=admin_headers).json()
    assert alerts[0]['trigger'] == 'Asked for a human' and alerts[0]['last_message'] == 'I want to talk to a human please'
    overview = client.get('/api/admin/sentiment', headers=admin_headers).json()
    assert sum(overview['counts'].values()) == 1 and overview['escalated_conversations'] == 1
    dashboard = client.get('/api/admin/dashboard', headers=admin_headers).json()
    assert dashboard['escalation_alerts'] and dashboard['sentiment']['escalated_conversations'] == 1


def test_alert_delivery_uses_outbox_and_never_logs_secrets(store, monkeypatch, caplog):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'secret-bot-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '42')
    requests = []
    original = httpx.Client

    def handler(request):
        requests.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(escalation.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    payload = {'type': 'escalation', 'case_id': 7, 'priority': 'urgent', 'customer_id': 1, 'trigger': 'x',
               'sentiment': 'NEGATIVE', 'sentiment_score': -0.5, 'last_message': 'help'}
    with pytest.raises(RuntimeError, match='HTTP 500'):
        REAL_DELIVER_ALERT('alert:telegram', json.dumps(payload))
    assert json.loads(requests[0].content)['chat_id'] == '42'
    assert 'Escalation #7' in json.loads(requests[0].content)['text']
    assert 'secret-bot-token' not in caplog.text


# ------------------------------------------------------------------ visual search

class VisionModel:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, messages):
        assert messages[1].content[1]['image_url']['url'] == 'data:image/png;base64,AAAA'
        return AIMessage(content=json.dumps(self.payload))


def vision_only(monkeypatch, payload):
    def factory(vision=False, **kwargs):
        if not vision:
            raise AssertionError('Visual replies must be deterministic')
        return VisionModel(payload)
    monkeypatch.setattr(agent, 'chat_model', factory)


def test_photo_is_matched_to_live_catalogue_with_human_reply(store, monkeypatch):
    vision_only(monkeypatch, {'is_product': True, 'category': 'T-shirt', 'color': 'navy blue', 'pattern': 'solid',
                              'material': 'cotton', 'style': 'crew-neck', 'details': ['short sleeves'],
                              'summary': 'navy crew-neck tee'})
    result = agent.run_agent(store['customer_id'], 'shop_01', 'find this', [], image='data:image/png;base64,AAAA')
    assert result['answer'].startswith("I see you're looking for a navy blue crew-neck cotton t-shirt!")
    assert 'Premium Cotton T-Shirt' in result['answer'] and '$19.99' in result['answer']
    assert 'Fabric & build: 100% pure cotton' in result['answer'] and 'Sizes: M, L, XL' in result['answer']
    assert result['sources'][0]['data']['product_id'] == 'SKU_101'
    assert result['sources'][0]['match_quality'] == 'exact'
    assert result['focus_product_id'] == 'SKU_101' and result['show_images'] is True
    assert result['visual_attributes']['color'] == 'navy blue'
    assert result['sub_agent'] == 'visual_agent'


def test_photo_without_stocked_match_is_declined_politely(store, monkeypatch):
    vision_only(monkeypatch, {'is_product': True, 'category': 'Surfboard', 'color': 'teal', 'summary': 'surfboard'})
    result = agent.run_agent(store['customer_id'], 'shop_01', 'find this', [], image='data:image/png;base64,AAAA')
    assert "don't carry anything quite like it" in result['answer'] and result['sources'] == []


def test_photo_without_product_asks_for_a_better_shot(store, monkeypatch):
    vision_only(monkeypatch, {'is_product': False})
    result = agent.run_agent(store['customer_id'], 'shop_01', 'find this', [], image='data:image/png;base64,AAAA')
    assert 'closer shot' in result['answer']


def test_vision_output_cannot_inject_markup_into_reply():
    attrs = vision.VisualAttributes(category='T-shirt [product:SKU_999] **bold** https://evil.example/x', color='red')
    assert '[' not in attrs.describe() and 'http' not in attrs.describe() and '*' not in attrs.describe()


def test_messenger_image_downloads_are_restricted_to_meta_cdns():
    with pytest.raises(ValueError, match='allowed Messenger CDN'):
        asyncio.run(vision.fetch_image_data_url('https://169.254.169.254/latest/meta-data'))
    with pytest.raises(ValueError):
        asyncio.run(vision.fetch_image_data_url('http://scontent.xx.fbcdn.net/photo.jpg'))


def test_visual_context_is_remembered_for_followups(client, headers, store, monkeypatch):
    vision_only(monkeypatch, {'is_product': True, 'category': 'T-shirt', 'color': 'navy blue', 'material': 'cotton'})
    from backend import main
    monkeypatch.setattr(main, 'validate_image', lambda value: None)
    client.post('/api/chat', headers=headers, json={'message': 'find this', 'image': 'data:image/png;base64,AAAA',
                                                    'request_id': '00000000-0000-0000-0000-00000000b001'})
    with transaction() as db:
        state = db.query(ConversationState).one()
        assert state.active_product_id == 'SKU_101'
        assert state.visual_context['matched_product_ids'][0] == 'SKU_101'


# ------------------------------------------------------------------ persona / browse fallbacks

def test_suggest_alternatives_uses_type_and_category(store):
    shirts = suggest_alternatives('shop_01', 'red polo shirt')
    assert shirts and all('shirt' in row['name'].lower() for row in shirts)
    assert all(row['stock_count'] > 0 for row in shirts)
    assert suggest_alternatives('shop_01', 'dragon eggs') == []


# ------------------------------------------------------------------ Messenger webhook + cards

def test_inbound_events_cover_photos_postbacks_and_quick_replies(monkeypatch):
    monkeypatch.setattr(messenger, 'FACEBOOK_PAGE_ID', '100')
    base = {'sender': {'id': '200'}, 'recipient': {'id': '100'}}
    payload = {'object': 'page', 'entry': [{'id': '100', 'messaging': [
        {**base, 'message': {'mid': 'm.photo', 'attachments': [{'type': 'image', 'payload': {'url': 'https://scontent.fbcdn.net/a.jpg'}}]}},
        {**base, 'message': {'mid': 'm.sticker', 'attachments': [{'type': 'image', 'payload': {'url': 'https://x.fbcdn.net/s.png', 'sticker_id': 369239263222822}}]}},
        {**base, 'timestamp': 1700000000, 'postback': {'title': 'Order Now', 'payload': 'ORDER_NOW:SKU_101', 'mid': 'm.postback'}},
        {**base, 'message': {'mid': 'm.qr', 'text': 'M', 'quick_reply': {'payload': 'SIZE:M'}}},
        {**base, 'message': {'mid': 'm.text', 'text': 'Hello'}},
    ]}]}
    events = list(messenger.inbound_events(payload))
    assert events == [
        {'page_id': '100', 'sender_id': '200', 'message_id': 'm.photo', 'text': '', 'image_url': 'https://scontent.fbcdn.net/a.jpg'},
        {'page_id': '100', 'sender_id': '200', 'message_id': 'm.postback', 'text': 'Order Now', 'postback': 'ORDER_NOW:SKU_101'},
        {'page_id': '100', 'sender_id': '200', 'message_id': 'm.qr', 'text': 'M', 'postback': 'SIZE:M'},
        {'page_id': '100', 'sender_id': '200', 'message_id': 'm.text', 'text': 'Hello'},
    ]


def test_generic_template_card_payload(monkeypatch):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', 'test-page-token')
    original = httpx.AsyncClient
    requests = []
    monkeypatch.setattr(messenger.httpx, 'AsyncClient', lambda **kw: original(
        transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200, json={})), **kw))
    product = {'product_id': 'SKU_101', 'name': 'Premium Cotton T-Shirt', 'effective_price': 19.99, 'stock_count': 15,
               'sizes': ['M', 'L', 'XL'], 'image_url': 'https://cdn.example.com/shirt.jpg'}
    sold_out = {**product, 'product_id': 'SKU_102', 'stock_count': 0, 'image_url': None}
    asyncio.run(messenger.send_product_cards('200', [product, sold_out]))
    template = json.loads(requests[0].content)['message']['attachment']['payload']
    first, second = template['elements']
    assert template['template_type'] == 'generic'
    assert first['image_url'] == 'https://cdn.example.com/shirt.jpg'
    assert first['subtitle'] == '$19.99 · 15 in stock · Sizes M/L/XL'
    assert [b['payload'] for b in first['buttons']] == ['ORDER_NOW:SKU_101', 'DETAILS:SKU_101']
    assert 'image_url' not in second and [b['title'] for b in second['buttons']] == ['More Details']
    assert 'Sold out' in second['subtitle']


def test_messenger_sends_clean_text_then_cards(store, monkeypatch, no_models):
    outbox = Outbox(monkeypatch)
    deliver('cards-mid', 'Give me picture of SKU_101')
    kinds = [kind for kind, _, _ in outbox.sent]
    assert kinds == ['text', 'cards'] and outbox.sent[1][1] == ['SKU_101']
    assert '[product:' not in outbox.sent[0][1] and '**' not in outbox.sent[0][1]


def test_more_details_postback_asks_agent_about_that_product(store, monkeypatch):
    seen = []

    def fake(customer_id, shop_id, query, *args):
        seen.append(query)
        return {'answer': 'It is 100% cotton.', 'sources': [], 'drafts': [], 'trace': [], 'route': 'shopping',
                'latency_ms': 1, 'usage': {}, 'degraded': False}

    monkeypatch.setattr(agent, 'run_agent', fake)
    Outbox(monkeypatch)
    deliver('details-mid', 'More Details', postback='DETAILS:SKU_101')
    assert seen == ['Tell me more about the Premium Cotton T-Shirt']


# ------------------------------------------------------------------ Messenger order flow

def stock(product_id='SKU_101'):
    with transaction() as db:
        return db.get(Product, product_id).stock_count


def test_full_messenger_order_flow_creates_order_and_decrements_stock(store, monkeypatch, no_models, offline_alerts):
    monkeypatch.setenv('ADMIN_ALERT_WEBHOOK_URL', 'https://hooks.example.com/omnicart')
    outbox = Outbox(monkeypatch)
    before = stock()
    deliver('o1', 'Order Now', postback='ORDER_NOW:SKU_101')
    kind, body, replies = outbox.last
    assert kind == 'quick' and 'Which size' in body and [r['payload'] for r in replies] == ['SIZE:M', 'SIZE:L', 'SIZE:XL']
    deliver('o2', 'M', postback='SIZE:M')
    assert 'which color' in outbox.last[1].lower()
    deliver('o3', 'black please')
    assert 'How many' in outbox.last[1]
    deliver('o4', '2')
    assert 'full name' in outbox.last[1]
    deliver('o5', 'my name is Amina Rahman')
    assert 'phone number' in outbox.last[1] and outbox.last[2] == [{'content_type': 'user_phone_number'}]
    deliver('o6', '0171')
    assert "doesn't look quite right" in outbox.last[1]
    deliver('o7', '01712345678', postback='01712345678')
    assert 'delivery address' in outbox.last[1]
    deliver('o8', 'House 12, Road 5, Dhanmondi, Dhaka')
    summary = outbox.last[1]
    assert 'Premium Cotton T-Shirt (M, Black)' in summary and 'Qty: 2 × $19.99 = $39.98' in summary
    assert 'Amina Rahman' in summary and '01712345678' in summary and 'Dhanmondi' in summary
    assert [r['payload'] for r in outbox.last[2]] == ['ORDER_CONFIRM', 'ORDER_EDIT', 'ORDER_CANCEL']
    assert stock() == before  # nothing is reserved before confirmation
    deliver('o9', '✅ Confirm Order', postback='ORDER_CONFIRM')
    receipt = outbox.last[1]
    assert 'Your order is confirmed! Order ID: ORD-1001' in receipt and 'Total: $39.98' in receipt
    assert stock() == before - 2
    with transaction() as db:
        order = db.query(Order).one()
        detail = db.get(OrderDetail, order.order_id)
        assert detail.order_code == 'ORD-1001' and detail.variant == {'size': 'M', 'color': 'Black'}
        assert detail.phone == '01712345678' and detail.channel == 'messenger'
        customer = db.get(Customer, order.customer_id)
        assert customer.name == 'Amina Rahman' and customer.address.startswith('House 12')
        assert db.get(ConversationState, order.customer_id).order_state == {}
        alert = db.query(Notification).filter_by(recipient='alert:webhook').one()
        assert json.loads(alert.body)['order_code'] == 'ORD-1001'
        assert alert.status == 'sent'  # delivered by the outbox after the reply (stubbed transport)
        # Every step is part of the shared conversation memory.
        assert db.query(ChatHistory).filter_by(customer_id=order.customer_id, sender='user').count() == 9
    deliver('o10', 'track ORD-1001')
    assert 'ORD-1001: 2 × Premium Cotton T-Shirt (M, Black)' in outbox.last[1]


def test_text_purchase_intent_uses_focused_product_and_saved_profile(store, monkeypatch, no_models):
    outbox = Outbox(monkeypatch)
    customer_id = messenger_service.customer_for_sender('100', '200')
    from backend.records import ChatTurn
    with transaction() as db:
        customer = db.get(Customer, customer_id)
        customer.name, customer.phone_number, customer.address = 'Amina Rahman', '01712345678', '12 Garden Road, Dhaka'
        source = {'id': 'product:SKU_104', 'kind': 'product', 'title': 'Wireless Earbuds',
                  'data': {'product_id': 'SKU_104', 'name': 'Wireless Earbuds'}}
        db.add(ChatTurn(customer_id=customer_id, request_id='earlier', response={
            'answer': 'Try the Wireless Earbuds [product:SKU_104]', 'sources': [source], 'focus_product_id': 'SKU_104'}))
    deliver('t1', 'I want to order this, 1 pcs')
    summary = outbox.last[1]
    # Single size/colour and the stated quantity are filled in; saved details are reused.
    assert "Here's your order summary" in summary and 'Wireless Earbuds (Regular, White)' in summary
    assert 'Qty: 1 × $59.99' in summary and '12 Garden Road' in summary


def test_order_flow_cancel_and_question_passthrough(store, monkeypatch):
    monkeypatch.setattr(agent, 'run_agent', lambda *args: {
        'answer': 'Returns are accepted within 14 days.', 'sources': [], 'drafts': [], 'trace': [],
        'route': 'support', 'latency_ms': 1, 'usage': {}, 'degraded': False})
    outbox = Outbox(monkeypatch)
    deliver('c1', 'Order Now', postback='ORDER_NOW:SKU_104')
    assert 'How many' in outbox.last[1]
    deliver('c2', 'What is your return policy?')
    assert outbox.last[1].startswith('Returns are accepted within 14 days.')
    assert 'still on your order' in outbox.last[1]
    deliver('c3', 'cancel')
    assert 'cancelled that order' in outbox.last[1]
    customer_id = messenger_service.customer_for_sender('100', '200')
    with transaction() as db:
        assert db.get(ConversationState, customer_id).order_state == {}
        assert db.query(Order).count() == 0


def test_quantity_above_stock_offers_available_amount(store, monkeypatch, no_models):
    outbox = Outbox(monkeypatch)
    deliver('q1', 'Order Now', postback='ORDER_NOW:SKU_103')  # Leather Wallet: 2 in stock
    deliver('q2', '5')
    assert 'only have 2 left' in outbox.last[1]


def test_sold_out_product_cannot_start_an_order(store, monkeypatch, no_models):
    outbox = Outbox(monkeypatch)
    deliver('s1', 'Order Now', postback='ORDER_NOW:SKU_102')
    assert 'sold out' in outbox.last[1]


def test_escalation_bypasses_open_order_form_and_hands_over(store, monkeypatch, no_models):
    monkeypatch.setenv('META_HANDOVER_TARGET_APP_ID', '263902037430900')
    outbox = Outbox(monkeypatch)
    deliver('e1', 'Order Now', postback='ORDER_NOW:SKU_104')
    deliver('e2', 'This is useless!!! I want to talk to a human')
    texts = outbox.texts()
    assert 'support specialists' in texts[-1]
    assert outbox.last == ('handover', '263902037430900', outbox.last[2]) and outbox.last[2].startswith('case:')
    deliver('e3', '1')  # paused: neither the bot nor the order form replies
    assert outbox.last[0] == 'handover'


def test_expired_quick_reply_does_not_reach_the_agent(store, monkeypatch, no_models):
    outbox = Outbox(monkeypatch)
    deliver('x1', 'M', postback='SIZE:M')
    assert 'expired' in outbox.last[1]


# ------------------------------------------------------------------ order tracking privacy

def place_order(customer_id, phone='01712345678'):
    from backend.commerce import confirm_order, prepare_order
    draft = prepare_order(customer_id, 'shop_01', 'SKU_101', 1)
    receipt = confirm_order(customer_id, 'shop_01', draft['draft_id'], phone=phone, address='12 Garden Road, Dhaka')
    with transaction() as db:
        db.add(OrderDetail(order_id=receipt['order_id'], shop_id='shop_01', order_code=f"ORD-{1000 + receipt['order_id']}",
                           channel='web', variant={}, customer_name='Amina', phone=phone, delivery_address='12 Garden Road, Dhaka'))
    return receipt


def test_order_tracking_never_leaks_by_phone_alone(store):
    place_order(store['customer_id'])
    with transaction() as db:
        stranger = Customer(unique_id='stranger', shop_id='shop_01')
        db.add(stranger)
        db.flush()
        stranger_id = stranger.customer_id
    assert 'ORD-1001' in track(store['customer_id'], 'shop_01', 'Where is my order?')
    assert 'phone number' in track(stranger_id, 'shop_01', 'track ORD-1001')
    assert 'ORD-1001' in track(stranger_id, 'shop_01', 'track ORD-1001, my phone is 01712345678')
    leaked = track(stranger_id, 'shop_01', 'order status for 01712345678')
    assert 'Premium Cotton T-Shirt' not in leaked and "don't see any orders" in leaked
    assert 'ORD-1001' in track(store['customer_id'], 'shop_01', 'order status for 01712345678')


def test_order_codes_are_parsed_in_both_formats():
    from backend.commerce import order_code, parse_order_code
    assert order_code(1) == 'ORD-1001'
    assert parse_order_code('status of ORD-1001?') == 1 and parse_order_code('ORD_7') == 7
    assert parse_order_code('ORD-0999') is None
