from uuid import uuid4

from langchain_core.messages import AIMessage

from backend import agent
from backend.database import transaction
from backend.language import normalize_query
from backend.records import AuditEvent, GraphCheckpoint, SupportCase
from backend.retrieval import HybridRetriever
from models import Admin, ChatHistory


def test_banglish_search_honors_color_size_and_price(store):
    value = normalize_query('lal jama medium dam koto?')
    assert value.locale == 'bn-Latn'
    assert 'red' in value.text and 'shirt' in value.text
    results = HybridRetriever(False).search('lal jama medium dam koto?', 'shop_01', 'product')
    assert [source['title'] for source in results] == ['Polo Shirt']
    assert HybridRetriever(False).search('lal jama 20 moddhe', 'shop_01', 'product') == []


def test_normalization_does_not_corrupt_english_substrings():
    assert normalize_query('The item was damaged').text == 'the item was damaged'
    assert normalize_query('শার্ট ৩০ ডলার').text == 'শার্ট 30 ডলার'


def test_numeric_stock_claims_are_checked_against_live_sources():
    sources = [{'data': {'stock_count': 15}}]
    assert agent.stock_claims_supported('Stock: 15. There are 15 in stock.', sources)
    assert not agent.stock_claims_supported('Stock count is 1000', sources)
    assert not agent.stock_claims_supported('স্টকে ১০০০টি আছে', sources)


def test_explicit_human_request_uses_durable_interrupt_without_model(store, monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('Must not call a model')))
    request_id = str(uuid4())
    result = agent.run_agent(store['customer_id'], 'shop_01', 'I need a human agent', [], request_id=request_id)
    assert result['route'] == 'human'
    assert result['handoff']['case_id']
    thread = f"shop_01:web:{store['customer_id']}:{request_id}"
    state = agent.graph.get_state({'configurable': {'thread_id': thread}})
    assert state.next == ('human_wait',)
    assert state.tasks[0].interrupts
    again = agent.run_agent(store['customer_id'], 'shop_01', 'I need a human agent', [], request_id=request_id)
    assert again['handoff']['case_id'] == result['handoff']['case_id']
    agent.resume_handoff(thread, 'The customer question was resolved.')
    assert not agent.graph.get_state({'configurable': {'thread_id': thread}}).next


def test_repeated_low_confidence_still_requires_handoff_consent(store, monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('No evidence should not call a model')))
    result = agent.run_agent(store['customer_id'], 'shop_01', 'What is your antimatter policy?', [])
    assert result['offer_handoff'] and not result['handoff']
    assert not result['sources']
    again = agent.run_agent(store['customer_id'], 'shop_01', 'What is your antimatter policy?', [])
    assert again['offer_handoff'] and not again['handoff']
    assert again['route'] != 'human'


def test_user_can_accept_handoff_offer_in_natural_conversation(store, monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('No model needed')))
    history = [ChatHistory(sender='ai', message_text="I couldn't verify that. Would you like me to send it to the support team?")]
    result = agent.run_agent(store['customer_id'], 'shop_01', 'Yes please', history)
    assert result['handoff'] and result['route'] == 'human'


def test_repeated_product_miss_clarifies_without_forcing_handoff(store, monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('No evidence should not call a model')))
    first = agent.run_agent(store['customer_id'], 'shop_01', 'dragon eggs', [])
    assert not first['handoff'] and first['offer_handoff']
    assert "haven't found" in first['answer'] and first['sources'] == []
    second = agent.run_agent(store['customer_id'], 'shop_01', 'dragon eggs', [])
    assert second['offer_handoff'] and not second['handoff']
    assert 'share its name' in second['answer'].lower()


def test_product_miss_falls_back_to_published_policy_only_when_relevant(client, admin_headers, store, monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('English policy answer should be verbatim')))
    before = agent.run_agent(store['customer_id'], 'shop_01', 'gift wrapping', [])
    assert before['offer_handoff'] and before['sources'] == []
    payload = {'title': 'Gift wrapping service', 'content': 'Gift wrapping costs $3 per parcel and uses recyclable paper.',
               'locale': 'en', 'category': 'faq', 'reason': 'Verified store service'}
    document = client.post('/api/admin/knowledge/documents', headers=admin_headers, json=payload).json()
    client.post(f"/api/admin/knowledge/documents/{document['document_id']}/publish", headers=admin_headers,
                json={'version': 1, 'reason': 'Publish verified service'})
    after = agent.run_agent(store['customer_id'], 'shop_01', 'gift wrapping', [])
    assert 'Gift wrapping costs $3' in after['answer']
    assert any(source['id'].startswith('knowledge:') for source in after['sources'])
    assert after['route'] == 'support'


