from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from backend.database import make_engine, normalize_url
from backend.migrate_postgres import MigrationError, connection_urls, copy_tables, migrate_snapshot
from backend.records import AccessSession, ChatTurn
from models import Base, Shop


@pytest.mark.parametrize('scheme', ['postgres', 'postgresql', 'postgresql+psycopg'])
def test_postgres_urls_select_psycopg3(scheme):
    url = normalize_url(f'{scheme}://example:escaped%40password@db.example:5432/omnichat?sslmode=require')
    assert url.drivername == 'postgresql+psycopg'
    assert url.password == 'escaped@password'
    assert url.query['sslmode'] == 'require'
    assert url.database == 'omnichat'


def test_target_database_is_separate_and_credentials_are_preserved():
    admin, target = connection_urls('postgresql://example:password@db.example:5432/projectflow', 'omnichat')
    assert admin.database == 'projectflow'
    assert target.database == 'omnichat'
    assert target.host == admin.host
    assert target.password == admin.password
    assert 'password' not in str(target)


@pytest.mark.parametrize('name', ['projectflow', 'postgres', 'template1', 'bad;DROP DATABASE projectflow', 'UPPER', 'a' * 64])
def test_unsafe_or_administrative_target_is_rejected(name):
    with pytest.raises(MigrationError):
        connection_urls('postgresql://example:password@db.example:5432/projectflow', name)


def test_schema_compiles_for_postgresql():
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert 'CREATE TABLE' in ddl


def test_migration_preserves_all_tables_sessions_and_json(store, tmp_path):
    source = store['engine']
    with source.begin() as connection:
        connection.execute(AccessSession.__table__.insert().values(token_hash='hashed-session-token', role='customer',
            customer_id=store['customer_id'], shop_id='shop_01', expires_at=datetime.utcnow() + timedelta(days=1)))
        connection.execute(ChatTurn.__table__.insert().values(customer_id=store['customer_id'], request_id='test-request',
            response={'answer': 'বাংলা উত্তর', 'sources': [{'id': 'product:SKU_101'}]}))
    target = make_engine(f"sqlite:///{(tmp_path / 'destination.db').as_posix()}")
    try:
        counts = migrate_snapshot(source.url.database, target)
        assert counts['products'] == 101
        assert counts['policies'] == 51
        assert counts['api_access_sessions'] == 1
        assert counts['api_chat_turns'] == 1
        with source.connect() as original, target.connect() as migrated:
            for table in Base.metadata.sorted_tables:
                assert original.execute(select(func.count()).select_from(table)).scalar_one() == migrated.execute(select(func.count()).select_from(table)).scalar_one()
            response = migrated.execute(select(ChatTurn.__table__.c.response)).scalar_one()
            assert response['answer'] == 'বাংলা উত্তর'
            assert migrated.execute(select(AccessSession.__table__.c.token_hash)).scalar_one() == 'hashed-session-token'
    finally:
        target.dispose()


def test_migration_refuses_populated_target(store, tmp_path):
    target = make_engine(f"sqlite:///{(tmp_path / 'populated.db').as_posix()}")
    try:
        Base.metadata.create_all(target)
        with target.begin() as connection:
            connection.execute(Shop.__table__.insert().values(shop_id='existing', shop_name='Do not replace'))
        with pytest.raises(MigrationError, match='not empty'):
            copy_tables(store['engine'], target)
        with target.connect() as connection:
            assert connection.execute(select(Shop.__table__.c.shop_name)).scalar_one() == 'Do not replace'
    finally:
        target.dispose()
