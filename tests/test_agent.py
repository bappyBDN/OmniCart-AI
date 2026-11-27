from langchain_core.messages import AIMessage

from backend import agent
from backend.agent import GroundedAnswer, build_tools, run_agent


def test_followup_focus_and_media_intent(store):
    previous = [{"kind": "product", "title": "Premium Cotton T-Shirt", "data": {"product_id": "SKU_101"}}]
    assert agent.followup_focus("what size is it?", previous) == "SKU_101"
    assert agent.followup_focus("show me the image", previous) == "SKU_101"
    assert agent.followup_focus("i need 6", previous) == "SKU_101"
    assert agent.followup_focus("6 pieces", previous) == "SKU_101"
    assert agent.followup_focus("show me hoodies", previous) is None
    assert agent.followup_focus("show similar alternatives", previous) is None
    assert agent.wants_product_media("Show me shirts") is True
    assert agent.wants_product_media("What is the price and stock?") is False
    assert agent.wants_product_media("i need 6") is False
    assert agent.wants_product_media("show image of it") is True
    assert agent.requested_quantity("i need 6") == 6
    assert agent.requested_quantity("6 pieces") == 6
    assert agent.requested_quantity("need 6 pcs") == 6
    assert agent.requested_quantity("i need 6 of them") == 6
    assert agent.requested_quantity("ok i need to buy 3") == 3
    assert agent.requested_quantity("okay, I need to buy 3") == 3
    assert agent.requested_quantity("i'll take 6") == 6
    assert agent.requested_quantity("buy 3") == 3
    assert agent.requested_quantity("ami 3 ta nibo") == 3
    assert agent.requested_quantity("3 ta nibo") == 3
    assert agent.requested_quantity("i need 6 hoodies") is None
    assert agent.requested_quantity("what size is it?") is None
    assert agent.requested_quantity("Backpack etar configuration bolo") is None
    assert agent.followup_focus("ok i need to buy 3", previous) == "SKU_101"
    ambiguous = previous + [{"kind": "product", "title": "Hoodie", "data": {"product_id": "SKU_116"}}]
    assert agent.followup_focus("i need 6", ambiguous) is None


def test_quantity_followup_uses_focused_stock_and_returns_similar_cards(store):
    previous = [{"id": "product:SKU_108", "kind": "product", "title": "Backpack",
                 "data": {"product_id": "SKU_108"}}]
    result = agent.run_agent(store["customer_id"], "shop_01", "i need 6", [], previous_sources=previous)
    returned = {source["id"] for source in result["sources"]}
    assert "product:SKU_108" in returned
    # Phase 1 / defect 14: every similar product named in the text must also be
    # returned as a card source, never text-only.
    assert len(returned) > 1
    assert all(source["kind"] == "product" for source in result["sources"])
    assert result["show_images"] is True
    assert result["degraded"] is False
    assert result["model_tier"] == "rules"
    assert "5" in result["answer"]


def test_conversational_quantity_followup_keeps_focus(store):
    previous = [{"id": "product:SKU_108", "kind": "product", "title": "Backpack",
                 "data": {"product_id": "SKU_108"}}]
    result = agent.run_agent(store["customer_id"], "shop_01", "ok i need to buy 3", [],
                             previous_sources=previous)
    assert {source["id"] for source in result["sources"]} == {"product:SKU_108"}
    assert result["show_images"] is False
    assert result["degraded"] is False
    assert "৳195.00" in result["answer"]


def test_quantity_without_focus_asks_for_product_instead_of_listing_catalogue(store):
    result = agent.run_agent(store["customer_id"], "shop_01", "i need 6", [])
    assert result["sources"] == []
    assert result["show_images"] is False
    assert "which product" in result["answer"].lower()