def test_banglish_human_request_does_not_need_a_model(store, monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('No model needed')))
    result = agent.run_agent(store['customer_id'], 'shop_01', 'ami manusher sathe kotha bolte chai', [])
    assert result['handoff'] and result['locale'] == 'bn-Latn'


def test_simple_policy_question_does_not_escalate(store, monkeypatch):
    class Model:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            if self.schema:
                return agent.GroundedAnswer(answer='Refund requests must be made within 7 days. [policy:1:0]', source_ids=['policy:1:0'])
            return AIMessage(content='Refund requests must be made within 7 days. [policy:1:0]')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Model(schema))
    result = agent.run_agent(store['customer_id'], 'shop_01', 'What is the refund policy?', [])
    assert not result['handoff']
    assert not result['degraded']


def test_policy_comparison_cannot_invent_approval_steps(store, monkeypatch):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('Exact English clauses should not call a model')))
    result = agent.run_agent(store['customer_id'], 'shop_01', 'What is the difference between your refund policy and return window?', [])
    assert '7 days' in result['answer'] and '14 days' in result['answer']
    assert 'inspection' not in result['answer'] and 'approval' not in result['answer']
    assert result['model_tier'] == 'rules' and not result['degraded']


def test_two_failed_tools_escalate_without_endless_loop(store, monkeypatch):
    class Model:
        def invoke(self, messages):
            return AIMessage(content='', tool_calls=[{'name': 'unauthorized_tool', 'args': {}, 'id': str(uuid4())}])
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: Model())
    result = agent.run_agent(store['customer_id'], 'shop_01', 'Show shirts', [])
    assert result['handoff']['reason'] == 'tool_failure'
    assert len([t for t in result['trace'] if t['node'] == 'tool']) == 2


def test_model_detected_authority_limit_routes_to_a_real_human_case(store, monkeypatch):
    class Model:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            if self.schema:
                return agent.GroundedAnswer(answer='This requires a specialist.', source_ids=[], needs_human=True)
            return AIMessage(content='A specialist needs to coordinate this case.')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Model(schema))
    result = agent.run_agent(store['customer_id'], 'shop_01', 'cotton shirt special resolution with the store manager', [])
    assert result['route'] == 'human' and result['handoff']['reason'] == 'authority_exceeded'


def test_case_notes_assignment_and_resume_are_private(client, headers, admin_headers):
    response = client.post('/api/chat', headers=headers, json={'message': 'I need a human', 'request_id': str(uuid4())})
    assert response.status_code == 200
    case_id = response.json()['handoff']['case_id']
    context = client.get(f'/api/admin/cases/{case_id}/context', headers=admin_headers).json()
    customer_id = context['customer_id']
    team = client.get('/api/admin/team', headers=admin_headers).json()
    updated = client.patch(f'/api/admin/cases/{case_id}', headers=admin_headers,
        json={'note': 'PRIVATE INVESTIGATION ONLY', 'assigned_admin_id': team[0]['admin_id'], 'reason': 'Review delivery issue'})
    assert updated.status_code == 200
    assert updated.json()['assigned_admin_id'] == team[0]['admin_id']
    waiting = client.post('/api/chat', headers=headers, json={'message': 'Are you there?', 'request_id': str(uuid4())}).json()
    assert waiting['status'] == 'queued_for_human' and not waiting['answer']
    history = client.get('/api/messages', headers=headers).json()
    assert history[-1]['sender'] == 'user'
    assert all('PRIVATE INVESTIGATION' not in message['content'] for message in history)
    result = client.post(f'/api/admin/cases/{case_id}/resolve', headers=admin_headers,
        json={'note': 'The package will be reviewed by the delivery team.', 'resolution_code': 'resolved'})
    assert result.status_code == 200
    assert not client.get('/api/profile', headers=headers).json()['human_intervention']
    events = client.get('/api/admin/audit', headers=admin_headers).json()
    assert any(event['action'] == 'case.updated' for event in events)
    assert any(event['action'] == 'case.resolved' for event in events)


