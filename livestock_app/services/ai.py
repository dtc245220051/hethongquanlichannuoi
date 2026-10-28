"""Farm-scoped services for the UC013, UC014 and UC015 features."""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session, scoped_session

from livestock_app.models import (
    Animal,
    DiseaseRiskAssessment,
    FeedInventoryBalance,
    FeedItem,
    FeedPlan,
    FeedPlanItem,
    FeedTransaction,
    GrowthPrediction,
    Herd,
    HerdEvent,
    Species,
    WeightRecord,
)
from livestock_app.services import (
    NotFoundError,
    ValidationError,
    atomic,
    record_audit,
    require_farm_member,
    require_farm_operator,
    utc_now,
)
from livestock_app.services.ai_analysis import (
    AIAnalysisError,
    AIProviderUnavailableError,
    FeedAIResult,
    GrowthAIResult,
    GrowthForecast,
    StructuredAIClient,
)
from livestock_app.services.analytics import (
    analyze_growth,
    assess_disease_risk,
    calculate_feed_plan,
    normalize_symptoms,
)

logger = logging.getLogger(__name__)

_MAX_WEIGHT_KG = Decimal("9999999.999")
_LOCAL_GROWTH_METHOD = "local-growth-fallback-v1"
_LOCAL_GROWTH_MODEL = "local-linear-regression-v1"
_LOCAL_GROWTH_DEDUPE_MINUTES = 10


def _session(session: Session | scoped_session) -> Session:
    return session() if isinstance(session, scoped_session) else session


def _require_animal(session: Session, farm_id: str, animal_id: str) -> Animal:
    animal = session.scalar(
        select(Animal).where(Animal.farm_id == farm_id, Animal.id == animal_id)
    )
    if animal is None:
        raise NotFoundError("Không tìm thấy vật nuôi trong trang trại của bạn.")
    return animal


def _animal_label(session: Session, animal: Animal) -> str:
    species = session.scalar(
        select(Species).where(Species.code == animal.species_code)
    )
    species_name = species.name_vi if species else animal.species_code
    if animal.display_name and animal.identifier:
        return f"{animal.display_name} · {animal.identifier}"
    if animal.display_name:
        return animal.display_name
    if animal.identifier:
        return f"{species_name} {animal.identifier}"
    return f"{species_name}"


