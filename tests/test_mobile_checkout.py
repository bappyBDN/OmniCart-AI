from backend.database import transaction
from models import Order, Product


DETAILS = {'name': 'Mobile Shopper', 'phone': '01712345678', 'address': 'House 12, Road 4, Dhaka 1205'}
EDITED = {'name': 'Edited Mobile Shopper', 'phone': '01898765432', 'address': 'House 18, Road 6, Dhaka 1207'}


def guest_draft(client):
    session = client.post('/api/sessions').json()
    auth = {'Authorization': f"Bearer {session['token']}"}
    assert client.patch('/api/profile', headers=auth, json=DETAILS).status_code == 200
    response = client.post('/api/checkout', headers=auth, json={'product_id': 'SKU_183', 'quantity': 2})
    assert response.status_code == 200
    return auth, response.json()


def test_mobile_checkout_saves_edited_details_only_on_explicit_confirmation(client):
    auth, draft = guest_draft(client)
    assert draft['name'] == DETAILS['name']
    assert draft['phone'] == DETAILS['phone']
    assert client.get('/api/orders', headers=auth).json() == []
    assert client.get('/api/public/products/SKU_183').json()['stock_count'] == 80
    endpoint = f"/api/checkout/{draft['draft_id']}/confirm"
    receipt = client.post(endpoint, headers=auth, json=EDITED)
    assert receipt.status_code == 200
    assert receipt.json()['product_id'] == 'SKU_183' and receipt.json()['quantity'] == 2
    profile = client.get('/api/profile', headers=auth).json()
    assert all(profile[key] == value for key, value in EDITED.items())
    assert client.get('/api/public/products/SKU_183').json()['stock_count'] == 78
    duplicate = client.post(endpoint, headers=auth, json={**EDITED, 'name': 'Must not apply on retry'})
    assert duplicate.json()['order_id'] == receipt.json()['order_id']
    assert client.get('/api/profile', headers=auth).json()['name'] == EDITED['name']
    with transaction() as db:
        assert db.query(Order).count() == 1


def test_invalid_delivery_details_do_not_place_an_order(client):
    auth, draft = guest_draft(client)
    endpoint = f"/api/checkout/{draft['draft_id']}/confirm"
    for values in ({**EDITED, 'name': '  '}, {**EDITED, 'phone': '123'}, {**EDITED, 'address': 'short'}):
        assert client.post(endpoint, headers=auth, json=values).status_code == 422
    assert client.get('/api/orders', headers=auth).json() == []
    assert client.get('/api/public/products/SKU_183').json()['stock_count'] == 80


def test_price_conflict_rolls_back_edited_contact_details_and_stock(client):
    auth, draft = guest_draft(client)
    with transaction() as db:
        db.get(Product, ('shop_01', 'SKU_183')).discounted_price = 13.50
    response = client.post(f"/api/checkout/{draft['draft_id']}/confirm", headers=auth, json=EDITED)
    assert response.status_code == 409
    profile = client.get('/api/profile', headers=auth).json()
    assert all(profile[key] == value for key, value in DETAILS.items())
    assert client.get('/api/orders', headers=auth).json() == []
    assert client.get('/api/public/products/SKU_183').json()['stock_count'] == 80


def test_form_checkout_cannot_confirm_another_customers_draft(client):
    _, draft = guest_draft(client)
    other = client.post('/api/sessions').json()
    auth = {'Authorization': f"Bearer {other['token']}"}
    response = client.post(f"/api/checkout/{draft['draft_id']}/confirm", headers=auth, json=EDITED)
    assert response.status_code == 404
    assert client.get('/api/orders', headers=auth).json() == []
