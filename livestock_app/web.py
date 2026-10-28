import hmac
import secrets
from datetime import date, datetime, time, timedelta, timezone
from functools import wraps
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from werkzeug.exceptions import RequestEntityTooLarge
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from livestock_app.agent import AgentError, AgentToolError, FarmAgent
from livestock_app.agent.runtime import validate_conversation_history
from livestock_app.extensions import db
from livestock_app.models import (
    Alert,
    AlertEvent,
    Animal,
    AuditLog,
    Barn,
    DiseaseRiskAssessment,
    Farm,
    FarmMembership,
    PlatformAdmin,
    FeedInventoryBalance,
    FeedItem,
    FeedPlan,
    FeedTransaction,
    GrowthPrediction,
    Herd,
    HerdEvent,
    HealthRecord,
    Species,
    User,
    VaccinationOutcome,
    VaccinationSchedule,
    WeightRecord,
)
from livestock_app.services import (
    AuthorizationError,
    NotFoundError,
    ServiceError,
    ValidationError,
    require_farm_member,
    utc_now,
)
from livestock_app.services.ai import (
    DiseaseRiskService,
    FeedPlanService,
    GrowthPredictionService,
    WeightRecordService,
)
from livestock_app.services.ai_analysis import (
    AIAnalysisError,
    ai_service_from_config,
    ai_service_status,
)
from livestock_app.services.analytics import ALL_SYMPTOMS, RISK_LABELS
from livestock_app.services.feed import FeedInventoryService
from livestock_app.services.livestock import LivestockService
from livestock_app.services.members import FarmMemberService
from livestock_app.services.workflows import (
    AlertService,
    BarnService,
    HealthRecordService,
    VaccinationService,
)

web_blueprint = Blueprint("web", __name__)


def _stable_display_labels(records, fallback_label, value_label):
    labels = {}
    for index, record in enumerate(sorted(records, key=lambda row: row.id), start=1):
        labels[record.id] = value_label(record) or f"{fallback_label} #{index:02d}"
    return labels


def _farm_display_labels(farm_id: str) -> dict[str, dict[str, str]]:
    barns = db.session.scalars(
        select(Barn).where(Barn.farm_id == farm_id).order_by(Barn.id)
    ).all()
    herds = db.session.scalars(
        select(Herd).where(Herd.farm_id == farm_id).order_by(Herd.id)
    ).all()
    animals = db.session.scalars(
        select(Animal).where(Animal.farm_id == farm_id).order_by(Animal.id)
    ).all()
    schedules = db.session.scalars(
        select(VaccinationSchedule)
        .where(VaccinationSchedule.farm_id == farm_id)
        .order_by(VaccinationSchedule.id)
    ).all()
    users = db.session.scalars(
        select(User)
        .join(FarmMembership, FarmMembership.user_id == User.id)
        .where(FarmMembership.farm_id == farm_id)
        .order_by(User.id)
    ).all()
    species_names = {
        species.code: species.name_vi
        for species in db.session.scalars(select(Species)).all()
    }

    barn_labels = _stable_display_labels(
        barns, "Chuồng", lambda barn: barn.name
    )
    herd_labels = _stable_display_labels(
        herds, "Đàn/lô", lambda herd: herd.name
    )

    def animal_name(animal: Animal) -> str | None:
        species_name = species_names.get(animal.species_code)
        if animal.display_name and animal.identifier:
            return f"{animal.display_name} · {animal.identifier}"
        if animal.display_name:
            return animal.display_name
        if animal.identifier:
            return f"{species_name} {animal.identifier}" if species_name else animal.identifier
        return None

    animal_labels = _stable_display_labels(animals, "Vật nuôi", animal_name)
    schedule_labels = {
        schedule.id: f"Lịch tiêm phòng số {index:02d}"
        for index, schedule in enumerate(schedules, start=1)
    }
    user_labels = _stable_display_labels(
        users,
        "Người dùng",
        lambda user: user.display_name or user.email,
    )
    return {
        "barns": barn_labels,
        "herds": herd_labels,
        "animals": animal_labels,
        "schedules": schedule_labels,
        "users": user_labels,
    }


def _audit_entity_labels(
    farm_id: str,
    rows: list[AuditLog],
    display_labels: dict[str, dict[str, str]],
) -> dict[tuple[str, str], str]:
    existing_labels = {
        "animal": display_labels["animals"],
        "herd": display_labels["herds"],
        "barn": display_labels["barns"],
        "vaccination_schedule": display_labels["schedules"],
    }
    labels = {
        (entity_type, entity_id): label
        for entity_type, entity_labels in existing_labels.items()
        for entity_id, label in entity_labels.items()
    }
    related_entities = {
        "alert": (Alert, "Cảnh báo", lambda alert: alert.title),
        "feed_item": (FeedItem, "Loại thức ăn", lambda item: item.name),
        "feed_transaction": (
            FeedTransaction,
            "Giao dịch thức ăn",
            lambda transaction: (
                f"Giao dịch thức ăn · {transaction.transaction_type}"
            ),
        ),
        "health_record": (
            HealthRecord,
            "Hồ sơ sức khỏe",
            lambda record: (
                display_labels["animals"].get(record.animal_id)
                if record.animal_id
                else display_labels["herds"].get(record.herd_id)
            ),
        ),
        "vaccination_outcome": (
            VaccinationOutcome,
            "Kết quả tiêm phòng",
            lambda outcome: display_labels["schedules"].get(outcome.schedule_id),
        ),
    }
    for entity_type, (model, fallback, value_label) in related_entities.items():
        entity_ids = {
            row.entity_id
            for row in rows
            if row.entity_type == entity_type and row.entity_id
        }
        if not entity_ids:
            continue
        records = db.session.scalars(
            select(model).where(
                model.farm_id == farm_id,
                model.id.in_(entity_ids),
            )
        ).all()
        entity_labels = _stable_display_labels(records, fallback, value_label)
        labels.update(
            {
                (entity_type, entity_id): label
                for entity_id, label in entity_labels.items()
            }
        )
    return labels


def _safe_next(value: str | None) -> str | None:
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not urlsplit(value).scheme
        and not urlsplit(value).netloc
    ):
        return value
    return None


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@web_blueprint.before_request
def load_request_context():
    if request.method == "POST":
        supplied = (
            request.headers.get("X-CSRF-Token", "")
            if request.is_json
            else request.form.get("_csrf_token", "")
        )
        expected = session.get("_csrf_token", "")
        if not isinstance(expected, str) or not hmac.compare_digest(supplied, expected):
            if request.is_json:
                return jsonify(
                    {
                        "ok": False,
                        "error": "Phiên biểu mẫu đã hết hạn. Tải lại trang rồi thử lại.",
                    }
                ), 400
            abort(400, description="Yêu cầu không hợp lệ hoặc phiên biểu mẫu đã hết hạn.")

    g.user = None
    g.farm = None
    g.membership = None
    if request.endpoint == "web.login":
        return None

    user_id = session.get("user_id")
    if not isinstance(user_id, str):
        if request.endpoint == "web.assistant_message":
            return jsonify(
                {"ok": False, "error": "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."}
            ), 401
        return redirect(url_for("web.login", next=request.full_path))


    user = db.session.get(User, user_id)
    if user is None or user.status != "active":
        session.clear()
        if request.endpoint == "web.assistant_message":
            return jsonify(
                {"ok": False, "error": "Phiên đăng nhập không còn hợp lệ. Vui lòng đăng nhập lại."}
            ), 401
        return redirect(url_for("web.login"))

    memberships = db.session.execute(
        select(FarmMembership, Farm)
        .join(Farm, Farm.id == FarmMembership.farm_id)
        .where(
            FarmMembership.user_id == user.id,
            Farm.status == "active",
        )
        .order_by(Farm.name, Farm.id)
    ).all()
    requested_farm_id = session.get("farm_id")
    selected = next(
        (
            (membership, farm)
            for membership, farm in memberships
            if membership.farm_id == requested_farm_id
        ),
        memberships[0] if memberships else None,
    )
    if selected is None:
        session.clear()
        if request.endpoint == "web.assistant_message":
            return jsonify(
                {"ok": False, "error": "Tài khoản chưa có trang trại đang hoạt động."}
            ), 403
        return redirect(url_for("web.login"))
    g.user = user
    g.membership, g.farm = selected
    session["farm_id"] = g.farm.id
    if g.membership.role == "veterinarian":
        allowed_endpoints = {
            "web.livestock",
            "web.livestock_detail",
            "web.health_records",
            "web.vaccinations",
            "web.disease_risk",
            "web.disease_risk_detail",
            "web.growth",
            "web.feed_plans",
            "web.feed_plan_detail",
            "web.assistant",
            "web.assistant_message",
            "web.logout",
        }
        if request.endpoint not in allowed_endpoints:
            abort(403)
        if request.method != "GET" and request.endpoint not in {
            "web.assistant_message",
            "web.logout",
        }:
            abort(403)
    return None


