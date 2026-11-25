import asyncio
import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import agent, messenger, messenger_service, messenger_setup
from backend.database import transaction
from backend.records import ChatTurn, MessengerContact, MessengerEvent
from models import ChatHistory, Customer


@pytest.fixture
def webhook(monkeypatch):
    monkeypatch.setattr(messenger, 'VERIFY_TOKEN', 'test-verify')
    monkeypatch.setattr(messenger, 'APP_SECRET', 'test-app-secret')
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', 'test-page-token')
    monkeypatch.setattr(messenger, 'FACEBOOK_PAGE_ID', '100')
    app = FastAPI()
    app.include_router(messenger.router)
    return TestClient(app)


def event(mid='mid.1', text='Hello', **message_fields):
    return {'object': 'page', 'entry': [{'id': '100', 'messaging': [{
        'sender': {'id': '200'}, 'recipient': {'id': '100'},
        'message': {'mid': mid, 'text': text, **message_fields},
    }]}]}


def signed_post(client, payload):
    body = json.dumps(payload, ensure_ascii=False).encode()
    signature = hmac.new(b'test-app-secret', body, hashlib.sha256).hexdigest()
    return client.post('/webhook', content=body,
                       headers={'X-Hub-Signature-256': f'sha256={signature}', 'Content-Type': 'application/json'})


def test_healthz_is_lightweight(webhook):
    assert webhook.get('/healthz').json() == {'status': 'active'}


def test_verification_returns_exact_plain_text(webhook, monkeypatch):
    monkeypatch.setattr(messenger, 'APP_SECRET', '')
    response = webhook.get('/webhook', params={
        'hub.mode': 'subscribe', 'hub.verify_token': 'test-verify', 'hub.challenge': '123456',
    })
    assert response.status_code == 200
    assert response.text == '123456'
    assert response.headers['content-type'].startswith('text/plain')


@pytest.mark.parametrize('params', [
    {}, {'hub.mode': 'subscribe', 'hub.verify_token': 'wrong', 'hub.challenge': '123'},
    {'hub.mode': 'unsubscribe', 'hub.verify_token': 'test-verify', 'hub.challenge': '123'},
    {'hub.mode': 'subscribe', 'hub.verify_token': 'test-verify'},
])
def test_verification_rejects_bad_parameters(webhook, params):
    assert webhook.get('/webhook', params=params).status_code == 403


def test_text_messages_are_scheduled_in_background(webhook, monkeypatch):
    process = AsyncMock()
    monkeypatch.setattr(messenger_service, 'process_message', process)
    response = signed_post(webhook, event(text='বাংলা'))
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    process.assert_awaited_once_with(page_id='100', sender_id='200', message_id='mid.1', text='বাংলা')


@pytest.mark.parametrize('payload', [
    {}, [], None, {'object': 'instagram'}, {'object': 'page', 'entry': None},
    {'object': 'page', 'entry': [None, {}, {'id': '100', 'messaging': [None, {}, {'delivery': {}}, {'read': {}}]}]},
    event(is_echo=True), event(text=''), event(text=None), event(text='x' * 4001),
    {'object': 'page', 'entry': [{'id': '100', 'messaging': [{'postback': {'payload': 'TEST'}}]}]},
    {'object': 'page', 'entry': [{'id': '100', 'messaging': [{'sender': {'id': '200'}, 'message': {'attachments': []}}]}]},
])
def test_unknown_echo_and_receipt_events_are_acknowledged(webhook, monkeypatch, payload):
    process = AsyncMock()
    monkeypatch.setattr(messenger_service, 'process_message', process)
    response = signed_post(webhook, payload)
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    process.assert_not_awaited()


def test_foreign_page_and_bad_sender_are_ignored(webhook, monkeypatch):
    process = AsyncMock()
    monkeypatch.setattr(messenger_service, 'process_message', process)
    for page_id, sender_id in [('999', '200'), ('100', 'not-a-psid'), ('100', '100')]:
        payload = event()
        payload['entry'][0]['id'] = page_id
        payload['entry'][0]['messaging'][0]['sender']['id'] = sender_id
        assert signed_post(webhook, payload).status_code == 200
    process.assert_not_awaited()


