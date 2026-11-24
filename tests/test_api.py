from uuid import uuid4

from backend import agent
from backend.database import transaction
from backend.main import app
from models import Customer, Order, Product


def test_auth_required_and_role_separation(client, headers, admin_headers):
    assert client.get('/api/profile').status_code == 401
    assert client.get('/api/admin/dashboard', headers=headers).status_code == 403
    assert client.get('/api/profile', headers=admin_headers).status_code == 403


def test_admin_dashboard_reports_aggregated_provider_cost(client, headers, admin_headers, monkeypatch):
    monkeypatch.setattr(agent, 'run_agent', lambda *args: {
        'answer': 'ok', 'sources': [], 'drafts': [], 'trace': [], 'route': 'shopping',
        'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120, 'cached_tokens': 0,
                  'calls': 1, 'cost_usd': 0.00012,
                  'by_model': {'deepseek:deepseek-flash': {'provider': 'deepseek', 'model': 'deepseek-flash',
                      'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120,
                      'cached_tokens': 0, 'calls': 1, 'cost_usd': 0.00012}}},
        'latency_ms': 1, 'degraded': False,
    })
    assert client.post('/api/chat', headers=headers, json={'message': 'hello there', 'request_id': str(uuid4())}).status_code == 200
    usage = client.get('/api/admin/dashboard', headers=admin_headers).json()['ai_usage']
    assert usage['calls'] == 1
    assert usage['total_tokens'] == 120
    assert usage['by_model']['deepseek:deepseek-flash']['cost_usd'] == 0.00012


def test_no_email_impersonation(client, headers):
    profile = client.get('/api/profile', headers=headers).json()
    assert profile['email'] is None
    assert client.get('/api/orders', headers=headers).json() == []


def test_profile_validation(client, headers):
    assert client.patch('/api/profile', headers=headers, json={'email': 'not-email'}).status_code == 422
    assert client.patch('/api/profile', headers=headers, json={'phone': '123'}).status_code == 422


def test_checkout_and_cross_customer_access(client, complete_headers, headers):
    draft = client.post('/api/checkout', headers=complete_headers, json={'product_id': 'SKU_101', 'quantity': 2}).json()
    other_token = client.post('/api/sessions').json()['token']
    other = {'Authorization': f'Bearer {other_token}'}
    assert client.post(f"/api/checkout/{draft['draft_id']}/confirm", headers=other).status_code == 404
    assert client.post(f"/api/checkout/{draft['draft_id']}/confirm", headers=complete_headers).status_code == 200
    assert client.get('/api/orders', headers=other).json() == []


def test_handoff_reply_and_resume(client, headers, admin_headers, monkeypatch):
    case = client.post('/api/support', headers=headers, json={'message': 'I need a human for a damaged delivery'}).json()
    customer_id = client.get('/api/profile', headers=headers).json()['customer_id']
    response = client.post('/api/chat', headers=headers, json={'message': 'Please help', 'request_id': str(uuid4())})
    assert response.json()['route'] == 'human'
    reply = client.post(f'/api/admin/customers/{customer_id}/messages', headers=admin_headers, json={'message': 'We are reviewing your issue.'})
    assert reply.status_code == 201
    assert any(m['sender'] == 'admin' for m in client.get('/api/messages', headers=headers).json())
    assert client.post(f"/api/admin/cases/{case['case_id']}/resolve", headers=admin_headers).status_code == 200
    assert not client.get('/api/profile', headers=headers).json()['human_intervention']


def test_chat_request_retries_do_not_duplicate(client, headers, monkeypatch):
    calls = []
    def fake(*args):
        calls.append(args)
        return {'answer': 'Hello', 'sources': [], 'drafts': [], 'trace': [], 'route': 'shopping', 'latency_ms': 2, 'usage': {}, 'degraded': False}
    monkeypatch.setattr(agent, 'run_agent', fake)
    payload = {'message': 'Hello', 'request_id': str(uuid4())}
    first = client.post('/api/chat', headers=headers, json=payload)
    second = client.post('/api/chat', headers=headers, json=payload)
    assert first.json() == second.json()
    assert len(calls) == 1
    assert len(client.get('/api/messages', headers=headers).json()) == 2