@web_blueprint.app_context_processor
def inject_csrf_token():
    return {"csrf_token": _csrf_token}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("web.login"))
        return view(*args, **kwargs)

    return wrapped


def owner_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.membership.role != "owner":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _farm_allowed(permission: str) -> bool:
    try:
        require_farm_member(
            db.session,
            g.farm.id,
            g.user.id,
            permissions=(permission,),
        )
        return True
    except AuthorizationError:
        return False


def _parse_local_datetime(value: str) -> datetime:
    try:
        local_value = datetime.fromisoformat(value)
        if local_value.tzinfo is not None:
            return local_value.astimezone(timezone.utc)
        try:
            zone = ZoneInfo(g.farm.timezone)
        except ZoneInfoNotFoundError:
            current_app.logger.error("Invalid configured timezone for farm %s", g.farm.id)
            raise ValidationError("Múi giờ trang trại chưa được cấu hình hợp lệ.") from None
        return local_value.replace(tzinfo=zone).astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValidationError("Vui lòng nhập ngày giờ hợp lệ.") from None


def _local_datetime(value: datetime) -> str:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return normalized.astimezone(ZoneInfo(g.farm.timezone)).strftime("%d/%m/%Y %H:%M")


def _local_datetime_input(value: datetime) -> str:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return normalized.astimezone(ZoneInfo(g.farm.timezone)).strftime("%Y-%m-%dT%H:%M")


def _integer_field(name: str, label: str) -> int:
    try:
        value = int(request.form.get(name, ""))
    except (TypeError, ValueError):
        raise ValidationError(f"{label} phải là số nguyên.") from None
    return value


def _service_error(exc: ServiceError, endpoint: str) -> None:
    db.session.rollback()
    flash(str(exc), "error")


def _ai_client():
    return ai_service_from_config(current_app.config)


def _integrity_error() -> None:
    db.session.rollback()
    current_app.logger.warning("Rejected a web form due to a database integrity constraint.")
    flash("Dữ liệu bị trùng hoặc không còn hợp lệ. Vui lòng kiểm tra và thử lại.", "error")


@web_blueprint.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = db.session.scalar(
        select(User).where(func.lower(User.email) == email)
    )

    valid = (
        user is not None
        and check_password_hash(user.password_hash, password)
        and user.status == "active"
    )

    if not valid:
        flash("Thông tin đăng nhập không hợp lệ.", "error")
        return render_template("login.html"), 401

    membership = db.session.execute(
        select(FarmMembership, Farm)
        .join(Farm, Farm.id == FarmMembership.farm_id)
        .where(
            FarmMembership.user_id == user.id,
            Farm.status == "active",
        )
        .order_by(Farm.name, Farm.id)
    ).first()

    if membership is None:
        flash(
            "Tài khoản chưa được cấp quyền trang trại.",
            "error",
        )
        return render_template("login.html"), 401

    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["farm_id"] = membership[1].id
    _csrf_token()

    default_endpoint = (
        "web.livestock"
        if membership[0].role == "veterinarian"
        else "web.dashboard"
    )

    return redirect(
        _safe_next(request.args.get("next"))
        or url_for(default_endpoint)
    )


@web_blueprint.get("/admin")
@login_required
def admin_dashboard():
    if (
        db.session.scalar(
            select(PlatformAdmin).where(PlatformAdmin.user_id == g.user.id)
        )
        is None
    ):
        abort(403)

    return render_template("admin_dashboard.html")


@web_blueprint.post("/logout")
@login_required
def logout():
    session.clear()
    flash("Bạn đã đăng xuất.", "success")
    return redirect(url_for("web.login"))



@web_blueprint.get("/")
@login_required
def dashboard():
    farm_id = g.farm.id
    barn_count = db.session.scalar(
        select(func.count(Barn.id)).where(Barn.farm_id == farm_id, Barn.status == "active")
    ) or 0
    active_herds = db.session.scalars(
        select(Herd).where(Herd.farm_id == farm_id, Herd.status == "active")
    ).all()
    herd_ids = [herd.id for herd in active_herds]
    identified_animals = db.session.scalar(
        select(func.count(Animal.id)).where(
            Animal.farm_id == farm_id, Animal.status == "active"
        )
    ) or 0
    total_heads = LivestockService(db.session).farm_head_count(
        farm_id=farm_id, actor_user_id=g.user.id
    )
    feed_total = db.session.scalar(
        select(func.coalesce(func.sum(FeedInventoryBalance.quantity_kg), 0)).where(
            FeedInventoryBalance.farm_id == farm_id
        )
    )
    now = utc_now()
    soon = now + timedelta(days=7)
    administered_schedules = select(VaccinationOutcome.schedule_id).where(
        VaccinationOutcome.farm_id == farm_id,
        VaccinationOutcome.outcome == "administered",
    )
    overdue_vaccinations = db.session.scalar(
        select(func.count(VaccinationSchedule.id)).where(
            VaccinationSchedule.farm_id == farm_id,
            VaccinationSchedule.status == "scheduled",
            VaccinationSchedule.due_at < now,
            ~VaccinationSchedule.id.in_(administered_schedules),
        )
    ) or 0
    upcoming_vaccinations = db.session.scalar(
        select(func.count(VaccinationSchedule.id)).where(
            VaccinationSchedule.farm_id == farm_id,
            VaccinationSchedule.status == "scheduled",
            VaccinationSchedule.due_at >= now,
            VaccinationSchedule.due_at <= soon,
            ~VaccinationSchedule.id.in_(administered_schedules),
        )
    ) or 0
    open_alerts = db.session.scalar(
        select(func.count(Alert.id)).where(
            Alert.farm_id == farm_id, Alert.status != "closed"
        )
    ) or 0
    recent_herd_events = db.session.scalars(
        select(HerdEvent)
        .where(HerdEvent.farm_id == farm_id)
        .order_by(HerdEvent.occurred_at.desc())
        .limit(8)
    ).all()
    return render_template(
        "dashboard.html",
        barn_count=barn_count,
        herd_count=len(active_herds),
        identified_animals=identified_animals,
        total_heads=total_heads,
        feed_total=feed_total,
        overdue_vaccinations=overdue_vaccinations,
        upcoming_vaccinations=upcoming_vaccinations,
        open_alerts=open_alerts,
        recent_events=recent_herd_events,
        herd_ids=herd_ids,
    )


