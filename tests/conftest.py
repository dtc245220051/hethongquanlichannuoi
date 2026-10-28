import pytest

from livestock_app import create_app


@pytest.fixture
def app():
    return create_app(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
            "TESTING": True,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()