def test_checkout_collects_address_and_phone_on_review(client, headers):
    assert client.patch('/api/profile', headers=headers, json={'name': 'Rahim Uddin'}).status_code == 200
    draft = client.post('/api/checkout', headers=headers,
                        json={'product_id': 'SKU_101', 'quantity': 1}).json()
    assert set(draft['missing_contact']) == {'phone', 'address'}
    invalid = client.post(f"/api/checkout/{draft['draft_id']}/confirm", headers=headers,
                          json={'phone': '123', 'address': 'short'})
    assert invalid.status_code == 422
    receipt = client.post(f"/api/checkout/{draft['draft_id']}/confirm", headers=headers,
                          json={'phone': '01712345678', 'address': 'House 5, Road 2, Dhanmondi, Dhaka 1205'})
    assert receipt.status_code == 200
    profile = client.get('/api/profile', headers=headers).json()
    assert profile['phone'] == '01712345678'
    assert profile['address'].startswith('House 5')


def test_chat_recovers_focus_after_degraded_multi_product_turn(client, headers, monkeypatch):
    backpack = [{'id': 'product:SKU_108', 'kind': 'product', 'title': 'Backpack',
                 'data': {'product_id': 'SKU_108', 'name': 'Backpack', 'stock_count': 5}}]
    noise = [
        {'id': 'product:SKU_167', 'kind': 'product', 'title': 'Sticky Notes',
         'data': {'product_id': 'SKU_167', 'name': 'Sticky Notes', 'stock_count': 210}},
        {'id': 'product:SKU_122', 'kind': 'product', 'title': 'Notebook',
         'data': {'product_id': 'SKU_122', 'name': 'Notebook', 'stock_count': 200}},
    ]
    seen = []

    def fake(*args):
        seen.append(args[4])
        message = args[2]
        if message == 'Tell me about the Backpack':
            return {'answer': 'Backpack details', 'sources': backpack, 'focus_product_id': 'SKU_108',
                    'show_images': False, 'drafts': [], 'trace': [], 'route': 'shopping',
                    'latency_ms': 1, 'usage': {}, 'degraded': False}
        if message == 'i need 6':
            return {'answer': 'AI unavailable', 'sources': noise, 'focus_product_id': None,
                    'show_images': True, 'drafts': [], 'trace': [], 'route': 'shopping',
                    'latency_ms': 1, 'usage': {}, 'degraded': True}
        return {'answer': 'ok', 'sources': [], 'drafts': [], 'trace': [], 'route': 'shopping',
                'latency_ms': 1, 'usage': {}, 'degraded': False}

    monkeypatch.setattr(agent, 'run_agent', fake)
    for message in ('Tell me about the Backpack', 'i need 6', '6 pcs'):
        assert client.post('/api/chat', headers=headers,
                           json={'message': message, 'request_id': str(uuid4())}).status_code == 200
    assert seen[1] == backpack
    assert seen[2] == backpack


def test_bad_images_and_empty_messages_rejected(client, headers):
    payload = {'message': 'what is this', 'request_id': str(uuid4()), 'image': 'data:image/png;base64,bm90YW5pbWFnZQ=='}
    assert client.post('/api/chat', headers=headers, json=payload).status_code == 422
    assert client.post('/api/chat', headers=headers, json={'message': '  ', 'request_id': str(uuid4())}).status_code == 422


def test_admin_edit_is_immediately_visible(client, headers, admin_headers):
    products = client.get('/api/products?q=SKU_101', headers=headers).json()
    product = products[0]
    product['stock_count'] = 7
    assert client.put('/api/admin/products/SKU_101', headers=admin_headers, json=product).status_code == 200
    assert client.get('/api/products?q=SKU_101', headers=headers).json()[0]['stock_count'] == 7


def test_cancel_restocks_once(client, complete_headers, admin_headers):
    draft = client.post('/api/checkout', headers=complete_headers, json={'product_id': 'SKU_101', 'quantity': 2}).json()
    order = client.post(f"/api/checkout/{draft['draft_id']}/confirm", headers=complete_headers).json()
    for _ in range(2):
        assert client.patch(f"/api/admin/orders/{order['order_id']}", headers=admin_headers, json={'status': 'Cancelled'}).status_code == 200
    with transaction() as db:
        assert db.get(Product, 'SKU_101').stock_count == 15
    assert client.patch(f"/api/admin/orders/{order['order_id']}", headers=admin_headers, json={'status': 'Pending'}).status_code == 409


def test_logout_revokes_token(client, headers):
    assert client.delete('/api/sessions/current', headers=headers).status_code == 204
    assert client.get('/api/profile', headers=headers).status_code == 401


def test_invalid_login_and_missing_api_route(client):
    assert client.post('/api/admin/login', json={'email': 'bad@example.com', 'password': 'wrong'}).status_code == 401
    assert client.get('/api/not-a-real-endpoint').status_code == 404