@web_blueprint.route("/assistant", methods=["GET", "POST"])
@login_required
def assistant():
    mode = current_app.config.get("AGENT_MODE", "disabled")
    status = FarmAgent.status(
        app_env=current_app.config["APP_ENV"],
        mode=mode,
        gemini_api_key=current_app.config.get("GEMINI_API_KEY", ""),
        gemini_model=current_app.config.get("GEMINI_MODEL", ""),
    )
    result = None
    error = None
    if request.method == "POST":
        question = request.form.get("question", "")
        try:
            result = FarmAgent(
                db.session,
                app_env=current_app.config["APP_ENV"],
                mode=mode,
                gemini_api_key=current_app.config.get("GEMINI_API_KEY", ""),
                gemini_model=current_app.config.get("GEMINI_MODEL", ""),
            ).ask(
                question,
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
            )
            if result.tool_calls:
                db.session.commit()
        except (AgentError, AgentToolError) as exc:
            db.session.rollback()
            error = str(exc)
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.error("Agent database query failed.")
            error = "Không thể xử lý yêu cầu lúc này. Vui lòng thử lại sau."
    return render_template(
        "assistant.html",
        status=status,
        result=result,
        error=error,
        question=(
            request.form.get("question", "")[:2000]
            if request.method == "POST"
            else ""
        ),
    )


@web_blueprint.post("/assistant/messages")
@login_required
def assistant_message():
    request.max_content_length = 48_000
    if request.content_length is not None and request.content_length > 48_000:
        return jsonify({"ok": False, "error": "Nội dung hội thoại vượt giới hạn."}), 413
    try:
        payload = request.get_json(silent=True)
    except RequestEntityTooLarge:
        return jsonify({"ok": False, "error": "Nội dung hội thoại vượt giới hạn."}), 413
    if not isinstance(payload, dict) or set(payload) != {"message", "history"}:
        return jsonify({"ok": False, "error": "Yêu cầu hội thoại không hợp lệ."}), 400
    question = payload["message"]
    history = payload["history"]
    if not isinstance(question, str) or not question.strip():
        return jsonify({"ok": False, "error": "Vui lòng nhập câu hỏi."}), 400
    if len(question) > 2000:
        return jsonify({"ok": False, "error": "Câu hỏi vượt giới hạn 2.000 ký tự."}), 400
    if not isinstance(history, list):
        return jsonify({"ok": False, "error": "Lịch sử hội thoại không hợp lệ."}), 400
    try:
        history = validate_conversation_history(history)
    except AgentError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    mode = current_app.config.get("AGENT_MODE", "disabled")
    try:
        result = FarmAgent(
            db.session,
            app_env=current_app.config["APP_ENV"],
            mode=mode,
            gemini_api_key=current_app.config.get("GEMINI_API_KEY", ""),
            gemini_model=current_app.config.get("GEMINI_MODEL", ""),
        ).ask(
            question,
            farm_id=g.farm.id,
            actor_user_id=g.user.id,
            conversation_history=history,
        )
        if result.tool_calls:
            db.session.commit()
    except (AgentError, AgentToolError) as exc:
        db.session.rollback()
        current_app.logger.warning("Assistant request rejected: %s", str(exc)[:300])
        return jsonify({"ok": False, "error": str(exc)}), 503
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.error("Assistant database query failed.")
        return jsonify(
            {
                "ok": False,
                "error": "Không thể truy vấn dữ liệu lúc này. Vui lòng thử lại sau.",
            }
        ), 500

    return jsonify(
        {
            "ok": True,
            "message": {
                "role": "assistant",
                "content": result.answer,
                "mode_label": result.mode_label,
                "observed_at": result.observed_at,
                "data_scope": result.data_scope,
                "tool_calls": [
                    {
                        "name": call.name,
                        "description": call.description,
                        "row_count": call.row_count,
                        "data": call.data,
                    }
                    for call in result.tool_calls
                ],
            },
        }
    )


@web_blueprint.route("/barns", methods=["GET", "POST"])
@login_required
def barns():
    if request.method == "POST":
        if g.membership.role not in {"owner", "manager"}:
            abort(403)
        try:
            BarnService(db.session).create(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                name=request.form.get("name", ""),
                capacity_heads=_integer_field("capacity_heads", "Sức chứa"),
                location_note=request.form.get("location_note") or None,
            )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.barns")
        else:
            db.session.commit()
            flash("Đã tạo chuồng.", "success")
        return redirect(url_for("web.barns"))
    search = request.args.get("q", "").strip()
    query = select(Barn).where(Barn.farm_id == g.farm.id)
    if search:
        query = query.where(Barn.name.ilike(f"%{search}%"))
    barn_rows = db.session.scalars(query.order_by(Barn.name)).all()
    display_labels = _farm_display_labels(g.farm.id)
    service = BarnService(db.session)
    occupancy = {barn.id: service.occupancy(farm_id=g.farm.id, barn_id=barn.id) for barn in barn_rows}
    return render_template(
        "barns.html",
        barns=barn_rows,
        occupancy=occupancy,
        search=search,
        display_labels=display_labels,
    )


@web_blueprint.route("/barns/<barn_id>", methods=["GET", "POST"])
@login_required
def barn_detail(barn_id: str):
    barn = db.session.scalar(
        select(Barn).where(Barn.farm_id == g.farm.id, Barn.id == barn_id)
    )
    if barn is None:
        abort(404)
    if request.method == "POST":
        if g.membership.role not in {"owner", "manager"}:
            abort(403)
        try:
            BarnService(db.session).update(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                barn_id=barn.id,
                name=request.form.get("name", ""),
                capacity_heads=_integer_field("capacity_heads", "Sức chứa"),
                location_note=request.form.get("location_note") or None,
            )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.barn_detail")
        else:
            db.session.commit()
            flash("Đã cập nhật thông tin chuồng.", "success")
        return redirect(url_for("web.barn_detail", barn_id=barn.id))
    current_occupancy = BarnService(db.session).occupancy(
        farm_id=g.farm.id, barn_id=barn.id
    )
    herds = db.session.scalars(
        select(Herd).where(Herd.farm_id == g.farm.id, Herd.barn_id == barn.id)
    ).all()
    animals = db.session.scalars(
        select(Animal).where(
            Animal.farm_id == g.farm.id,
            Animal.barn_id == barn.id,
            Animal.herd_id.is_(None),
            Animal.status == "active",
        )
    ).all()
    display_labels = _farm_display_labels(g.farm.id)
    return render_template(
        "barn_detail.html",
        barn=barn,
        occupancy=current_occupancy,
        herds=herds,
        animals=animals,
        display_labels=display_labels,
    )


