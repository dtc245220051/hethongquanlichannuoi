import pytest
from sqlalchemy.engine import make_url

from livestock_app import ConfigurationError, create_app, db, migrate


def test_factory_registers_database_and_migration_extensions(app):
    assert app.extensions["sqlalchemy"] is db
    assert app.extensions["migrate"] is not None
    assert migrate.db is db
    assert app.config["TESTING"] is True


def test_liveness_endpoint_does_not_claim_database_readiness(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_missing_runtime_database_url_fails_closed(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in (
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="APP_ENV must be explicitly set"):
        create_app()


def test_database_environment_components_encode_reserved_password_characters(
    monkeypatch,
):
    password = "test:@/;?#[]%&+ password"
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("SECRET_KEY", "test-only-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_HOST", "db")
    monkeypatch.setenv("DATABASE_PORT", "3306")
    monkeypatch.setenv("DATABASE_NAME", "livestock_demo_test")
    monkeypatch.setenv("DATABASE_USER", "livestock_app")
    monkeypatch.setenv("DATABASE_PASSWORD", password)

    app = create_app()
    with app.app_context():
        database_url = db.engine.url

    assert database_url.drivername == "mysql+pymysql"
    assert database_url.host == "db"
    assert database_url.port == 3306
    assert database_url.database == "livestock_demo_test"
    assert database_url.password == password
    assert make_url(database_url).password == password
    assert password not in str(database_url)


def test_partial_database_environment_reports_missing_variable_names_only(
    monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in (
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_PASSWORD", "test-only-password")

    with pytest.raises(ConfigurationError) as error:
        create_app()

    assert "DATABASE_HOST" in str(error.value)
    assert "DATABASE_PASSWORD" not in str(error.value)
    assert "test-only-password" not in str(error.value)


def test_non_test_environment_rejects_sqlite():
    with pytest.raises(ConfigurationError, match="require a MySQL DATABASE_URL"):
        create_app(
            {
                "APP_ENV": "development",
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
            }
        )


def test_non_test_environment_rejects_missing_secret():
    with pytest.raises(ConfigurationError, match="SECRET_KEY must be configured"):
        create_app(
            {
                "APP_ENV": "development",
                "SECRET_KEY": "",
                "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://localhost/livestock",
            }
        )


def test_non_test_environment_uses_mysql_pymysql_driver_without_connecting():
    app = create_app(
        {
            "APP_ENV": "development",
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://localhost/livestock",
        }
    )

    with app.app_context():
        assert db.engine.dialect.name == "mysql"
        assert db.engine.dialect.driver == "pymysql"


def test_test_environment_accepts_explicit_sqlite_database():
    app = create_app(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
        }
    )

    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite+pysqlite:///:memory:"


def test_mock_agent_mode_is_restricted_to_test_and_demo():
    with pytest.raises(ConfigurationError, match="only allowed in test or demo"):
        create_app(
            {
                "APP_ENV": "development",
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://localhost/livestock",
                "AGENT_MODE": "mock",
            }
        )

    with pytest.raises(ConfigurationError, match="AGENT_MODE must be"):
        create_app(
            {
                "APP_ENV": "test",
                "SECRET_KEY": "test-only-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
                "AGENT_MODE": "unrestricted",
            }
        )


def test_gemini_mode_can_start_unconfigured_and_fails_closed():
    app = create_app(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
            "AGENT_MODE": "gemini",
            "GEMINI_API_KEY": "",
            "GEMINI_MODEL": "",
        }
    )

    from livestock_app.agent import FarmAgent

    status = FarmAgent.status(
        app_env=app.config["APP_ENV"],
        mode=app.config["AGENT_MODE"],
        gemini_api_key=app.config["GEMINI_API_KEY"],
        gemini_model=app.config["GEMINI_MODEL"],
    )
    assert status["available"] is False
    assert status["label"] == "CHƯA CẤU HÌNH"
    assert "Không có yêu cầu nào được gửi" in status["message"]


def test_gemini_environment_values_are_loaded_server_side(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SECRET_KEY", "test-only-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AGENT_MODE", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")

    app = create_app()

    assert app.config["GEMINI_API_KEY"] == "test-only-key"
    assert app.config["GEMINI_MODEL"] == "gemini-test-model"


def test_production_session_cookie_security_is_enforced():
    app = create_app(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "test-only-production-config-secret",
            "SQLALCHEMY_DATABASE_URI": "mysql+pymysql://localhost/livestock",
            "SESSION_COOKIE_SECURE": False,
            "SESSION_COOKIE_HTTPONLY": False,
            "SESSION_COOKIE_SAMESITE": "None",
        }
    )

    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
