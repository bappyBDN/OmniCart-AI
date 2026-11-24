from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from models import Base


def normalize_url(value):
    url = make_url(value)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    return url


def make_engine(value):
    url = normalize_url(value)
    sqlite = url.get_backend_name() == "sqlite"
    options = {"pool_pre_ping": True, "hide_parameters": True}
    if sqlite:
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    elif url.get_backend_name() == "postgresql":
        options.update(connect_args={"connect_timeout": 8, "application_name": "omnichat"},
                       pool_size=5, max_overflow=10, pool_recycle=1800)
    engine = create_engine(url, **options)
    if sqlite:
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
    return engine


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def transaction():
    with SessionLocal() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def initialize():
    import backend.records  # noqa: F401: register only additive tables
    Base.metadata.create_all(engine)