@web_blueprint.route("/livestock", methods=["GET", "POST"])
@login_required
def livestock():
    if request.method == "POST":
        service = LivestockService(db.session)
        action = request.form.get("action")
        try:
            if action == "create_herd":
                service.create_herd(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    name=request.form.get("name", ""),
                    species_code=request.form.get("species_code", ""),
                    heads=_integer_field("heads", "Số lượng"),
                    event_type=request.form.get("event_type", "acquisition"),
                    reason=request.form.get("reason", ""),
                    barn_id=request.form.get("barn_id") or None,
                )
            elif action == "update_herd":
                service.update_herd(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    herd_id=request.form.get("herd_id", ""),
                    name=request.form.get("name", ""),
                )
            elif action == "create_animal":
                service.create_animal(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    species_code=request.form.get("species_code", ""),
                    identifier=request.form.get("identifier") or None,
                    display_name=request.form.get("display_name") or None,
                    herd_id=request.form.get("herd_id") or None,
                    barn_id=request.form.get("barn_id") or None,
                    event_type=request.form.get("event_type", "acquisition"),
                    reason=request.form.get("reason", ""),
                )
            elif action == "update_animal":
                service.update_animal(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    animal_id=request.form.get("animal_id", ""),
                    identifier=request.form.get("identifier") or None,
                    display_name=request.form.get("display_name") or None,
                )
            elif action == "identify":
                service.identify_unidentified_animal(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    herd_id=request.form.get("herd_id", ""),
                    identifier=request.form.get("identifier", ""),
                    display_name=request.form.get("display_name") or None,
                    reason=request.form.get("reason", ""),
                )
            elif action == "adjust_count":
                delta = _integer_field("delta", "Mức thay đổi")
                service.adjust_unidentified_count(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    herd_id=request.form.get("herd_id", ""),
                    delta=delta,
                    event_type=request.form.get("event_type", ""),
                    reason=request.form.get("reason", ""),
                )
            elif action == "move_herd":
                service.move_herd(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    herd_id=request.form.get("herd_id", ""),
                    target_barn_id=request.form.get("barn_id") or None,
                    reason=request.form.get("reason", ""),
                )
            elif action == "move_animal":
                service.move_animal(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    animal_id=request.form.get("animal_id", ""),
                    target_barn_id=request.form.get("barn_id") or None,
                    reason=request.form.get("reason", ""),
                )
            elif action == "split_herd":
                service.split_herd(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    source_herd_id=request.form.get("source_herd_id", ""),
                    target_name=request.form.get("target_name", ""),
                    heads=_integer_field("heads", "Số lượng tách"),
                    target_barn_id=request.form.get("target_barn_id") or None,
                    reason=request.form.get("reason", ""),
                )
            elif action == "merge_herds":
                service.merge_herds(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    source_herd_id=request.form.get("source_herd_id", ""),
                    target_herd_id=request.form.get("target_herd_id", ""),
                    reason=request.form.get("reason", ""),
                )
            elif action == "animal_status":
                service.change_animal_status(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    animal_id=request.form.get("animal_id", ""),
                    status=request.form.get("status", ""),
                    reason=request.form.get("reason", ""),
                )
            else:
                raise ValidationError("Thao tác vật nuôi không được hỗ trợ.")
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.livestock")
        else:
            db.session.commit()
            flash("Đã lưu thay đổi vật nuôi và lịch sử nghiệp vụ.", "success")
        return redirect(url_for("web.livestock"))

    search = request.args.get("q", "").strip()
    species = db.session.scalars(select(Species).where(Species.active.is_(True)).order_by(Species.name_vi)).all()
    barns_rows = db.session.scalars(
        select(Barn).where(Barn.farm_id == g.farm.id, Barn.status == "active").order_by(Barn.name)
    ).all()
    barn_filter = request.args.get("barn_id", "")
    species_filter = request.args.get("species_code", "")
    status_filter = request.args.get("status", "active")
    herds_query = select(Herd).where(Herd.farm_id == g.farm.id)
    animals_query = select(Animal).where(Animal.farm_id == g.farm.id)
    if status_filter in {"active", "inactive", "closed"}:
        herds_query = herds_query.where(Herd.status == status_filter)
    else:
        herds_query = herds_query.where(Herd.id.is_(None))
    if status_filter in {"active", "inactive", "deceased", "sold"}:
        animals_query = animals_query.where(Animal.status == status_filter)
    else:
        animals_query = animals_query.where(Animal.id.is_(None))
    if barn_filter:
        herds_query = herds_query.where(Herd.barn_id == barn_filter)
        animals_query = animals_query.where(Animal.barn_id == barn_filter)
    if species_filter:
        herds_query = herds_query.where(Herd.species_code == species_filter)
        animals_query = animals_query.where(Animal.species_code == species_filter)
    if search:
        herds_query = herds_query.where(Herd.name.ilike(f"%{search}%"))
        animals_query = animals_query.where(
            or_(
                Animal.identifier.ilike(f"%{search}%"),
                Animal.display_name.ilike(f"%{search}%"),
            )
        )
    herds_rows = db.session.scalars(herds_query.order_by(Herd.name)).all()
    animals_rows = db.session.scalars(animals_query.order_by(Animal.identifier)).all()
    display_labels = _farm_display_labels(g.farm.id)
    return render_template(
        "livestock.html",
        species=species,
        barns=barns_rows,
        herds=herds_rows,
        animals=animals_rows,
        search=search,
        barn_filter=barn_filter,
        species_filter=species_filter,
        status_filter=status_filter,
        display_labels=display_labels,
    )


@web_blueprint.get("/livestock/<entity_type>/<entity_id>")
@login_required
def livestock_detail(entity_type: str, entity_id: str):
    if entity_type == "herds":
        entity = db.session.scalar(
            select(Herd).where(Herd.farm_id == g.farm.id, Herd.id == entity_id)
        )
        if entity is None:
            abort(404)
        event_query = select(HerdEvent).where(
            HerdEvent.farm_id == g.farm.id,
            or_(HerdEvent.source_herd_id == entity.id, HerdEvent.target_herd_id == entity.id),
        )
        health_query = select(HealthRecord).where(
            HealthRecord.farm_id == g.farm.id, HealthRecord.herd_id == entity.id
        )
        schedule_query = select(VaccinationSchedule).where(
            VaccinationSchedule.farm_id == g.farm.id,
            VaccinationSchedule.herd_id == entity.id,
        )
        kind_label = "Đàn/lô"
    elif entity_type == "animals":
        entity = db.session.scalar(
            select(Animal).where(Animal.farm_id == g.farm.id, Animal.id == entity_id)
        )
        if entity is None:
            abort(404)
        event_query = select(HerdEvent).where(
            HerdEvent.farm_id == g.farm.id, HerdEvent.animal_id == entity.id
        )
        health_query = select(HealthRecord).where(
            HealthRecord.farm_id == g.farm.id, HealthRecord.animal_id == entity.id
        )
        schedule_query = select(VaccinationSchedule).where(
            VaccinationSchedule.farm_id == g.farm.id,
            VaccinationSchedule.animal_id == entity.id,
        )
        kind_label = "Cá thể"
    else:
        abort(404)
    events = db.session.scalars(event_query.order_by(HerdEvent.occurred_at.desc())).all()
    health = db.session.scalars(health_query.order_by(HealthRecord.recorded_at.desc())).all()
    schedules = db.session.scalars(
        schedule_query.order_by(VaccinationSchedule.due_at.desc())
    ).all()
    display_labels = _farm_display_labels(g.farm.id)
    return render_template(
        "livestock_detail.html",
        entity=entity,
        entity_type=entity_type,
        kind_label=kind_label,
        events=events,
        health=health,
        schedules=schedules,
        display_labels=display_labels,
    )


@web_blueprint.route("/feed", methods=["GET", "POST"])
@login_required
def feed():
    if request.method == "POST":
        try:
            feed_service = FeedInventoryService(db.session)
            if request.form.get("action") == "create_item":
                feed_service.create_item(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    name=request.form.get("name", ""),
                    low_stock_threshold_kg=request.form.get("threshold_kg") or None,
                )
            else:
                feed_service.record_transaction(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    feed_item_id=request.form.get("feed_item_id", ""),
                    transaction_type=request.form.get("transaction_type", ""),
                    quantity_kg=request.form.get("quantity_kg", ""),
                    unit_code=request.form.get("unit_code", "kg"),
                    reason=request.form.get("reason") or None,
                    herd_id=request.form.get("herd_id") or None,
                )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.feed")
        else:
            db.session.commit()
            flash("Đã ghi giao dịch kho thức ăn.", "success")
        return redirect(url_for("web.feed"))

    items = db.session.scalars(
        select(FeedItem)
        .where(FeedItem.farm_id == g.farm.id)
        .order_by(FeedItem.name)
    ).all()
    balances = {
        row.feed_item_id: row.quantity_kg
        for row in db.session.scalars(
            select(FeedInventoryBalance).where(FeedInventoryBalance.farm_id == g.farm.id)
        ).all()
    }
    transactions = db.session.scalars(
        select(FeedTransaction)
        .where(FeedTransaction.farm_id == g.farm.id)
        .order_by(FeedTransaction.occurred_at.desc())
        .limit(100)
    ).all()
    herds_rows = db.session.scalars(
        select(Herd).where(Herd.farm_id == g.farm.id, Herd.status == "active").order_by(Herd.name)
    ).all()
    return render_template(
        "feed.html",
        items=items,
        balances=balances,
        transactions=transactions,
        herds=herds_rows,
        can_receipt=g.membership.role in {"owner", "manager"} or _farm_allowed("feed_stock_in"),
        can_adjust=g.membership.role in {"owner", "manager"} or _farm_allowed("feed_adjust"),
    )