def test_specific_product_followup_stays_focused_and_images_are_opt_in(store, monkeypatch):
    class Fake:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            if self.schema:
                return GroundedAnswer(answer='It is available in Grey and Black. [product:SKU_116]', source_ids=['product:SKU_116'])
            return AIMessage(content='It is available in Grey and Black. [product:SKU_116]')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Fake(schema))
    previous = [{'id': 'product:SKU_116', 'kind': 'product', 'title': 'Hoodie',
                 'data': {'product_id': 'SKU_116'}}]
    followup = agent.run_agent(store['customer_id'], 'shop_01', 'what colors is it?', [], previous_sources=previous)
    assert followup['sources'] and {source['id'] for source in followup['sources']} == {'product:SKU_116'}
    assert followup['show_images'] is False
    visual = agent.run_agent(store['customer_id'], 'shop_01', 'show me the image', [], previous_sources=previous)
    assert visual['show_images'] is True
    assert {source['id'] for source in visual['sources']} == {'product:SKU_116'}


def test_context_window_prioritizes_recent_turns(store):
    from models import ChatHistory
    rows = [ChatHistory(sender="user" if index % 2 == 0 else "ai", message_text=f"older-{index}") for index in range(30)]
    recent, summary = agent.build_context_window(rows, "current question")
    assert recent[-1].message_text == "older-29"
    assert len(recent) == 16  # eight user/assistant turns, not eight messages
    assert "older-0" in summary and "older-13" in summary


def test_supervisor_selects_visual_specialist_for_image_turn(store):
    result = agent.route_node({'query': 'find something like this', 'image': True, 'trace': []})
    assert result['sub_agent'] == 'visual_agent'
    assert result['trace'][0]['node'] == 'supervisor'


def test_hallucinated_profile_values_rejected(store):
    tools = {t.name: t for t in build_tools(store['customer_id'], 'shop_01', 'আমার নাম আমিনা রহমান। Email: amina@example.com')}
    assert 'error' in tools['update_user_info_tool'].invoke({'name': 'Invented Person'})
    result = tools['update_user_info_tool'].invoke({'name': 'আমিনা রহমান', 'email': 'amina@example.com'})
    assert result['name'] == 'আমিনা রহমান'


def test_quantity_quote_uses_live_discount_and_decimal_arithmetic(store):
    tools = {t.name: t for t in build_tools(store['customer_id'], 'shop_01', 'How much for 3 shirts?')}
    result = tools['quote_product_tool'].invoke({'product_id': 'SKU_101', 'quantity': 3})
    assert result['total_price'] == 59.97
    assert result['unit_price'] == 19.99


def test_phone_grounding_cannot_join_unrelated_numbers(store):
    tools = {t.name: t for t in build_tools(store['customer_id'], 'shop_01', 'I want 17 shirts, 1234 mugs, and 5678 pens')}
    result = tools['update_user_info_tool'].invoke({'phone': '1712345678'})
    assert 'error' in result


def test_bounded_langgraph_tools_and_citations(store, monkeypatch):
    class Fake:
        def __init__(self, schema):
            self.schema = schema
        def invoke(self, messages):
            if self.schema:
                return GroundedAnswer(answer='The shirt is $19.99. [product:SKU_101]', source_ids=['product:SKU_101'])
            if messages[-1].type != 'tool':
                return AIMessage(content='', tool_calls=[{'name': 'product_search_tool', 'args': {'query': 'SKU_101'}, 'id': 'lookup-1'}])
            return AIMessage(content='The shirt is $19.99. [product:SKU_101]')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Fake(schema))
    result = run_agent(store['customer_id'], 'shop_01', 'cotton shirt price', [])
    assert result['answer'] == 'The shirt is $19.99. [product:SKU_101]'
    assert any(event['node'] == 'tool' for event in result['trace'])
    assert not result['degraded']


def test_unknown_citation_falls_back_to_real_records(store, monkeypatch):
    class Fake:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            return GroundedAnswer(answer='Fake product [product:SKU_99999]', source_ids=['product:SKU_99999']) if self.schema else AIMessage(content='Fake product')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Fake(schema))
    result = run_agent(store['customer_id'], 'shop_01', 'cotton shirt', [])
    assert 'SKU_99999' not in result['answer']
    assert 'Premium Cotton T-Shirt' in result['answer']
    assert result['degraded']


def test_provider_failure_returns_grounded_fallback(store, monkeypatch):
    def fail(**kwargs): raise RuntimeError('provider unavailable')
    monkeypatch.setattr(agent, 'chat_model', fail)
    result = run_agent(store['customer_id'], 'shop_01', 'cotton shirt price', [])
    assert '19.99' in result['answer']
    assert result['degraded']


