"""Deployment configuration tests; no network or real database connections."""
import pytest
from sqlalchemy.engine import make_url

from backend.config import ROOT, database_url_from_env
from backend.database import normalize_url
from backend import serve


def test_local_development_keeps_existing_sqlite_default():
    assert database_url_from_env({}) == f"sqlite:///{(ROOT / 'ecommerce_saas.db').as_posix()}"
    assert database_url_from_env({"DATABASE_URL": " "}) == database_url_from_env({})


@pytest.mark.parametrize("flag", ["RENDER", "DATABASE_REQUIRE_POSTGRES"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_hosted_deployment_never_silently_falls_back_to_sqlite(flag, value):
    env = {flag: "true"}
    if value is not None:
        env["DATABASE_URL"] = value
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        database_url_from_env(env)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_render_connection_url_selects_psycopg_and_preserves_tls(scheme):
    value = f"{scheme}://user:encoded%40password@internal-host/render_db?sslmode=require"
    url = normalize_url(database_url_from_env({"RENDER": "true", "DATABASE_URL": value}))
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "encoded@password"
    assert url.database == "render_db"
    assert url.query["sslmode"] == "require"


@pytest.mark.parametrize("value", ["sqlite:////app/ecommerce_saas.db", "mysql://user:password@db/shop"])
def test_render_rejects_non_postgres_even_if_explicit_flag_is_false(value):
    with pytest.raises(RuntimeError, match="PostgreSQL is required"):
        database_url_from_env({"RENDER": "true", "DATABASE_REQUIRE_POSTGRES": "false", "DATABASE_URL": value})


def test_compose_password_components_are_url_encoded_safely():
    password = "special@:/?#% +quotes'\""
    url = make_url(database_url_from_env({
        "DATABASE_REQUIRE_POSTGRES": "true", "DATABASE_URL": "",
        "POSTGRES_HOST": "db", "POSTGRES_PASSWORD": password,
    }))
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "db"
    assert url.port == 5432
    assert url.database == "omnichat"
    assert url.username == "omnicart"
    assert url.password == password


def test_explicit_database_url_takes_precedence_over_compose_components():
    value = "postgresql://user:password@existing-db/existing_shop"
    assert database_url_from_env({
        "DATABASE_REQUIRE_POSTGRES": "true", "DATABASE_URL": value,
        "POSTGRES_HOST": "db", "POSTGRES_PASSWORD": "other-password",
    }) == value


def test_database_config_errors_do_not_echo_credentials():
    with pytest.raises(RuntimeError) as error:
        database_url_from_env({"RENDER": "true", "DATABASE_URL": "postgresql://user:sensitive-password@host:bad/db"})
    assert "DATABASE_URL is invalid" in str(error.value)
    assert "sensitive-password" not in str(error.value)
    assert error.value.__suppress_context__


def test_compose_components_require_password():
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD is required"):
        database_url_from_env({"POSTGRES_HOST": "db"})


def test_compose_components_reject_invalid_port():
    with pytest.raises(RuntimeError, match="POSTGRES_PORT"):
        database_url_from_env({"POSTGRES_HOST": "db", "POSTGRES_PASSWORD": "password", "POSTGRES_PORT": "bad"})


@pytest.mark.parametrize("port, expected", [(None, 8000), ("10000", 10000), ("8080", 8080)])
def test_container_start_honors_platform_port(monkeypatch, port, expected):
    monkeypatch.delenv("PORT", raising=False)
    if port is not None:
        monkeypatch.setenv("PORT", port)
    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    serve.main()
    assert calls == [(("backend.main:app",), {"host": "0.0.0.0", "port": expected, "workers": 1})]


@pytest.mark.parametrize("port", ["", "bad", "0", "65536", "-1"])
def test_container_start_rejects_invalid_port(monkeypatch, port):
    monkeypatch.setenv("PORT", port)
    with pytest.raises(SystemExit, match="PORT must be"):
        serve.main()