@web_blueprint.route("/health-records", methods=["GET", "POST"])
@login_required
def health_records():
    if request.method == "POST":
        try:
            follow_up = request.form.get("follow_up_at", "").strip()
            HealthRecordService(db.session).record(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                observations=request.form.get("observations", ""),
                animal_id=request.form.get("animal_id") or None,
                herd_id=request.form.get("herd_id") or None,
                action_taken=request.form.get("action_taken") or None,
                follow_up_at=_parse_local_datetime(follow_up) if follow_up else None,
            )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.health_records")
        else:
            db.session.commit()
            flash("Đã ghi nhận nhật ký sức khỏe.", "success")
        return redirect(url_for("web.health_records"))
    records = db.session.scalars(
        select(HealthRecord)
        .where(HealthRecord.farm_id == g.farm.id)
        .order_by(HealthRecord.recorded_at.desc())
        .limit(200)
    ).all()
    herds_rows = db.session.scalars(
        select(Herd).where(Herd.farm_id == g.farm.id, Herd.status == "active").order_by(Herd.name)
    ).all()
    animals_rows = db.session.scalars(
        select(Animal).where(Animal.farm_id == g.farm.id, Animal.status == "active").order_by(Animal.identifier)
    ).all()
    display_labels = _farm_display_labels(g.farm.id)
    return render_template(
        "health_records.html",
        records=records,
        herds=herds_rows,
        animals=animals_rows,
        local_datetime=_local_datetime,
        display_labels=display_labels,
    )


@web_blueprint.route("/vaccinations", methods=["GET", "POST"])
@login_required
def vaccinations():
    if request.method == "POST":
        action = request.form.get("action")
        if action in {"create_schedule", "update_schedule"} and g.membership.role not in {"owner", "manager"}:
            abort(403)
        workflow = VaccinationService(db.session)
        try:
            if action == "create_schedule":
                workflow.create_schedule(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    vaccine_name=request.form.get("vaccine_name", ""),
                    due_at=_parse_local_datetime(request.form.get("due_at", "")),
                    animal_id=request.form.get("animal_id") or None,
                    herd_id=request.form.get("herd_id") or None,
                )
            elif action == "update_schedule":
                workflow.update_schedule(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    schedule_id=request.form.get("schedule_id", ""),
                    vaccine_name=request.form.get("vaccine_name", ""),
                    due_at=_parse_local_datetime(request.form.get("due_at", "")),
                    status=request.form.get("status", ""),
                )
            elif action == "record_outcome":
                occurred = request.form.get("occurred_at", "").strip()
                workflow.record_outcome(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    schedule_id=request.form.get("schedule_id", ""),
                    outcome=request.form.get("outcome", ""),
                    occurred_at=_parse_local_datetime(occurred) if occurred else None,
                    notes=request.form.get("notes") or None,
                )
            else:
                raise ValidationError("Thao tác lịch tiêm không được hỗ trợ.")
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.vaccinations")
        else:
            db.session.commit()
            flash("Đã lưu lịch hoặc kết quả tiêm riêng biệt.", "success")
        return redirect(url_for("web.vaccinations"))

    schedules = db.session.scalars(
        select(VaccinationSchedule)
        .where(VaccinationSchedule.farm_id == g.farm.id)
        .order_by(VaccinationSchedule.due_at)
    ).all()
    outcomes = db.session.scalars(
        select(VaccinationOutcome)
        .where(VaccinationOutcome.farm_id == g.farm.id)
        .order_by(VaccinationOutcome.id.desc())
        .limit(100)
    ).all()
    all_outcomes = db.session.scalars(
        select(VaccinationOutcome).where(
            VaccinationOutcome.farm_id == g.farm.id
        )
    ).all()
    outcome_by_schedule = {outcome.schedule_id: outcome.outcome for outcome in all_outcomes}
    outcome_schedule_ids = set(outcome_by_schedule)
    herds_rows = db.session.scalars(
        select(Herd).where(Herd.farm_id == g.farm.id, Herd.status == "active").order_by(Herd.name)
    ).all()
    animals_rows = db.session.scalars(
        select(Animal).where(Animal.farm_id == g.farm.id, Animal.status == "active").order_by(Animal.identifier)
    ).all()
    display_labels = _farm_display_labels(g.farm.id)
    now = utc_now()
    return render_template(
        "vaccinations.html",
        schedules=schedules,
        outcomes=outcomes,
        outcome_schedule_ids=outcome_schedule_ids,
        outcome_by_schedule=outcome_by_schedule,
        herds=herds_rows,
        animals=animals_rows,
        now=now,
        local_datetime=_local_datetime,
        local_datetime_input=_local_datetime_input,
        display_labels=display_labels,
    )


@web_blueprint.get("/alerts")
@login_required
def alerts():
    status = request.args.get("status", "")
    from_value = request.args.get("from", "")
    to_value = request.args.get("to", "")
    query = select(Alert).where(Alert.farm_id == g.farm.id)
    if status in {"new", "acknowledged", "in_progress", "closed"}:
        query = query.where(Alert.status == status)
    try:
        zone = ZoneInfo(g.farm.timezone)
        if from_value:
            start_day = date.fromisoformat(from_value)
            query = query.where(
                Alert.created_at >= datetime.combine(start_day, time.min, zone).astimezone(timezone.utc)
            )
        if to_value:
            end_day = date.fromisoformat(to_value) + timedelta(days=1)
            query = query.where(
                Alert.created_at < datetime.combine(end_day, time.min, zone).astimezone(timezone.utc)
            )
        if from_value and to_value and date.fromisoformat(from_value) > date.fromisoformat(to_value):
            raise ValueError
    except (ValueError, ZoneInfoNotFoundError):
        flash("Khoảng ngày lọc không hợp lệ.", "error")
        from_value = ""
        to_value = ""
        query = select(Alert).where(Alert.farm_id == g.farm.id)
        if status in {"new", "acknowledged", "in_progress", "closed"}:
            query = query.where(Alert.status == status)
    alert_rows = db.session.scalars(query.order_by(Alert.created_at.desc())).all()
    return render_template(
        "alerts.html",
        alerts=alert_rows,
        selected_status=status,
        from_value=from_value,
        to_value=to_value,
    )


@web_blueprint.route("/alerts/<alert_id>", methods=["GET", "POST"])
@login_required
def alert_detail(alert_id: str):
    alert = db.session.scalar(
        select(Alert).where(Alert.farm_id == g.farm.id, Alert.id == alert_id)
    )
    if alert is None:
        abort(404)
    if request.method == "POST":
        if request.form.get("action") == "close" and g.membership.role not in {"owner", "manager"}:
            abort(403)
        try:
            AlertService(db.session).record_action(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                alert_id=alert.id,
                action=request.form.get("action", ""),
                note=request.form.get("note") or None,
                reason=request.form.get("reason") or None,
            )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.alert_detail")
        else:
            db.session.commit()
            flash("Đã cập nhật lịch sử cảnh báo.", "success")
        return redirect(url_for("web.alert_detail", alert_id=alert.id))
    events = db.session.scalars(
        select(AlertEvent)
        .where(AlertEvent.farm_id == g.farm.id, AlertEvent.alert_id == alert.id)
        .order_by(AlertEvent.occurred_at.desc())
    ).all()
    display_labels = _farm_display_labels(g.farm.id)
    return render_template(
        "alert_detail.html",
        alert=alert,
        events=events,
        display_labels=display_labels,
    )