def _decimal_weight(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError("Cân nặng không hợp lệ.")
    try:
        weight = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Cân nặng phải là số hợp lệ.") from None
    if not weight.is_finite() or weight <= 0:
        raise ValidationError("Cân nặng phải lớn hơn 0.")
    if weight > _MAX_WEIGHT_KG:
        raise ValidationError("Cân nặng vượt giới hạn cho phép.")
    if weight.quantize(Decimal("0.001")) != weight:
        raise ValidationError("Cân nặng tối đa 3 chữ số thập phân.")
    return weight


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _latest_weight(
    session: Session, *, farm_id: str, animal_id: str
) -> WeightRecord | None:
    return session.scalar(
        select(WeightRecord)
        .where(
            WeightRecord.farm_id == farm_id,
            WeightRecord.animal_id == animal_id,
        )
        .order_by(WeightRecord.measured_at.desc())
        .limit(1)
    )


def _species_name(session: Session, code: str) -> str:
    species = session.scalar(select(Species).where(Species.code == code))
    return species.name_vi if species else code


def _feed_species_conflict(feed_name: str, species_code: str) -> bool:
    name = (feed_name or "").casefold()
    code = (species_code or "").casefold()
    if code in {"cattle", "buffalo", "bovine", "bò", "bo"}:
        markers = ("gia cầm", "gà", "vịt", "ngan", "ngỗng", "heo", "lợn", "pig", "poultry")
    elif code in {"pig", "swine", "heo", "lợn", "lon"}:
        markers = ("bò", "trâu", "gia cầm", "gà", "vịt", "cattle", "poultry")
    elif code in {"poultry", "chicken", "duck", "gà", "ga", "vịt", "vit"}:
        markers = ("bò", "trâu", "heo", "lợn", "cattle", "pig", "swine")
    else:
        return False
    return any(marker in name for marker in markers)


def _age_days(
    session: Session, *, farm_id: str, animal_id: str, now: datetime
) -> tuple[int | None, str | None]:
    birth = session.scalar(
        select(HerdEvent)
        .where(
            HerdEvent.farm_id == farm_id,
            HerdEvent.animal_id == animal_id,
            HerdEvent.event_type == "birth",
        )
        .order_by(HerdEvent.occurred_at)
        .limit(1)
    )
    if birth is None:
        return None, None
    days = (_as_utc(now) - _as_utc(birth.occurred_at)).days
    return (max(days, 0), "birth_event")


def _validate_growth_result(
    result: GrowthAIResult, *, latest_weight: float
) -> GrowthAIResult:
    if not isinstance(result, GrowthAIResult):
        raise AIAnalysisError("AI trả về kết quả dự báo không hợp lệ.")
    horizons = [forecast.horizon_days for forecast in result.forecasts]
    if horizons != [7, 14, 30]:
        raise AIAnalysisError("AI phải trả về đúng các mốc 7, 14 và 30 ngày.")
    for forecast in result.forecasts:
        weight = forecast.predicted_weight_kg
        if weight <= 0 or weight < latest_weight * 0.25 or weight > latest_weight * 4:
            raise AIAnalysisError(
                "Cân nặng dự báo của AI lệch quá xa dữ liệu gần nhất."
            )
    if result.trend not in {"increasing", "stable", "decreasing", "uncertain"}:
        raise AIAnalysisError("AI trả về xu hướng không hợp lệ.")
    if result.confidence not in {"low", "medium", "high"}:
        raise AIAnalysisError("AI trả về mức độ tin cậy không hợp lệ.")
    if not result.explanation.strip() or len(result.explanation) > 2000:
        raise AIAnalysisError("AI trả về giải thích không hợp lệ.")
    if not result.limitations:
        raise AIAnalysisError("AI không nêu giới hạn của kết quả.")
    return result


def _validate_feed_result(
    result: FeedAIResult, *, available_names: dict[str, str], total_weight: float
) -> FeedAIResult:
    if not isinstance(result, FeedAIResult):
        raise AIAnalysisError("AI trả về kết quả khẩu phần không hợp lệ.")
    if not result.items:
        raise AIAnalysisError("AI không trả về thành phần khẩu phần.")
    seen: set[str] = set()
    total_amount = 0.0
    for item in result.items:
        key = item.feed_name.strip().casefold()
        if key not in available_names:
            raise AIAnalysisError(
                "AI đề xuất thức ăn không có trong danh mục hiện có. Kết quả không được lưu."
            )
        if key in seen:
            raise AIAnalysisError("AI trả về một loại thức ăn bị trùng.")
        seen.add(key)
        if item.amount_per_day_kg <= 0:
            raise AIAnalysisError("AI trả về lượng thức ăn không hợp lệ.")
        if not item.reason.strip() or len(item.reason) > 500:
            raise AIAnalysisError("AI trả về lý do chọn thức ăn không hợp lệ.")
        total_amount += item.amount_per_day_kg
    if total_weight > 0 and total_amount > total_weight * 0.25:
        raise AIAnalysisError(
            "Tổng lượng thức ăn AI đề xuất vượt quá 25% khối lượng cơ thể ước tính."
        )
    if not 1 <= result.feedings_per_day <= 6:
        raise AIAnalysisError("AI trả về số bữa ăn mỗi ngày không hợp lệ.")
    if not result.rationale.strip() or len(result.rationale) > 2000:
        raise AIAnalysisError("AI trả về giải thích khẩu phần không hợp lệ.")
    if not result.limitations:
        raise AIAnalysisError("AI không nêu giới hạn của khẩu phần.")
    return result


def _local_growth_result(
    history: list[WeightRecord],
    *,
    context: dict[str, object],
) -> GrowthAIResult:
    analysis = analyze_growth(
        [
            (_as_utc(record.measured_at), float(record.weight_kg))
            for record in history
        ]
    )
    if not analysis.available or sorted(analysis.forecasts) != [7, 14, 30]:
        raise AIAnalysisError(
            "Không đủ dữ liệu cân nặng để tạo dự báo dự phòng."
        )

    latest_weight = float(history[-1].weight_kg)
    daily_gain = analysis.average_daily_gain or 0.0
    if abs(daily_gain) < 0.01:
        trend = "stable"
    elif daily_gain > 0:
        trend = "increasing"
    else:
        trend = "decreasing"

    validation = dict(analysis.validation or {})
    test_at = validation.get("test_at")
    if isinstance(test_at, datetime):
        validation["test_at"] = _iso_utc(test_at)
    validation_mae = validation.get("absolute_error_kg")
    relative_error = (
        float(validation_mae) / latest_weight
        if isinstance(validation_mae, (int, float)) and latest_weight > 0
        else None
    )
    if len(history) >= 5 and relative_error is not None and relative_error <= 0.05:
        confidence = "high"
    elif len(history) >= 3 and validation.get("available"):
        confidence = "medium"
    else:
        confidence = "low"

    record_count = len(history)
    explanation = (
        f"Mô hình dự phòng nội bộ dùng hồi quy tuyến tính trên "
        f"{record_count} lần cân trong "
        f"{context['observation_days']} ngày. "
        f"Xu hướng ước tính là {trend}, với mức tăng trung bình "
        f"{daily_gain:.3f} kg/ngày. "
        "Kết quả này không phải do Gemini tạo."
    )
    limitations = [
        "Kết quả do mô hình dự phòng nội bộ tạo, không phải Gemini.",
        analysis.model_note,
        "Dự báo giả định xu hướng tuyến tính và chỉ dựa trên lịch sử cân nặng.",
    ]
    missing_data = context.get("missing_data")
    if isinstance(missing_data, list):
        limitations.extend(str(item) for item in missing_data)

    result = GrowthAIResult(
        forecasts=tuple(
            GrowthForecast(
                horizon_days=horizon,
                predicted_weight_kg=analysis.forecasts[horizon],
            )
            for horizon in (7, 14, 30)
        ),
        trend=trend,
        confidence=confidence,
        explanation=explanation,
        limitations=tuple(limitations),
        model=_LOCAL_GROWTH_MODEL,
        raw={
            "provider": "local",
            "method": analysis.method,
            "average_daily_gain_kg": analysis.average_daily_gain,
            "validation": validation,
        },
    )
    return _validate_growth_result(result, latest_weight=latest_weight)


def _safe_ai_text(value: str, limit: int = 2000) -> str:
    return value.strip()[:limit]


def _feed_price_map(session: Session, farm_id: str) -> dict[str, Decimal]:
    rows = session.execute(
        select(FeedPlanItem.feed_name, FeedPlanItem.price_per_unit)
        .join(FeedPlan, FeedPlan.id == FeedPlanItem.plan_id)
        .where(
            FeedPlan.farm_id == farm_id,
            FeedPlanItem.farm_id == farm_id,
            FeedPlanItem.price_per_unit > 0,
        )
        .order_by(FeedPlan.created_at.desc())
        .limit(500)
    ).all()
    prices: dict[str, Decimal] = {}
    for name, price in rows:
        key = name.strip().casefold()
        if key not in prices:
            prices[key] = Decimal(str(price))
    return prices


def _recent_consumption(
    session: Session, *, farm_id: str, herd_id: str | None
) -> dict[str, float]:
    if herd_id is None:
        return {}
    since = utc_now() - timedelta(days=30)
    rows = session.execute(
        select(
            FeedTransaction.feed_item_id,
            FeedTransaction.quantity_delta_kg,
        ).where(
            FeedTransaction.farm_id == farm_id,
            FeedTransaction.herd_id == herd_id,
            FeedTransaction.transaction_type == "consumption",
            FeedTransaction.occurred_at >= since,
        )
    ).all()
    totals: dict[str, float] = {}
    for feed_item_id, delta in rows:
        totals[feed_item_id] = totals.get(feed_item_id, 0.0) + abs(float(delta))
    return totals


class DiseaseRiskService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def assess(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str,
        symptoms: list[str],
    ) -> DiseaseRiskAssessment:
        try:
            cleaned = normalize_symptoms(symptoms)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        if not cleaned:
            raise ValidationError("Vui lòng chọn ít nhất một triệu chứng.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            animal = _require_animal(self.session, farm_id, animal_id)

            try:
                result = assess_disease_risk(cleaned)
            except ValueError as exc:
                raise ValidationError(str(exc)) from None

            assessment = DiseaseRiskAssessment(
                farm_id=farm_id,
                animal_id=animal.id,
                herd_id=None,
                symptoms=cleaned,
                risk_level=result.risk_level,
                signs_used=result.signs_used,
                explanation=result.explanation,
                recommendations="\n".join(result.recommendations),
                method=result.method,
                emergency_warning=result.emergency_warning,
                performed_by_user_id=actor_user_id,
                created_at=utc_now(),
            )
            self.session.add(assessment)
            self.session.flush()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="disease_risk.assessed",
                entity_type="disease_risk_assessment",
                entity_id=assessment.id,
            )
            return assessment

    def history(self, *, farm_id: str, limit: int = 100) -> list[DiseaseRiskAssessment]:
        return list(
            self.session.scalars(
                select(DiseaseRiskAssessment)
                .where(DiseaseRiskAssessment.farm_id == farm_id)
                .order_by(DiseaseRiskAssessment.created_at.desc())
                .limit(limit)
            ).all()
        )

    def get(self, *, farm_id: str, assessment_id: str) -> DiseaseRiskAssessment:
        assessment = self.session.scalar(
            select(DiseaseRiskAssessment).where(
                DiseaseRiskAssessment.farm_id == farm_id,
                DiseaseRiskAssessment.id == assessment_id,
            )
        )
        if assessment is None:
            raise NotFoundError("Không tìm thấy lần phân tích này.")
        return assessment


class WeightRecordService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def add(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str,
        weight_kg: object,
        measured_at: datetime,
        notes: str | None = None,
    ) -> WeightRecord:
        weight = _decimal_weight(weight_kg)
        if not isinstance(measured_at, datetime):
            raise ValidationError("Ngày cân không hợp lệ.")
        measured = _as_utc(measured_at)
        if measured > utc_now() + timedelta(days=1):
            raise ValidationError("Ngày cân không được ở tương lai.")
        if notes is not None:
            notes = notes.strip() or None
            if notes and len(notes) > 500:
                raise ValidationError("Ghi chú tối đa 500 ký tự.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            animal = _require_animal(self.session, farm_id, animal_id)

            existing = self.session.scalar(
                select(WeightRecord).where(
                    WeightRecord.farm_id == farm_id,
                    WeightRecord.animal_id == animal.id,
                    WeightRecord.measured_at == measured,
                )
            )
            if existing is not None:
                raise ValidationError(
                    "Vật nuôi đã có lần cân trùng thời điểm. Vui lòng chọn thời điểm khác."
                )

            record = WeightRecord(
                farm_id=farm_id,
                animal_id=animal.id,
                weight_kg=weight,
                measured_at=measured,
                notes=notes,
                recorded_by_user_id=actor_user_id,
                created_at=utc_now(),
            )
            self.session.add(record)
            self.session.flush()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="weight_record.created",
                entity_type="weight_record",
                entity_id=record.id,
            )
            return record

    def history(self, *, farm_id: str, animal_id: str) -> list[WeightRecord]:
        return list(
            self.session.scalars(
                select(WeightRecord)
                .where(
                    WeightRecord.farm_id == farm_id,
                    WeightRecord.animal_id == animal_id,
                )
                .order_by(WeightRecord.measured_at)
            ).all()
        )


class GrowthPredictionService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def analyze(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str,
        save: bool = False,
        ai_client: StructuredAIClient | None = None,
        allow_fallback: bool = True,
    ) -> tuple[Animal, GrowthAIResult, GrowthPrediction | None]:
        require_farm_member(self.session, farm_id, actor_user_id)
        if save:
            require_farm_operator(self.session, farm_id, actor_user_id)
        animal = _require_animal(self.session, farm_id, animal_id)
        history = self.session.scalars(
            select(WeightRecord)
            .where(
                WeightRecord.farm_id == farm_id,
                WeightRecord.animal_id == animal.id,
            )
            .order_by(WeightRecord.measured_at)
        ).all()
        if len(history) < 2:
            raise ValidationError(
                "Cần ít nhất 2 lần cân để phân tích tăng trưởng. "
                "Hãy ghi thêm lần cân trước khi gọi AI."
            )

        now = utc_now()
        age_days, age_source = _age_days(
            self.session, farm_id=farm_id, animal_id=animal.id, now=now
        )
        herd = None
        if animal.herd_id:
            herd = self.session.scalar(
                select(Herd).where(
                    Herd.farm_id == farm_id, Herd.id == animal.herd_id
                )
            )
        latest = history[-1]
        missing = ["Không có trường giống trong dữ liệu."]
        if age_days is None:
            missing.append("Không có ngày sinh để xác định tuổi chính xác.")
        context = {
            "species": {
                "code": animal.species_code,
                "name_vi": _species_name(self.session, animal.species_code),
            },
            "breed": None,
            "age_days": age_days,
            "age_source": age_source,
            "herd": (
                {
                    "head_count": herd.head_count,
                    "unidentified_count": herd.unidentified_count,
                }
                if herd is not None
                else None
            ),
            "weights": [
                {
                    "measured_at": _iso_utc(record.measured_at),
                    "weight_kg": round(float(record.weight_kg), 3),
                }
                for record in history
            ],
            "record_count": len(history),
            "latest_weight_kg": round(float(latest.weight_kg), 3),
            "observation_days": round(
                (
                    _as_utc(history[-1].measured_at)
                    - _as_utc(history[0].measured_at)
                ).total_seconds()
                / 86400.0,
                2,
            ),
            "missing_data": missing,
        }
        try:
            if ai_client is None:
                if not allow_fallback:
                    raise AIAnalysisError(
                        "Chưa cấu hình dịch vụ AI. Không có kết quả nào được tạo."
                    )
                result = _local_growth_result(history, context=context)
            else:
                result = _validate_growth_result(
                    ai_client.generate_growth(context),
                    latest_weight=float(latest.weight_kg),
                )
        except AIProviderUnavailableError as exc:
            if not allow_fallback:
                raise
            logger.warning(
                "Gemini unavailable for growth analysis; using local fallback: "
                "status=%s detail=%s",
                exc.status_code,
                str(exc)[:500],
            )
            result = _local_growth_result(history, context=context)

        is_local = result.raw.get("provider") == "local"
        method = _LOCAL_GROWTH_METHOD if is_local else "gemini-structured-v1"
        prediction = None
        if save:
            with atomic(self.session):
                require_farm_operator(self.session, farm_id, actor_user_id)
                if is_local:
                    cutoff = _as_utc(now) - timedelta(
                        minutes=_LOCAL_GROWTH_DEDUPE_MINUTES
                    )
                    recent = self.session.scalars(
                        select(GrowthPrediction)
                        .where(
                            GrowthPrediction.farm_id == farm_id,
                            GrowthPrediction.animal_id == animal.id,
                            GrowthPrediction.method == method,
                        )
                        .order_by(GrowthPrediction.created_at.desc())
                        .limit(5)
                    ).all()
                    prediction = next(
                        (
                            item
                            for item in recent
                            if _as_utc(item.created_at) >= cutoff
                            and item.input_snapshot == context
                        ),
                        None,
                    )
                if prediction is None:
                    prediction = GrowthPrediction(
                        farm_id=farm_id,
                        animal_id=animal.id,
                        method=method,
                        history_count=len(history),
                        predicted={
                            "horizons": {
                                str(forecast.horizon_days): forecast.predicted_weight_kg
                                for forecast in result.forecasts
                            },
                            "trend": result.trend,
                            "confidence": result.confidence,
                            "limitations": list(result.limitations),
                            "model": result.model,
                            "raw": dict(result.raw),
                        },
                        explanation=_safe_ai_text(result.explanation),
                        performed_by_user_id=actor_user_id,
                        created_at=now,
                        ai_model=result.model[:64],
                        input_snapshot=context,
                        trend=result.trend,
                        confidence=result.confidence,
                        limitations="\n".join(result.limitations),
                    )
                    self.session.add(prediction)
                    self.session.flush()
                    record_audit(
                        self.session,
                        farm_id=farm_id,
                        actor_user_id=actor_user_id,
                        action="growth_prediction.created",
                        entity_type="growth_prediction",
                        entity_id=prediction.id,
                    )
        return animal, result, prediction

    def history(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str | None = None,
        limit: int = 50,
    ) -> list[GrowthPrediction]:
        require_farm_member(self.session, farm_id, actor_user_id)
        query = select(GrowthPrediction).where(GrowthPrediction.farm_id == farm_id)
        if animal_id:
            query = query.where(GrowthPrediction.animal_id == animal_id)
        return list(
            self.session.scalars(
                query.order_by(GrowthPrediction.created_at.desc()).limit(limit)
            ).all()
        )

    def get(
        self, *, farm_id: str, actor_user_id: str, prediction_id: str
    ) -> GrowthPrediction:
        require_farm_member(self.session, farm_id, actor_user_id)
        prediction = self.session.scalar(
            select(GrowthPrediction).where(
                GrowthPrediction.farm_id == farm_id,
                GrowthPrediction.id == prediction_id,
            )
        )
        if prediction is None:
            raise NotFoundError("Không tìm thấy kết quả dự báo này.")
        return prediction


class FeedPlanService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def create(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str | None,
        herd_id: str | None,
        name: str,
        period_days: int,
        notes: str | None,
        items: list[dict],
    ):
        if (animal_id is None) == (herd_id is None):
            raise ValidationError("Chọn đúng một vật nuôi hoặc một đàn.")
        name = (name or "").strip()
        if not name or len(name) > 160:
            raise ValidationError("Tên phương án phải có 1 đến 160 ký tự.")
        if (
            isinstance(period_days, bool)
            or not isinstance(period_days, int)
            or not 1 <= period_days <= 365
        ):
            raise ValidationError("Số ngày phải trong khoảng 1 đến 365.")
        if notes is not None:
            notes = notes.strip() or None
            if notes and len(notes) > 1000:
                raise ValidationError("Ghi chú tối đa 1000 ký tự.")

        has_nutrition = any(
            isinstance(item, dict) and item.get("nutrients")
            for item in items
        )
        try:
            result = calculate_feed_plan(
                items, period_days=period_days, has_nutrition=has_nutrition
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from None

        assumptions = (
            "Chi phí tính từ giá và lượng do người dùng nhập. "
            + result.nutritional_note
        )

        now = utc_now()
        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            if animal_id is not None:
                _require_animal(self.session, farm_id, animal_id)
            if herd_id is not None:
                herd = self.session.scalar(
                    select(Herd).where(Herd.farm_id == farm_id, Herd.id == herd_id)
                )
                if herd is None:
                    raise NotFoundError("Không tìm thấy đàn trong trang trại của bạn.")

            plan = FeedPlan(
                farm_id=farm_id,
                animal_id=animal_id,
                herd_id=herd_id,
                name=name,
                period_days=period_days,
                notes=notes,
                assumptions=assumptions,
                created_by_user_id=actor_user_id,
                created_at=now,
                source="manual",
                status="accepted",
                accepted_at=now,
                accepted_by_user_id=actor_user_id,
            )
            self.session.add(plan)
            self.session.flush()

            for line, item in zip(result.lines, items):
                nutrients = item.get("nutrients") if isinstance(item, dict) else None
                if nutrients is not None and not isinstance(nutrients, dict):
                    raise ValidationError("Thành phần dinh dưỡng phải là đối tượng JSON.")
                self.session.add(
                    FeedPlanItem(
                        farm_id=farm_id,
                        plan_id=plan.id,
                        feed_name=line.feed_name,
                        unit=line.unit,
                        price_per_unit=Decimal(str(line.price_per_unit)),
                        amount_per_day=Decimal(str(line.amount_per_day)),
                        nutrients=nutrients,
                        notes=(
                            str(item.get("notes", "")).strip()[:500] or None
                            if isinstance(item, dict)
                            else None
                        ),
                    )
                )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="feed_plan.created",
                entity_type="feed_plan",
                entity_id=plan.id,
            )
        return plan, result

    def suggest(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str | None,
        herd_id: str | None,
        period_days: int,
        goal: str = "growth",
        ai_client: StructuredAIClient | None = None,
    ) -> tuple[FeedPlan, FeedAIResult]:
        if (animal_id is None) == (herd_id is None):
            raise ValidationError("Chọn đúng một vật nuôi hoặc một đàn.")
        if (
            isinstance(period_days, bool)
            or not isinstance(period_days, int)
            or not 1 <= period_days <= 365
        ):
            raise ValidationError("Số ngày phải trong khoảng 1 đến 365.")
        if goal not in {"maintenance", "growth", "finishing"}:
            raise ValidationError("Mục tiêu khẩu phần không hợp lệ.")

        require_farm_operator(self.session, farm_id, actor_user_id)
        now = utc_now()
        if animal_id is not None:
            animal = _require_animal(self.session, farm_id, animal_id)
            herd = None
            if animal.herd_id:
                herd = self.session.scalar(
                    select(Herd).where(
                        Herd.farm_id == farm_id, Herd.id == animal.herd_id
                    )
                )
            history = list(
                self.session.scalars(
                    select(WeightRecord)
                    .where(
                        WeightRecord.farm_id == farm_id,
                        WeightRecord.animal_id == animal.id,
                    )
                    .order_by(WeightRecord.measured_at)
                ).all()
            )
            if not history:
                raise ValidationError(
                    "Cần ít nhất một lần cân hiện tại của vật nuôi trước khi gợi ý khẩu phần."
                )
            latest_weight = float(history[-1].weight_kg)
            total_weight = latest_weight
            head_count = 1
            weighed_head_count = 1
            unidentified_count = 0
            weight_history = [
                {
                    "measured_at": _iso_utc(record.measured_at),
                    "weight_kg": round(float(record.weight_kg), 3),
                }
                for record in history[-12:]
            ]
            age_days, age_source = _age_days(
                self.session, farm_id=farm_id, animal_id=animal.id, now=now
            )
            species_code = animal.species_code
            subject_label = _animal_label(self.session, animal)
            selected_herd_id = animal.herd_id
            subject_type = "animal"
        else:
            herd = self.session.scalar(
                select(Herd).where(Herd.farm_id == farm_id, Herd.id == herd_id)
            )
            if herd is None:
                raise NotFoundError("Không tìm thấy đàn trong trang trại của bạn.")
            head_count = herd.head_count
            if head_count <= 0:
                raise ValidationError("Đàn đã chọn không có vật nuôi đang hoạt động.")
            animals = list(
                self.session.scalars(
                    select(Animal).where(
                        Animal.farm_id == farm_id,
                        Animal.herd_id == herd.id,
                        Animal.status == "active",
                    )
                ).all()
            )
            weights = []
            for member in animals:
                latest = _latest_weight(
                    self.session, farm_id=farm_id, animal_id=member.id
                )
                if latest is not None:
                    weights.append(latest)
            if not weights:
                raise ValidationError(
                    "Cần ít nhất một lần cân của một vật nuôi trong đàn trước khi gợi ý khẩu phần."
                )
            average_weight = sum(float(record.weight_kg) for record in weights) / len(
                weights
            )
            latest_weight = average_weight
            total_weight = average_weight * head_count
            weighed_head_count = len(weights)
            unidentified_count = herd.unidentified_count
            weight_history = [
                {
                    "measured_at": _iso_utc(record.measured_at),
                    "weight_kg": round(float(record.weight_kg), 3),
                }
                for record in weights[-12:]
            ]
            age_days = None
            age_source = None
            species_code = herd.species_code
            subject_label = herd.name
            selected_herd_id = herd.id
            subject_type = "herd"

        all_feed_items = list(
            self.session.scalars(
                select(FeedItem)
                .where(FeedItem.farm_id == farm_id, FeedItem.status == "active")
                .order_by(FeedItem.name)
            ).all()
        )
        if not all_feed_items:
            raise ValidationError(
                "Chưa có danh mục thức ăn đang hoạt động. Hãy thêm thức ăn trước khi gợi ý."
            )
        excluded_feed = [
            item.name
            for item in all_feed_items
            if _feed_species_conflict(item.name, species_code)
        ]
        feed_items = [
            item
            for item in all_feed_items
            if item.name not in excluded_feed
        ]
        if not feed_items:
            raise ValidationError(
                "Không có thức ăn đang hoạt động phù hợp với loài đã chọn. "
                "Hãy kiểm tra lại danh mục thức ăn."
            )
        balances = {
            row.feed_item_id: row.quantity_kg
            for row in self.session.scalars(
                select(FeedInventoryBalance).where(
                    FeedInventoryBalance.farm_id == farm_id
                )
            ).all()
        }
        prices = _feed_price_map(self.session, farm_id)
        consumption = _recent_consumption(
            self.session, farm_id=farm_id, herd_id=selected_herd_id
        )
        available_feed = [
            {
                "name": item.name,
                "available_kg": round(float(balances.get(item.id, 0)), 3),
                "last_price_per_kg": (
                    float(prices[item.name.strip().casefold()])
                    if item.name.strip().casefold() in prices
                    else None
                ),
                "recent_consumption_kg_30d": round(
                    consumption.get(item.id, 0.0), 3
                ),
            }
            for item in feed_items
        ]
        missing = ["Không có trường giống trong dữ liệu."]
        if age_days is None:
            missing.append("Không có ngày sinh để xác định tuổi chính xác.")
        missing.append(
            "Chưa có bảng thành phần dinh dưỡng của thức ăn để cân đối năng lượng/protein."
        )
        context = {
            "subject_type": subject_type,
            "species": {
                "code": species_code,
                "name_vi": _species_name(self.session, species_code),
            },
            "breed": None,
            "age_days": age_days,
            "age_source": age_source,
            "goal": goal,
            "period_days": period_days,
            "head_count": head_count,
            "weighed_head_count": weighed_head_count,
            "unidentified_count": unidentified_count,
            "latest_weight_kg": round(latest_weight, 3),
            "estimated_total_weight_kg": round(total_weight, 3),
            "weight_history": weight_history,
            "available_feed": available_feed,
            "missing_data": missing,
        }
        if ai_client is None:
            raise AIAnalysisError(
                "Chưa cấu hình dịch vụ AI. Không có kết quả nào được tạo."
            )
        available_names = {
            item["name"].strip().casefold(): item["name"] for item in available_feed
        }
        result = _validate_feed_result(
            ai_client.generate_feed(context),
            available_names=available_names,
            total_weight=total_weight,
        )
        limitations = list(result.limitations)
        if "Không có trường giống" in " ".join(missing):
            limitations.append(
                "Chưa có dữ liệu giống; AI không thể hiệu chỉnh nhu cầu theo giống."
            )
        if age_days is None:
            limitations.append(
                "Chưa có tuổi chính xác; khẩu phần chỉ dựa trên cân nặng và mục tiêu."
            )
        if subject_type == "herd":
            limitations.append(
                "Khối lượng đàn được ước tính từ các cá thể đã cân; "
                "các cá thể chưa cân được giả định có cùng cân nặng trung bình."
            )
        if excluded_feed:
            limitations.append(
                "Không xét các thức ăn có tên rõ ràng dành cho loài khác: "
                + ", ".join(excluded_feed)
                + "."
            )
        limitations.append(
            "Chưa có thành phần hóa học/dinh dưỡng của thức ăn; "
            "khẩu phần chưa được kiểm chứng cân đối năng lượng, protein và khoáng."
        )
        if any(item["last_price_per_kg"] is None for item in available_feed):
            limitations.append(
                "Một số thức ăn chưa có giá lịch sử; chi phí 0 không có nghĩa là miễn phí."
            )
        assumptions = list(result.assumptions)
        assumptions.append(
            "Đây là đề xuất do AI tạo từ dữ liệu hiện có, chưa được chuyên gia phê duyệt."
        )

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            name = f"Đề xuất AI · {subject_label} · {now.date().isoformat()}"[:160]
            plan = FeedPlan(
                farm_id=farm_id,
                animal_id=animal_id,
                herd_id=herd_id,
                name=name,
                period_days=period_days,
                notes=None,
                assumptions="\n".join(assumptions)[:1000],
                created_by_user_id=actor_user_id,
                created_at=now,
                source="ai",
                status="proposed",
                ai_model=result.model[:64],
                input_snapshot=context,
                rationale=_safe_ai_text(result.rationale),
                limitations="\n".join(limitations),
                confidence=result.confidence,
                feedings_per_day=result.feedings_per_day,
                schedule_note=result.schedule_note[:500],
            )
            self.session.add(plan)
            self.session.flush()

            total_amount = sum(
                item.amount_per_day_kg for item in result.items
            )
            for item in result.items:
                key = item.feed_name.strip().casefold()
                price = prices.get(key, Decimal("0"))
                ratio = (
                    round(item.amount_per_day_kg / total_amount * 100, 2)
                    if total_amount > 0
                    else None
                )
                self.session.add(
                    FeedPlanItem(
                        farm_id=farm_id,
                        plan_id=plan.id,
                        feed_name=item.feed_name,
                        unit="kg",
                        price_per_unit=price,
                        amount_per_day=Decimal(str(item.amount_per_day_kg)),
                        nutrients={
                            "source": "ai_gemini",
                            "mix_ratio_percent": ratio,
                            "price_source": (
                                "history" if price > 0 else "missing"
                            ),
                        },
                        notes=item.reason[:500],
                    )
                )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="feed_plan.ai_suggested",
                entity_type="feed_plan",
                entity_id=plan.id,
            )
        return plan, result

    def accept(
        self, *, farm_id: str, actor_user_id: str, plan_id: str
    ) -> FeedPlan:
        require_farm_operator(self.session, farm_id, actor_user_id)
        with atomic(self.session):
            plan = self.get(farm_id=farm_id, plan_id=plan_id)
            if plan.source != "ai":
                raise ValidationError("Chỉ có thể chấp nhận một đề xuất do AI tạo.")
            if plan.status != "proposed":
                raise ValidationError("Đề xuất này đã được xử lý trước đó.")
            plan.status = "accepted"
            plan.accepted_at = utc_now()
            plan.accepted_by_user_id = actor_user_id
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="feed_plan.accepted",
                entity_type="feed_plan",
                entity_id=plan.id,
            )
        return plan

    def list(self, *, farm_id: str, limit: int = 100) -> List[FeedPlan]:
        return list(
            self.session.scalars(
                select(FeedPlan)
                .where(FeedPlan.farm_id == farm_id)
                .order_by(FeedPlan.created_at.desc())
                .limit(limit)
            ).all()
        )

    def get(self, *, farm_id: str, plan_id: str) -> FeedPlan:
        plan = self.session.scalar(
            select(FeedPlan).where(FeedPlan.farm_id == farm_id, FeedPlan.id == plan_id)
        )
        if plan is None:
            raise NotFoundError("Không tìm thấy phương án khẩu phần này.")
        return plan

    def items(self, *, farm_id: str, plan_id: str) -> List[FeedPlanItem]:
        return list(
            self.session.scalars(
                select(FeedPlanItem)
                .where(
                    FeedPlanItem.farm_id == farm_id,
                    FeedPlanItem.plan_id == plan_id,
                )
                .order_by(FeedPlanItem.feed_name)
            ).all()
        )

    def summarize(self, plan: FeedPlan, items: List[FeedPlanItem]):
        lines = [
            {
                "feed_name": item.feed_name,
                "unit": item.unit,
                "price_per_unit": float(item.price_per_unit),
                "amount_per_day": float(item.amount_per_day),
            }
            for item in items
        ]
        has_nutrition = any(
            item.nutrients
            and not (
                isinstance(item.nutrients, dict)
                and item.nutrients.get("source") == "ai_gemini"
            )
            for item in items
        )
        return calculate_feed_plan(
            lines,
            period_days=plan.period_days,
            has_nutrition=has_nutrition,
        )
