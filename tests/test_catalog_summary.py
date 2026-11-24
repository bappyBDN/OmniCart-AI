import pytest

from backend import agent
from backend.catalog import catalog_summary, is_catalog_count_question
from backend.database import transaction
from models import Product, Shop


@pytest.mark.parametrize('question', [
    'how many product you have',
    'How many products do you have in your store?',
    'What is the total number of products?',
    'How many items are in stock?',
    'Total stock?',
    'আপনাদের কতগুলো পণ্য আছে?',
    'আপনার কাছে মোট কতটি পণ্য আছে?',
    'apnader kache koyta product ache?',
])
def test_catalog_count_questions_use_live_totals_without_llm(store, monkeypatch, question):
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('Catalog totals must not need a model')))
    result = agent.run_agent(store['customer_id'], 'shop_01', question, [])
    assert result['model_tier'] == 'rules'
    assert not result['degraded'] and not result['handoff']
    assert result['sources'][0]['data']['total_products'] == 100
    assert '100' in result['answer']
    assert any(t['detail'].startswith('catalog_summary_tool') for t in result['trace'])


@pytest.mark.parametrize('question', [
    'How many red products do you have?',
    'How many products under $30?',
    'How many Electronics products do you have?',
    'How many shirts are available in size M?',
    'How many units of SKU_101 are in stock?',
    'How much is this product?',
])
def test_filtered_questions_do_not_get_an_unfiltered_total(question):
    assert not is_catalog_count_question(question)


def test_summary_is_live_and_tenant_scoped(store):
    original = catalog_summary('shop_01')
    assert original['total_products'] == 100
    assert catalog_summary('other')['total_products'] == 1
    with transaction() as db:
        db.get(Product, 'SKU_102').stock_count = 5
    updated = catalog_summary('shop_01')
    assert updated['total_products'] == 100
    assert updated['in_stock_products'] == original['in_stock_products'] + 1
    assert updated['available_units'] == original['available_units'] + 5


def test_category_summary_tool_counts_whole_category_not_search_limit(store):
    tools = {t.name: t for t in agent.build_tools(store['customer_id'], 'shop_01', 'How many electronics products?')}
    result = tools['catalog_summary_tool'].invoke({'category': 'electronics'})
    with transaction() as db:
        expected = db.query(Product).filter_by(shop_id='shop_01', category='Electronics').count()
    assert result['total_products'] == expected
    assert expected > 6


def test_empty_catalog_returns_zero_not_an_error(store):
    with transaction() as db:
        db.add(Shop(shop_id='empty', shop_name='Empty shop'))
    result = catalog_summary('empty')
    assert result['total_products'] == result['in_stock_products'] == result['available_units'] == 0


def test_exact_reported_question_works_through_chat_api(client, headers, monkeypatch):
    from uuid import uuid4
    monkeypatch.setattr(agent, 'chat_model', lambda **kw: (_ for _ in ()).throw(AssertionError('No model should run')))
    response = client.post('/api/chat', headers=headers,
        json={'message': 'how many product you have', 'request_id': str(uuid4())})
    assert response.status_code == 200
    assert '100 different products' in response.json()['answer']
    assert response.json()['sources'][0]['data']['total_products'] == 100
    assert not response.json()['human_intervention']