@web_blueprint.get("/audit")
@owner_required
def audit():
    rows = db.session.scalars(
        select(AuditLog)
        .where(AuditLog.farm_id == g.farm.id)
        .order_by(AuditLog.occurred_at.desc())
        .limit(200)
    ).all()
    display_labels = _farm_display_labels(g.farm.id)
    audit_entity_labels = _audit_entity_labels(
        g.farm.id,
        rows,
        display_labels,
    )
    audit_labels = {}
    for index, row in enumerate(rows, start=1):
        label = audit_entity_labels.get((row.entity_type, row.entity_id))
        if label is None and row.entity_id:
            label = f"{row.entity_type.replace('_', ' ').capitalize()} #{index:02d}"
        audit_labels[row.id] = label or "—"
    return render_template(
        "audit.html",
        rows=rows,
        audit_labels=audit_labels,
    )


@web_blueprint.route("/members", methods=["GET", "POST"])
@owner_required
def members():
    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")
        if password != password_confirmation:
            flash("Mật khẩu xác nhận không khớp.", "error")
            return redirect(url_for("web.members"))
        try:
            FarmMemberService(db.session).create_member(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                role=request.form.get("role", "care_staff"),
                email=request.form.get("email", ""),
                display_name=request.form.get("display_name", ""),
                password=password,
            )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.members")
        else:
            db.session.commit()
            flash("Đã tạo tài khoản nhân viên cho trang trại này.", "success")
        return redirect(url_for("web.members"))

    rows = db.session.execute(
        select(FarmMembership, User)
        .join(User, User.id == FarmMembership.user_id)
        .where(FarmMembership.farm_id == g.farm.id)
        .order_by(FarmMembership.role, User.email)
    ).all()
    return render_template("members.html", members=rows)


def _active_animals(farm_id: str) -> list[Animal]:
    return list(
        db.session.scalars(
            select(Animal)
            .where(Animal.farm_id == farm_id, Animal.status == "active")
            .order_by(Animal.identifier, Animal.display_name)
        ).all()
    )


def _symptom_labels() -> list[tuple[str, str]]:
    return [(key, meta["label"]) for key, meta in ALL_SYMPTOMS.items()]


@web_blueprint.route("/disease-risk", methods=["GET", "POST"])
@login_required
def disease_risk():
    service = DiseaseRiskService(db.session)
    if request.method == "POST":
        try:
            assessment = service.assess(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                animal_id=request.form.get("animal_id", ""),
                symptoms=request.form.getlist("symptoms"),
            )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.disease_risk")
        else:
            db.session.commit()
            flash("Đã lưu kết quả phân tích nguy cơ bệnh.", "success")
            return redirect(
                url_for("web.disease_risk_detail", assessment_id=assessment.id)
            )
        return redirect(url_for("web.disease_risk"))

    assessments = service.history(farm_id=g.farm.id)
    animals = _active_animals(g.farm.id)
    display_labels = _farm_display_labels(g.farm.id)
    return render_template(
        "disease_risk.html",
        animals=animals,
        assessments=assessments,
        display_labels=display_labels,
        local_datetime=_local_datetime,
        risk_labels=RISK_LABELS,
        symptom_labels=_symptom_labels(),
        can_write=g.membership.role != "veterinarian",
    )


@web_blueprint.get("/disease-risk/<assessment_id>")
@login_required
def disease_risk_detail(assessment_id: str):
    try:
        assessment = DiseaseRiskService(db.session).get(
            farm_id=g.farm.id, assessment_id=assessment_id
        )
    except NotFoundError:
        abort(404)
    animal = db.session.scalar(
        select(Animal).where(
            Animal.farm_id == g.farm.id, Animal.id == assessment.animal_id
        )
    )
    display_labels = _farm_display_labels(g.farm.id)
    symptom_map = {key: meta["label"] for key, meta in ALL_SYMPTOMS.items()}
    return render_template(
        "disease_risk_detail.html",
        assessment=assessment,
        animal=animal,
        display_labels=display_labels,
        local_datetime=_local_datetime,
        risk_labels=RISK_LABELS,
        symptom_map=symptom_map,
    )


def _growth_chart_data(weight_records, forecasts=None):
    if not weight_records:
        return [], []
    origin = weight_records[0].measured_at
    points = [
        {
            "x": round(
                (record.measured_at - origin).total_seconds() / 86400.0, 2
            ),
            "y": round(float(record.weight_kg), 2),
            "label": _local_datetime(record.measured_at),
        }
        for record in weight_records
    ]
    last_day = (
        weight_records[-1].measured_at - origin
    ).total_seconds() / 86400.0
    chart_forecast = [
        {
            "x": round(last_day + horizon, 2),
            "y": round(value, 2),
            "label": f"+{horizon} ngày",
        }
        for horizon, value in sorted((forecasts or {}).items())
    ]
    return points, chart_forecast


@web_blueprint.route("/growth", methods=["GET", "POST"])
@login_required
def growth():
    if request.method == "POST":
        action = request.form.get("action")
        animal_id = request.form.get("animal_id", "").strip()
    else:
        action = None
        animal_id = request.args.get("animal_id", "").strip()
    if request.method == "POST":
        try:
            if action == "add_weight":
                WeightRecordService(db.session).add(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    animal_id=animal_id,
                    weight_kg=request.form.get("weight_kg", ""),
                    measured_at=_parse_local_datetime(
                        request.form.get("measured_at", "")
                    ),
                    notes=request.form.get("notes") or None,
                )
            elif action in {"analyze_growth", "save_prediction"}:
                _, _result, prediction = GrowthPredictionService(db.session).analyze(
                    farm_id=g.farm.id,
                    actor_user_id=g.user.id,
                    animal_id=animal_id,
                    save=True,
                    ai_client=_ai_client(),
                )
            else:
                raise ValidationError("Thao tác dự đoán tăng trưởng không được hỗ trợ.")
        except IntegrityError:
            _integrity_error()
        except AIAnalysisError as exc:
            db.session.rollback()
            current_app.logger.warning(
                "AI growth analysis failed: %s", str(exc)[:300]
            )
            flash(str(exc), "error")
        except ServiceError as exc:
            _service_error(exc, "web.growth")
        else:
            db.session.commit()
            if action == "add_weight":
                flash("Đã lưu lần cân mới.", "success")
            else:
                if prediction.method.startswith("local-"):
                    flash(
                        "Gemini tạm thời không khả dụng. Hệ thống đã dùng mô hình "
                        "dự phòng nội bộ từ lịch sử cân nặng; kết quả không phải "
                        "do Gemini tạo.",
                        "warning",
                    )
                else:
                    flash(
                        "AI đã phân tích và lưu kết quả dự báo tăng trưởng.",
                        "success",
                    )
                return redirect(
                    url_for(
                        "web.growth_prediction_detail",
                        prediction_id=prediction.id,
                    )
                )
        return redirect(url_for("web.growth", animal_id=animal_id))

    animals = _active_animals(g.farm.id)
    selected_animal = None
    weight_records: list[WeightRecord] = []
    predictions = []
    chart_points: list[dict] = []
    chart_forecast: list[dict] = []
    if animal_id:
        selected_animal = db.session.scalar(
            select(Animal).where(
                Animal.farm_id == g.farm.id, Animal.id == animal_id
            )
        )
        if selected_animal is None:
            flash("Không tìm thấy vật nuôi trong trang trại của bạn.", "error")
        else:
            weight_records = WeightRecordService(db.session).history(
                farm_id=g.farm.id, animal_id=selected_animal.id
            )
            predictions = GrowthPredictionService(db.session).history(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                animal_id=selected_animal.id,
            )
            chart_points, chart_forecast = _growth_chart_data(weight_records)
    species_names = {
        species.code: species.name_vi
        for species in db.session.scalars(select(Species)).all()
    }
    return render_template(
        "growth.html",
        animals=animals,
        selected_animal=selected_animal,
        selected_animal_id=animal_id,
        weight_records=weight_records,
        predictions=predictions,
        chart_points=chart_points,
        chart_forecast=chart_forecast,
        display_labels=_farm_display_labels(g.farm.id),
        species_names=species_names,
        local_datetime=_local_datetime,
        local_datetime_input=_local_datetime_input,
        now=utc_now(),
        can_write=g.membership.role != "veterinarian",
        ai_status=ai_service_status(current_app.config),
    )