def test_signature_is_required_and_covers_exact_body(webhook):
    assert webhook.post('/webhook', json=event()).status_code == 403
    assert webhook.post('/webhook', json=event(), headers={'X-Hub-Signature-256': 'sha256=bad'}).status_code == 403
    body = b'{"object":"page"}'
    signature = hmac.new(b'test-app-secret', body, hashlib.sha256).hexdigest()
    assert webhook.post('/webhook', content=body + b' ', headers={'X-Hub-Signature-256': f'sha256={signature}'}).status_code == 403


def test_missing_app_secret_fails_closed(webhook, monkeypatch):
    monkeypatch.setattr(messenger, 'APP_SECRET', '')
    assert signed_post(webhook, event()).status_code == 503


def test_malformed_json_and_body_limit(webhook):
    body = b'{not json'
    signature = hmac.new(b'test-app-secret', body, hashlib.sha256).hexdigest()
    assert webhook.post('/webhook', content=body, headers={'X-Hub-Signature-256': f'sha256={signature}'}).status_code == 400
    assert webhook.post('/webhook', content=b'x' * (messenger.MAX_BODY_BYTES + 1)).status_code == 413


def test_ack_body_is_sent_before_background_processing(webhook, monkeypatch):
    order = []

    async def process(**kwargs):
        order.append('slow-work')

    monkeypatch.setattr(messenger_service, 'process_message', process)
    body = json.dumps(event()).encode()
    signature = hmac.new(b'test-app-secret', body, hashlib.sha256).hexdigest()

    async def run():
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': 'POST', 'scheme': 'https', 'path': '/webhook', 'raw_path': b'/webhook',
                 'query_string': b'', 'root_path': '', 'server': ('test', 443), 'client': ('test', 1),
                 'headers': [(b'x-hub-signature-256', f'sha256={signature}'.encode())]}

        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}

        async def send(message):
            if message['type'] == 'http.response.start':
                assert message['status'] == 200
            if message['type'] == 'http.response.body' and not message.get('more_body'):
                assert json.loads(message['body']) == {'status': 'ok'}
                order.append('ack-sent')

        await webhook.app(scope, receive, send)

    asyncio.run(run())
    assert order == ['ack-sent', 'slow-work']


def mock_async_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(messenger.httpx, 'AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_send_api_uses_bearer_and_splits_long_text(webhook, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'message_id': 'outbound'})

    mock_async_http(monkeypatch, handler)
    asyncio.run(messenger.send_text_reply('200', 'x' * 2001))
    assert len(requests) == 2
    for request in requests:
        assert str(request.url) == 'https://graph.facebook.com/v21.0/me/messages'
        assert request.headers['authorization'] == 'Bearer test-page-token'
        payload = json.loads(request.content)
        assert payload['recipient'] == {'id': '200'}
        assert payload['messaging_type'] == 'RESPONSE'
        assert len(payload['message']['text']) <= 2000
    assert ''.join(json.loads(r.content)['message']['text'] for r in requests) == 'x' * 2001


@pytest.mark.parametrize('token', [
    '  test-page-token  ', '\r\ntest-page-token\r\n', '\ttest-page-token\t',
])
def test_send_trims_token_padding_and_explicitly_uses_http1(webhook, monkeypatch, token):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', token)
    original = httpx.AsyncClient
    requests, options = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'message_id': 'outbound'})

    def client_factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(messenger.httpx, 'AsyncClient', client_factory)
    asyncio.run(messenger.send_text_reply('200', 'Hello'))
    assert len(requests) == 1
    assert requests[0].headers['authorization'] == 'Bearer test-page-token'
    assert options[0]['http2'] is False


@pytest.mark.parametrize('token', [
    'private-token\nrest', 'private-token\r\nrest', 'private-token\trest',
    'private-token rest', 'Bearer private-token', '"private-token"', "'private-token'",
    'private-token\x00', 'private-token\x7f', 'private-token\u200b',
])
def test_invalid_tokens_fail_before_http_without_logging_secrets(webhook, monkeypatch, caplog, token):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', token)
    monkeypatch.setattr(messenger.httpx, 'AsyncClient',
                        lambda **kwargs: pytest.fail('Invalid token must not create an HTTP client'))
    with pytest.raises(messenger.MessengerConfigurationError, match='PAGE_ACCESS_TOKEN'):
        asyncio.run(messenger.send_text_reply('200', 'Hello'))
    assert 'Messenger configuration error' in caplog.text
    assert 'private-token' not in caplog.text


