"""Exercise the reported chat with real model providers in a disposable shop DB.

Requires explicit --live-model opt-in: configured providers may charge for calls.
Never connects to the real shop database, MCP process, email or payment services.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-model', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.live_model:
        parser.error('Pass --live-model to allow calls to your configured model providers.')
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ.update(MCP_ENABLED='false', RAG_DENSE_ENABLED='false', EMAIL_ENABLED='false')

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from backend import database
    from backend.config import settings
    from models import Base, Product, Policy, Shop, Order

    temporary_root = Path(tempfile.gettempdir()) / 'opencode' if os.name == 'nt' else None
    with tempfile.TemporaryDirectory(prefix='conversation-smoke-', dir=temporary_root) as folder:
        database.engine = database.make_engine(f"sqlite:///{Path(folder, 'shop.db').as_posix()}")
        database.SessionLocal = sessionmaker(bind=database.engine, expire_on_commit=False)
        Base.metadata.create_all(database.engine)
        from backend.main import app
        # Importing the application registers its auxiliary table metadata.
        Base.metadata.create_all(database.engine)
        with database.transaction() as db:
            db.add(Shop(shop_id=settings.shop_id, shop_name='Isolated conversation smoke test'))
            db.flush()
            for product in json.loads((root / 'Data/products.json').read_text(encoding='utf-8')):
                db.add(Product(**product, shop_id=settings.shop_id))
            for policy in json.loads((root / 'Data/policies.json').read_text(encoding='utf-8')):
                db.add(Policy(policy_type=policy['topic'], policy_text=policy['content'], shop_id=settings.shop_id))
        records = []
        try:
            with TestClient(app) as client:
                token = client.post('/api/sessions').json()['token']
                headers = {'Authorization': f'Bearer {token}'}
                client.patch('/api/profile', headers=headers, json={'name': 'Context smoke tester'}).raise_for_status()
                for question in (
                    'bangladeshi tk te koto tk?', 'protein powder er details bolo',
                    'different colour er hobe?', 'ata kano khabo?', 'i wanna order this', '2',
                    'bangladeshi tk te koto tk?', '1 USD = 120 BDT',
                ):
                    response = client.post('/api/chat', headers=headers, json={'message': question, 'request_id': str(uuid4())})
                    response.raise_for_status()
                    data = response.json()
                    record = {'question': question, **{key: data.get(key) for key in (
                        'answer', 'route', 'locale', 'focus_product_id', 'pending_action', 'human_intervention',
                        'degraded', 'latency_ms', 'usage', 'drafts')}, 'source_ids': [s['id'] for s in data['sources']]}
                    records.append(record)
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                    assert not data.get('human_intervention'), 'A routine follow-up opened support unexpectedly'
                assert not records[1]['degraded'], 'Product details used a fallback, not a successful live model response'
                assert any('\u0980' <= char <= '\u09ff' for char in records[1]['answer']), 'Banglish details request did not receive Bengali-script text'
                assert records[4]['pending_action']['kind'] == 'quantity'
                assert records[5]['drafts'][0]['total_price'] == 89.98
                assert '৳5,398.80' in records[7]['answer']
                with database.transaction() as db:
                    assert db.get(Product, ('shop_01', 'SKU_140')).stock_count == 12
                    assert db.query(Order).count() == 0
            if args.output:
                args.output.write_text(json.dumps({'isolated_database': True, 'live_models': True, 'passed': True, 'turns': records}, ensure_ascii=False, indent=2), encoding='utf-8')
            print('PASS: live-model conversation in isolated DB; no confirmed order or inventory mutation.')
        finally:
            database.engine.dispose()


if __name__ == '__main__':
    main()