def test_knowledge_draft_publish_rollback_and_tenant_boundary(client, admin_headers, store):
    payload = {'title': 'Gift wrapping service', 'content': 'Gift wrapping costs $3 per parcel. It uses recyclable paper.', 'locale': 'en', 'category': 'faq', 'reason': 'Initial verified content'}
    document = client.post('/api/admin/knowledge/documents', headers=admin_headers, json=payload).json()
    document_id = document['document_id']
    retriever = HybridRetriever(False)
    assert not any(s['id'].startswith('knowledge:') for s in retriever.search('gift wrapping', 'shop_01', 'policy'))
    assert client.post(f'/api/admin/knowledge/documents/{document_id}/publish', headers=admin_headers, json={'version': 1, 'reason': 'Reviewed by operator'}).status_code == 200
    assert any('$3' in s['content'] for s in retriever.search('gift wrapping', 'shop_01', 'policy'))
    payload['content'] = 'Gift wrapping costs $4 per parcel. It uses recyclable paper.'
    assert client.post(f'/api/admin/knowledge/documents/{document_id}/versions', headers=admin_headers, json=payload).status_code == 200
    assert client.post(f'/api/admin/knowledge/documents/{document_id}/publish', headers=admin_headers, json={'version': 2, 'reason': 'Updated verified service price'}).status_code == 200
    assert any('$4' in s['content'] for s in retriever.search('gift wrapping', 'shop_01', 'policy'))
    assert client.post(f'/api/admin/knowledge/documents/{document_id}/publish', headers=admin_headers, json={'version': 1, 'reason': 'Rollback service price'}).status_code == 200
    assert any('$3' in s['content'] for s in retriever.search('gift wrapping', 'shop_01', 'policy'))
    assert not any(s['id'].startswith('knowledge:') for s in retriever.search('gift wrapping', 'other', 'policy'))


def test_support_role_cannot_edit_catalogue(client, admin_headers):
    with transaction() as db:
        db.query(Admin).filter_by(email='manager@example.com').first().role = 'Support Agent'
    assert client.get('/api/admin/team', headers=admin_headers).status_code == 200
    assert client.post('/api/admin/knowledge/documents', headers=admin_headers, json={}).status_code == 403
    assert client.get('/api/admin/dashboard', headers=admin_headers).status_code == 200


def test_product_aliases_are_live_and_scoped(client, admin_headers):
    response = client.post('/api/admin/products/SKU_101/aliases', headers=admin_headers, json={'alias': 'cottonbreeze', 'locale': 'en'})
    assert response.status_code == 201
    results = HybridRetriever(False).search('cottonbreeze', 'shop_01', 'product')
    assert results[0]['data']['product_id'] == 'SKU_101'
    assert client.post('/api/admin/products/OTHER_SECRET/aliases', headers=admin_headers, json={'alias': 'secret name'}).status_code == 404


def test_checkout_does_not_require_email(client, headers):
    profile = {'name': 'Amina Rahman', 'phone': '01712345678', 'address': '12 Test Road, Dhaka', 'email': ''}
    assert client.patch('/api/profile', headers=headers, json=profile).status_code == 200
    draft = client.post('/api/checkout', headers=headers, json={'product_id': 'SKU_101', 'quantity': 1}).json()
    assert 'draft_id' in draft


def test_takeover_suppresses_late_ai_output(client, headers, admin_headers, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()
    customer_id = client.get('/api/profile', headers=headers).json()['customer_id']
    def slow_agent(*args):
        entered.set()
        release.wait(10)
        return {'answer': 'LATE AUTOMATED ANSWER', 'sources': [], 'drafts': [], 'trace': [], 'route': 'shopping', 'usage': {}, 'latency_ms': 1, 'degraded': False}
    monkeypatch.setattr(agent, 'run_agent', slow_agent)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(client.post, '/api/chat', headers=headers, json={'message': 'Help me shop', 'request_id': str(uuid4())})
        assert entered.wait(5)
        try:
            paused = client.patch(f'/api/admin/customers/{customer_id}/handoff', headers=admin_headers, json={'enabled': True})
            assert paused.status_code == 200
            assert not running.done(), 'Takeover should finish before the in-flight model'
        finally:
            release.set()
        assert running.result().json()['answer'] == ''
    assert all('LATE AUTOMATED' not in m['content'] for m in client.get('/api/messages', headers=headers).json())


def test_runtime_crash_creates_a_real_case_instead_of_exposing_error(client, headers, monkeypatch):
    def fail(*args):
        raise RuntimeError('PRIVATE INTERNAL FAILURE DETAILS')
    monkeypatch.setattr(agent, 'run_agent', fail)
    response = client.post('/api/chat', headers=headers, json={'message': 'Please help with my purchase', 'request_id': str(uuid4())})
    assert response.status_code == 200
    assert response.json()['handoff']['reason'] == 'runtime_failure'
    assert 'PRIVATE INTERNAL' not in response.text
    assert client.get('/api/profile', headers=headers).json()['human_intervention']


def test_raw_images_are_not_written_into_durable_checkpoints(store, monkeypatch):
    marker = 'PRIVATE_IMAGE_PAYLOAD_MUST_NOT_PERSIST'
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(RuntimeError('Vision unavailable')))
    agent.run_agent(store['customer_id'], 'shop_01', 'Find this image', [], image=marker)
    with transaction() as db:
        assert db.query(GraphCheckpoint).count() > 0
        assert all(marker.encode() not in row.payload and marker.encode() not in row.metadata_blob for row in db.query(GraphCheckpoint))
    assert not agent._images