def test_local_protocol_error_has_safe_actionable_diagnostic(webhook, monkeypatch, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.LocalProtocolError("Illegal header value b'Bearer private-token'", request=request)

    mock_async_http(monkeypatch, handler)
    with pytest.raises(messenger.MessengerSendError, match='Local HTTP protocol error') as error:
        asyncio.run(messenger.send_text_reply('200', 'Hello'))
    assert len(requests) == 1
    assert 'private-token' not in str(error.value)
    assert 'private-token' not in caplog.text
    assert 'LocalProtocolError' in caplog.text
    assert 'proxy configuration' in caplog.text


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500])
def test_send_errors_are_redacted_and_not_retried(webhook, monkeypatch, caplog, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={'error': {'message': 'sensitive-token-and-user-text'}})

    mock_async_http(monkeypatch, handler)
    with pytest.raises(messenger.MessengerSendError):
        asyncio.run(messenger.send_text_reply('200', 'Hello'))
    assert len(requests) == 1
    assert 'sensitive-token-and-user-text' not in caplog.text
    assert 'test-page-token' not in caplog.text


def test_send_timeout_is_safe(webhook, monkeypatch, caplog):
    def handler(request):
        raise httpx.ReadTimeout('sensitive error', request=request)

    mock_async_http(monkeypatch, handler)
    with pytest.raises(messenger.MessengerSendError, match='uncertain'):
        asyncio.run(messenger.send_text_reply('200', 'Hello'))
    assert 'sensitive error' not in caplog.text


@pytest.mark.parametrize('token', ['', '  ', '\r\n\t'])
def test_send_without_page_token_is_rejected(webhook, monkeypatch, token):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', token)
    with pytest.raises(messenger.MessengerSendError, match='not configured'):
        asyncio.run(messenger.send_text_reply('200', 'Hello'))


def fake_agent(*args):
    return {'answer': 'Hello from the shop', 'sources': [], 'drafts': [], 'trace': [],
            'route': 'shopping', 'latency_ms': 1, 'usage': {}, 'degraded': False}


def test_messages_reuse_chat_database_and_are_deduplicated(store, monkeypatch):
    monkeypatch.setattr(agent, 'run_agent', fake_agent)
    send = AsyncMock()
    monkeypatch.setattr(messenger_service, 'send_text_reply', send)
    kwargs = dict(page_id='100', sender_id='200', message_id='mid.1', text='Hello')
    asyncio.run(messenger_service.process_message(**kwargs))
    asyncio.run(messenger_service.process_message(**kwargs))
    send.assert_awaited_once_with('200', 'Hello from the shop')
    with transaction() as db:
        contact = db.get(MessengerContact, ('100', '200'))
        assert db.get(Customer, contact.customer_id).unique_id == 'messenger:100:200'
        assert db.query(ChatHistory).filter_by(customer_id=contact.customer_id).count() == 2
        assert db.query(ChatTurn).filter_by(customer_id=contact.customer_id).count() == 1
        assert db.get(MessengerEvent, ('100', 'mid.1')).status == 'sent'


def test_messenger_respects_human_handoff(store, monkeypatch):
    customer_id = messenger_service.customer_for_sender('100', '200')
    with transaction() as db:
        db.get(Customer, customer_id).human_intervention = True
    send = AsyncMock()
    monkeypatch.setattr(messenger_service, 'send_text_reply', send)
    monkeypatch.setattr(agent, 'run_agent', lambda *args: pytest.fail('Paused customer must not call an LLM'))
    asyncio.run(messenger_service.process_message('100', '200', 'mid.human', 'Help'))
    send.assert_not_awaited()
    with transaction() as db:
        assert db.get(MessengerEvent, ('100', 'mid.human')).status == 'human_handoff'
        assert db.query(ChatHistory).filter_by(customer_id=customer_id, sender='user').count() == 1


def test_background_failure_is_persisted_and_not_resent(store, monkeypatch):
    monkeypatch.setattr(agent, 'run_agent', fake_agent)
    send = AsyncMock(side_effect=messenger.MessengerSendError('safe error'))
    monkeypatch.setattr(messenger_service, 'send_text_reply', send)
    for _ in range(2):
        asyncio.run(messenger_service.process_message('100', '200', 'mid.failed', 'Hello'))
    assert send.await_count == 1
    with transaction() as db:
        record = db.get(MessengerEvent, ('100', 'mid.failed'))
        assert record.status == 'failed'
        assert record.error == 'MessengerSendError'