@web_blueprint.get("/growth/predictions/<prediction_id>")
@login_required
def growth_prediction_detail(prediction_id: str):
    service = GrowthPredictionService(db.session)
    try:
        prediction = service.get(
            farm_id=g.farm.id,
            actor_user_id=g.user.id,
            prediction_id=prediction_id,
        )
    except NotFoundError:
        abort(404)
    animal = db.session.scalar(
        select(Animal).where(
            Animal.farm_id == g.farm.id, Animal.id == prediction.animal_id
        )
    )
    weight_records = WeightRecordService(db.session).history(
        farm_id=g.farm.id, animal_id=prediction.animal_id
    )
    forecasts = {}
    if isinstance(prediction.predicted, dict):
        raw = prediction.predicted.get("horizons", prediction.predicted)
        if isinstance(raw, dict):
            for key, value in raw.items():
                try:
                    horizon = int(key)
                    forecasts[horizon] = float(value)
                except (TypeError, ValueError):
                    continue
    chart_points, chart_forecast = _growth_chart_data(weight_records, forecasts)
    limitations = [
        line.strip()
        for line in (prediction.limitations or "").splitlines()
        if line.strip()
    ]
    return render_template(
        "growth_detail.html",
        prediction=prediction,
        animal=animal,
        weight_records=weight_records,
        forecasts=forecasts,
        limitations=limitations,
        chart_points=chart_points,
        chart_forecast=chart_forecast,
        display_labels=_farm_display_labels(g.farm.id),
        local_datetime=_local_datetime,
    )


def _parse_feed_items_from_form() -> list[dict]:
    names = request.form.getlist("feed_name")
    units = request.form.getlist("unit")
    prices = request.form.getlist("price_per_unit")
    amounts = request.form.getlist("amount_per_day")
    notes = request.form.getlist("item_notes")
    items: list[dict] = []
    row_count = max(len(names), len(units), len(prices), len(amounts), len(notes))
    for index in range(row_count):
        name = names[index] if index < len(names) else ""
        amount = amounts[index] if index < len(amounts) else ""
        if not name.strip() and not amount.strip():
            continue
        items.append(
            {
                "feed_name": name,
                "unit": units[index] if index < len(units) else "kg",
                "price_per_unit": prices[index] if index < len(prices) else "",
                "amount_per_day": amount,
                "notes": notes[index] if index < len(notes) else "",
            }
        )
    return items


@web_blueprint.route("/feed-plans", methods=["GET", "POST"])
@login_required
def feed_plans():
    service = FeedPlanService(db.session)
    if request.method == "POST":
        try:
            period_raw = request.form.get("period_days", "")
            try:
                period_days = int(period_raw)
            except (TypeError, ValueError):
                raise ValidationError("Số ngày của kỳ phải là số nguyên.") from None
            plan, _result = service.create(
                farm_id=g.farm.id,
                actor_user_id=g.user.id,
                animal_id=request.form.get("animal_id") or None,
                herd_id=request.form.get("herd_id") or None,
                name=request.form.get("name", ""),
                period_days=period_days,
                notes=request.form.get("notes") or None,
                items=_parse_feed_items_from_form(),
            )
        except IntegrityError:
            _integrity_error()
        except ServiceError as exc:
            _service_error(exc, "web.feed_plans")
        else:
            db.session.commit()
            flash("Đã lưu phương án khẩu phần.", "success")
            return redirect(url_for("web.feed_plan_detail", plan_id=plan.id))
        return redirect(url_for("web.feed_plans"))

    plans = service.list(farm_id=g.farm.id)
    summaries = {}
    for plan in plans:
        items = service.items(farm_id=g.farm.id, plan_id=plan.id)
        try:
            summaries[plan.id] = service.summarize(plan, items)
        except ValueError:
            summaries[plan.id] = None
    animals = _active_animals(g.farm.id)
    herds = db.session.scalars(
        select(Herd)
        .where(Herd.farm_id == g.farm.id, Herd.status == "active")
        .order_by(Herd.name)
    ).all()
    return render_template(
        "feed_plans.html",
        plans=plans,
        summaries=summaries,
        animals=animals,
        herds=herds,
        display_labels=_farm_display_labels(g.farm.id),
        local_datetime=_local_datetime,
        can_write=g.membership.role != "veterinarian",
        ai_status=ai_service_status(current_app.config),
    )


@web_blueprint.post("/feed-plans/suggest")
@login_required
def feed_plan_suggest():
    service = FeedPlanService(db.session)
    try:
        period_days = _integer_field("period_days", "Số ngày của kỳ")
        plan, _result = service.suggest(
            farm_id=g.farm.id,
            actor_user_id=g.user.id,
            animal_id=request.form.get("animal_id") or None,
            herd_id=request.form.get("herd_id") or None,
            period_days=period_days,
            goal=request.form.get("goal", "growth"),
            ai_client=_ai_client(),
        )
    except IntegrityError:
        _integrity_error()
    except AIAnalysisError as exc:
        db.session.rollback()
        current_app.logger.warning("AI feed suggestion failed: %s", str(exc)[:300])
        flash(str(exc), "error")
    except ServiceError as exc:
        _service_error(exc, "web.feed_plans")
    else:
        db.session.commit()
        flash("AI đã tạo đề xuất khẩu phần. Vui lòng xem xét trước khi chấp nhận.", "success")
        return redirect(url_for("web.feed_plan_detail", plan_id=plan.id))
    return redirect(url_for("web.feed_plans"))


@web_blueprint.post("/feed-plans/<plan_id>/accept")
@login_required
def feed_plan_accept(plan_id: str):
    try:
        FeedPlanService(db.session).accept(
            farm_id=g.farm.id,
            actor_user_id=g.user.id,
            plan_id=plan_id,
        )
    except NotFoundError:
        abort(404)
    except IntegrityError:
        _integrity_error()
    except ServiceError as exc:
        _service_error(exc, "web.feed_plan_detail")
    else:
        db.session.commit()
        flash("Đã chấp nhận đề xuất khẩu phần AI.", "success")
    return redirect(url_for("web.feed_plan_detail", plan_id=plan_id))


@web_blueprint.post("/feed-plans/<plan_id>/manual-adjust")
@login_required
def feed_plan_manual_adjust(plan_id: str):
    service = FeedPlanService(db.session)
    try:
        source = service.get(farm_id=g.farm.id, plan_id=plan_id)
        period_days = _integer_field("period_days", "Số ngày của kỳ")
        notes = request.form.get("notes") or ""
        prefixed_notes = f"Điều chỉnh thủ công từ đề xuất AI {source.id}. {notes}"[:1000]
        plan, _result = service.create(
            farm_id=g.farm.id,
            actor_user_id=g.user.id,
            animal_id=source.animal_id,
            herd_id=source.herd_id,
            name=request.form.get("name", "")[:160],
            period_days=period_days,
            notes=prefixed_notes,
            items=_parse_feed_items_from_form(),
        )
    except NotFoundError:
        abort(404)
    except IntegrityError:
        _integrity_error()
    except ServiceError as exc:
        _service_error(exc, "web.feed_plan_detail")
    else:
        db.session.commit()
        flash("Đã lưu bản điều chỉnh thủ công; bản đề xuất AI giữ nguyên.", "success")
        return redirect(url_for("web.feed_plan_detail", plan_id=plan.id))
    return redirect(url_for("web.feed_plan_detail", plan_id=plan_id))


