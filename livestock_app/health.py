from flask import Blueprint, Response, jsonify

health_blueprint = Blueprint("health", __name__)


@health_blueprint.get("/health/live")
def live() -> tuple[Response, int]:
    return jsonify(status="ok"), 200
