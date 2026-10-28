from flask import Flask

from livestock_app.config import ConfigurationError, load_config, validate_config
from livestock_app.extensions import db, migrate
from livestock_app.health import health_blueprint
from livestock_app import models as _models
from livestock_app.web import register_cli, web_blueprint


def create_app(config_override: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(load_config())

    if config_override is not None:
        app.config.update(config_override)

    validate_config(app.config)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if app.config.get("APP_ENV") == "production":
        app.config["SESSION_COOKIE_SECURE"] = True
    else:
        app.config.setdefault("SESSION_COOKIE_SECURE", False)
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", 60 * 60 * 8)
    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    app.register_blueprint(health_blueprint)
    app.register_blueprint(web_blueprint)
    register_cli(app)

    return app


__all__ = ["ConfigurationError", "create_app", "db", "migrate"]
