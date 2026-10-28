import os
from typing import Any

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is missing or unsafe."""


_APP_ENVIRONMENTS = {"development", "test", "demo", "production"}
_AGENT_MODES = {"disabled", "mock", "provider", "gemini"}
_TEST_DATABASE_DRIVERS = {"sqlite", "sqlite+pysqlite", "mysql+pymysql"}


def _database_uri_from_environment() -> str | URL:
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        return database_url

    fields = {
        "DATABASE_HOST": os.environ.get("DATABASE_HOST", ""),
        "DATABASE_PORT": os.environ.get("DATABASE_PORT", ""),
        "DATABASE_NAME": os.environ.get("DATABASE_NAME", ""),
        "DATABASE_USER": os.environ.get("DATABASE_USER", ""),
        "DATABASE_PASSWORD": os.environ.get("DATABASE_PASSWORD", ""),
    }
    if not any(fields.values()):
        return ""

    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise ConfigurationError(
            "Database connection settings are incomplete: "
            + ", ".join(missing)
            + "."
        )

    try:
        port = int(fields["DATABASE_PORT"])
    except ValueError as exc:
        raise ConfigurationError("DATABASE_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ConfigurationError("DATABASE_PORT must be between 1 and 65535.")

    return URL.create(
        "mysql+pymysql",
        username=fields["DATABASE_USER"],
        password=fields["DATABASE_PASSWORD"],
        host=fields["DATABASE_HOST"],
        port=port,
        database=fields["DATABASE_NAME"],
    )


def load_config() -> dict[str, Any]:
    return {
        "APP_ENV": os.environ.get("APP_ENV", ""),
        "SECRET_KEY": os.environ.get("SECRET_KEY", ""),
        "SQLALCHEMY_DATABASE_URI": _database_uri_from_environment(),
        "AGENT_MODE": os.environ.get("AGENT_MODE", "disabled"),
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", ""),
        "DEMO_DATABASE_NAME": os.environ.get("DEMO_DATABASE_NAME", ""),
        "DEMO_DATABASE_SERVER_UUID": os.environ.get(
            "DEMO_DATABASE_SERVER_UUID", ""
        ),
        "DEMO_DATABASE_ATTESTATION": os.environ.get(
            "DEMO_DATABASE_ATTESTATION", ""
        ),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "TESTING": False,
        "DEBUG": False,
    }


def validate_config(config: dict[str, Any]) -> None:
    app_env = config.get("APP_ENV")
    if app_env not in _APP_ENVIRONMENTS:
        raise ConfigurationError(
            "APP_ENV must be explicitly set to development, test, demo, or production."
        )

    agent_mode = config.get("AGENT_MODE", "disabled")
    if agent_mode not in _AGENT_MODES:
        raise ConfigurationError(
            "AGENT_MODE must be disabled, mock, provider, or gemini."
        )
    if agent_mode == "mock" and app_env not in {"test", "demo"}:
        raise ConfigurationError("AGENT_MODE=mock is only allowed in test or demo.")

    if not config.get("SECRET_KEY"):
        raise ConfigurationError("SECRET_KEY must be configured outside source control.")

    database_uri = config.get("SQLALCHEMY_DATABASE_URI")
    if not isinstance(database_uri, (str, URL)) or not database_uri:
        raise ConfigurationError("DATABASE_URL must be configured.")

    try:
        database_url = make_url(database_uri)
    except (ArgumentError, ValueError) as exc:
        raise ConfigurationError("DATABASE_URL is not a valid SQLAlchemy URL.") from exc

    if app_env == "test":
        if database_url.drivername not in _TEST_DATABASE_DRIVERS:
            raise ConfigurationError(
                "Test DATABASE_URL must use SQLite or the configured MySQL driver."
            )
        config["TESTING"] = True
        return

    if database_url.drivername != "mysql+pymysql":
        raise ConfigurationError(
            "Non-test environments require a MySQL DATABASE_URL using PyMySQL."
        )