@web_blueprint.get("/feed-plans/<plan_id>")
@login_required
def feed_plan_detail(plan_id: str):
    service = FeedPlanService(db.session)
    try:
        plan = service.get(farm_id=g.farm.id, plan_id=plan_id)
    except NotFoundError:
        abort(404)
    items = service.items(farm_id=g.farm.id, plan_id=plan_id)
    try:
        summary = service.summarize(plan, items)
    except ValueError:
        summary = None
    animal = None
    herd = None
    if plan.animal_id:
        animal = db.session.scalar(
            select(Animal).where(
                Animal.farm_id == g.farm.id, Animal.id == plan.animal_id
            )
        )
    if plan.herd_id:
        herd = db.session.scalar(
            select(Herd).where(Herd.farm_id == g.farm.id, Herd.id == plan.herd_id)
        )
    limitations = [
        line.strip()
        for line in (plan.limitations or "").splitlines()
        if line.strip()
    ]
    return render_template(
        "feed_plan_detail.html",
        plan=plan,
        items=items,
        summary=summary,
        animal=animal,
        herd=herd,
        limitations=limitations,
        display_labels=_farm_display_labels(g.farm.id),
        local_datetime=_local_datetime,
        can_write=g.membership.role != "veterinarian",
        ai_status=ai_service_status(current_app.config),
    )


@web_blueprint.app_errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="Bạn không có quyền thực hiện thao tác này."), 403


@web_blueprint.app_errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="Không tìm thấy dữ liệu trong phạm vi trang trại của bạn."), 404


@web_blueprint.app_errorhandler(400)
def bad_request(_error):
    return render_template("error.html", code=400, message="Yêu cầu không hợp lệ. Vui lòng tải lại trang và thử lại."), 400



def register_cli(app):
    @app.cli.command("grant-platform-admin")
    @click.argument("email")
    def grant_platform_admin(email):
        """Grant global administrator access to an existing account."""
        from livestock_app.models import PlatformAdmin

        normalized_email = email.strip().lower()

        user = db.session.scalar(
            select(User).where(func.lower(User.email) == normalized_email)
        )

        if user is None:
            raise click.ClickException("Không tìm thấy tài khoản này.")

        if user.status != "active":
            raise click.ClickException("Tài khoản đang bị vô hiệu hóa.")

        existing = db.session.scalar(
            select(PlatformAdmin).where(PlatformAdmin.user_id == user.id)
        )

        if existing:
            click.echo("Tài khoản này đã có quyền admin.")
            return

        db.session.add(PlatformAdmin(user_id=user.id))
        db.session.commit()

        click.echo(f"Đã cấp quyền admin cho {user.email}.")

    @app.cli.command("provision-owner")
    @click.option("--email", prompt=True)
    @click.option("--display-name", prompt="Tên chủ trang trại")
    @click.option("--farm-name", prompt="Tên trang trại")
    def provision_owner(email: str, display_name: str, farm_name: str) -> None:
        """Create an initial farm and owner without a default credential."""
        normalized_email = email.strip().lower()

        if not normalized_email or len(normalized_email) > 254:
            raise click.ClickException("Email không hợp lệ.")

        if db.session.scalar(
            select(User.id).where(func.lower(User.email) == normalized_email)
        ):
            raise click.ClickException("Email đã tồn tại.")

        if db.session.scalar(select(Farm.id).limit(1)) is not None:
            raise click.ClickException(
                "Trang trại đã tồn tại; lệnh này chỉ khởi tạo farm/owner đầu tiên."
            )

        password = click.prompt(
            "Mật khẩu",
            hide_input=True,
            confirmation_prompt=True,
        )

        if len(password) < 12:
            raise click.ClickException("Mật khẩu phải có ít nhất 12 ký tự.")

        if not display_name.strip() or len(display_name.strip()) > 160:
            raise click.ClickException(
                "Tên hiển thị phải từ 1 đến 160 ký tự."
            )

        if not farm_name.strip() or len(farm_name.strip()) > 160:
            raise click.ClickException(
                "Tên trang trại phải từ 1 đến 160 ký tự."
            )

        farm = Farm(name=farm_name.strip())
        user = User(
            email=normalized_email,
            password_hash=generate_password_hash(password),
            display_name=display_name.strip(),
            status="active",
        )

        db.session.add_all([farm, user])
        db.session.flush()

        db.session.add(
            FarmMembership(
                farm_id=farm.id,
                user_id=user.id,
                role="owner",
            )
        )

        db.session.commit()
        click.echo(
            "Đã tạo trang trại và chủ sở hữu. "
            "Không hiển thị lại mật khẩu."
        )

    @app.cli.command("seed-demo")
    @click.option(
        "--confirm-database-name",
        required=True,
        help="Tên database demo phải khớp chính xác với DATABASE_URL.",
    )
    def seed_demo(confirm_database_name: str) -> None:
        """Create synthetic demo data only after explicit database verification."""
        from livestock_app.demo_seed import (
            DEMO_DATABASE_ATTESTATION,
            DEMO_OWNER_EMAIL,
            DemoSeedError,
            seed_synthetic_demo,
            validate_demo_database,
        )

        if current_app.config.get("APP_ENV") != "demo":
            raise click.ClickException(
                "Lệnh seed chỉ chạy khi APP_ENV=demo."
            )

        if current_app.config.get("AGENT_MODE") != "mock":
            raise click.ClickException(
                "Lệnh seed demo chỉ chạy khi AGENT_MODE=mock."
            )

        if (
            current_app.config.get("DEMO_DATABASE_ATTESTATION")
            != DEMO_DATABASE_ATTESTATION
        ):
            raise click.ClickException(
                "Thiếu xác nhận operator về database demo riêng, không production."
            )

        try:
            validate_demo_database(
                db.session,
                app_env=current_app.config["APP_ENV"],
                database_url=current_app.config["SQLALCHEMY_DATABASE_URI"],
                configured_database_name=current_app.config.get(
                    "DEMO_DATABASE_NAME", ""
                ),
                confirmed_database_name=confirm_database_name,
                expected_server_uuid=current_app.config.get(
                    "DEMO_DATABASE_SERVER_UUID", ""
                ),
                observed_attestation=current_app.config.get(
                    "DEMO_DATABASE_ATTESTATION", ""
                ),
            )
        except DemoSeedError as exc:
            raise click.ClickException(str(exc)) from None

        password = click.prompt(
            "Mật khẩu cho tài khoản demo",
            hide_input=True,
            confirmation_prompt=True,
        )

        try:
            result = seed_synthetic_demo(
                db.session,
                app_env=current_app.config["APP_ENV"],
                testing=current_app.testing,
                database_url=current_app.config["SQLALCHEMY_DATABASE_URI"],
                configured_database_name=current_app.config.get(
                    "DEMO_DATABASE_NAME", ""
                ),
                confirmed_database_name=confirm_database_name,
                expected_server_uuid=current_app.config.get(
                    "DEMO_DATABASE_SERVER_UUID", ""
                ),
                observed_attestation=current_app.config.get(
                    "DEMO_DATABASE_ATTESTATION", ""
                ),
                agent_mode=current_app.config.get("AGENT_MODE", "disabled"),
                owner_password=password,
            )
        except DemoSeedError as exc:
            raise click.ClickException(str(exc)) from None

        if result.created:
            click.echo(
                "Đã tạo dữ liệu tổng hợp. Đăng nhập bằng email "
                f"{DEMO_OWNER_EMAIL}; mật khẩu chỉ bạn vừa nhập. "
                f"Dataset: {result.dataset_marker}."
            )
        else:
            click.echo(
                "Dataset tổng hợp đã tồn tại và được kiểm tra; "
                "không có dữ liệu thay đổi."
            )