def test_greeting_never_requires_a_model(store, monkeypatch):
    def fail(**kwargs): raise AssertionError('Greeting should not call a model')
    monkeypatch.setattr(agent, 'chat_model', fail)
    result = run_agent(store['customer_id'], 'shop_01', 'হ্যালো', [])
    assert 'হ্যালো' in result['answer']
    assert not result['degraded']


def test_invented_amount_with_valid_citation_is_rejected(store, monkeypatch):
    class Fake:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            return GroundedAnswer(answer='The shirt costs $1234.56 [product:SKU_101]', source_ids=['product:SKU_101']) if self.schema else AIMessage(content='The shirt costs $1234.56')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Fake(schema))
    result = run_agent(store['customer_id'], 'shop_01', 'cotton shirt', [])
    assert '$1234.56' not in result['answer']
    assert result['degraded']


def test_model_source_id_prefix_is_normalised_to_a_clean_citation(store, monkeypatch):
    class Fake:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            if self.schema:
                return GroundedAnswer(answer='The price is $19.99 [source_id: product:SKU_101].', source_ids=['product:SKU_101'])
            return AIMessage(content='The price is $19.99')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Fake(schema))
    result = run_agent(store['customer_id'], 'shop_01', 'cotton shirt price', [])
    assert '[product:SKU_101]' in result['answer']
    assert 'source_id:' not in result['answer']


def test_bare_source_placeholder_and_unrequested_image_are_removed(store, monkeypatch):
    answer = ('The price is $19.99. ![Shirt](https://yourstore.com/api/products/SKU_101/image) '
              '/api/products/SKU_101/image [source_id] [product:SKU_101]')
    class Fake:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            return GroundedAnswer(answer=answer, source_ids=['product:SKU_101']) if self.schema else AIMessage(content=answer)
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Fake(schema))
    result = run_agent(store['customer_id'], 'shop_01', 'cotton shirt price', [])
    assert '![Shirt]' not in result['answer']
    assert 'yourstore.com' not in result['answer']
    assert '/api/products' not in result['answer']
    assert '[source_id]' not in result['answer']
    assert '[product:SKU_101]' in result['answer']
    assert '$19.99' in result['answer']


def test_product_name_check_uses_word_boundaries(store):
    source = [{'kind': 'product', 'title': 'Premium Cotton T-Shirt'}]
    assert agent.product_claims_supported('Capacity is 15 units.', source, 'shop_01')
    assert not agent.product_claims_supported('The Cap is also available.', source, 'shop_01')


def test_supported_sku_cannot_be_used_to_name_a_different_product(store, monkeypatch):
    class Fake:
        def __init__(self, schema): self.schema = schema
        def invoke(self, messages):
            if self.schema:
                return GroundedAnswer(answer='The Hoodie costs $19.99. [product:SKU_101]', source_ids=['product:SKU_101'])
            return AIMessage(content='The Hoodie costs $19.99. [product:SKU_101]')
    monkeypatch.setattr(agent, 'chat_model', lambda tools=None, schema=None, **kw: Fake(schema))
    result = run_agent(store['customer_id'], 'shop_01', 'cotton shirt', [])
    assert 'Hoodie' not in result['answer']
    assert 'Premium Cotton T-Shirt' in result['answer']
    assert result['degraded']


def test_endless_tool_loop_is_bounded(store, monkeypatch):
    class Endless:
        def invoke(self, messages):
            return AIMessage(content='', tool_calls=[{'name': 'check_user_status_tool', 'args': {}, 'id': f'check-{len(messages)}'}])
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: Endless())
    result = run_agent(store['customer_id'], 'shop_01', 'shirt', [])
    assert len([t for t in result['trace'] if t['node'] == 'tool']) == 4
    assert result['degraded']


def test_image_failure_does_not_claim_visual_matches(store, monkeypatch):
    def fail(**kwargs): raise RuntimeError('vision unavailable')
    monkeypatch.setattr(agent, 'chat_model', fail)
    result = run_agent(store['customer_id'], 'shop_01', 'Find this', [], image='placeholder')
    assert 'couldn\'t open that photo' in result['answer']
    assert result['sources'] == []