def test_subscription_check_is_read_only_unless_explicit(monkeypatch):
    monkeypatch.setattr(messenger_setup, 'FACEBOOK_PAGE_ID', '100')
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/me'):
            return httpx.Response(200, json={'id': '100', 'name': 'Test Page'})
        if request.method == 'POST':
            assert b'messages%2Cmessaging_postbacks' in request.content
            return httpx.Response(200, json={'success': True})
        return httpx.Response(200, json={'data': [{'id': '300', 'subscribed_fields': ['messages']}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        messenger_setup.check_page(client)
        assert all(r.method == 'GET' for r in requests)
        requests.clear()
        messenger_setup.check_page(client, subscribe=True)
        assert [r.method for r in requests] == ['GET', 'POST', 'GET']


def test_subscription_errors_do_not_expose_meta_response():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={'error': {'code': 190, 'message': 'secret'}}),
    )) as client:
        with pytest.raises(messenger_setup.SetupError) as error:
            messenger_setup.graph_request(client, 'GET', 'me')
    assert '190' in str(error.value)
    assert 'secret' not in str(error.value)


def test_setup_uses_shared_token_cleanup_and_http1(webhook, monkeypatch):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', '\r\n test-page-token \r\n')
    monkeypatch.setattr(messenger_setup, 'APP_SECRET', 'test-app-secret')
    monkeypatch.setattr('sys.argv', ['messenger_setup'])
    original = httpx.Client
    options = []

    def client_factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(lambda request: pytest.fail('No network expected')), **kwargs)

    def check_page(client, subscribe=False):
        assert client.headers['authorization'] == 'Bearer test-page-token'
        assert subscribe is False

    monkeypatch.setattr(messenger_setup.httpx, 'Client', client_factory)
    monkeypatch.setattr(messenger_setup, 'check_page', check_page)
    messenger_setup.main()
    assert options[0]['http2'] is False


def test_setup_rejects_bad_token_before_creating_client(webhook, monkeypatch, capsys):
    monkeypatch.setattr(messenger, 'PAGE_ACCESS_TOKEN', 'private-token\nrest')
    monkeypatch.setattr('sys.argv', ['messenger_setup'])
    monkeypatch.setattr(messenger_setup.httpx, 'Client',
                        lambda **kwargs: pytest.fail('Invalid token must not create an HTTP client'))
    with pytest.raises(SystemExit) as error:
        messenger_setup.main()
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert 'PAGE_ACCESS_TOKEN' in output
    assert 'private-token' not in output


def test_setup_local_protocol_errors_are_redacted():
    def handler(request):
        raise httpx.LocalProtocolError("Illegal header value b'Bearer private-token'", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(messenger_setup.SetupError, match='Local HTTP protocol error') as error:
            messenger_setup.graph_request(client, 'GET', 'me')
    assert 'private-token' not in str(error.value)


def test_storefront_health_and_webhook_coexist(client, monkeypatch, tmp_path):
    from backend import main
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html>Customer storefront</html>', encoding='utf-8')
    (dist / 'admin.html').write_text('<html>Admin console</html>', encoding='utf-8')
    monkeypatch.setattr(main, 'DIST', dist)
    monkeypatch.setattr(messenger, 'VERIFY_TOKEN', 'test-verify')
    monkeypatch.setattr(messenger, 'APP_SECRET', 'test-app-secret')

    for path in ('/', '/app'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/html')
        assert response.text == '<html>Customer storefront</html>'
        assert 'Content-Security-Policy' in response.headers
    assert client.get('/admin').text == '<html>Admin console</html>'
    health = client.get('/healthz')
    assert health.json() == {'status': 'active'}
    assert health.headers['Cache-Control'] == 'no-store'
    verification = client.get('/webhook', params={
        'hub.mode': 'subscribe', 'hub.verify_token': 'test-verify', 'hub.challenge': '123456',
    })
    assert verification.status_code == 200
    assert verification.text == '123456'
    assert verification.headers['content-type'].startswith('text/plain')
    receipt = signed_post(client, {'object': 'page', 'entry': []})
    assert receipt.status_code == 200
    assert receipt.json() == {'status': 'ok'}
