"""Deterministic, explainable baseline analytics for UC013-UC015.

This module contains no database access. It provides transparent rule-based
screening and least-squares trend estimation so the UI never has to pretend a
clinically validated AI model exists. Every result is explicitly labelled as a
reference estimate.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_HORIZON_DAYS = 30
MIN_WEIGHT_RECORDS = 2
RECOMMENDED_WEIGHT_RECORDS = 4

# Symptom catalogue. Keys are stable identifiers stored in the DB/JSON payload;
# Vietnamese labels are what the UI and explanations show.
SYMPTOM_CATALOG: dict[str, dict[str, Any]] = {
    "inappetence": {
        "label": "Bỏ ăn hoặc giảm ăn",
        "weight": 2,
        "category": "general",
    },
    "fever": {
        "label": "Sốt hoặc nhiệt độ bất thường",
        "weight": 2,
        "category": "general",
    },
    "cough": {
        "label": "Ho, khó thở",
        "weight": 2,
        "category": "respiratory",
    },
    "diarrhea": {
        "label": "Tiêu chảy",
        "weight": 2,
        "category": "digestive",
    },
    "lethargy": {
        "label": "Mệt mỏi, giảm vận động",
        "weight": 1,
        "category": "general",
    },
    "weight_loss": {
        "label": "Giảm cân hoặc tăng trưởng bất thường",
        "weight": 1,
        "category": "growth",
    },
}

# Symptoms that require urgent veterinary contact regardless of total score.
EMERGENCY_SYMPTOMS: dict[str, str] = {
    "severe_breathing": "Khó thở nghiêm trọng",
    "seizure": "Co giật",
    "paralysis": "Nằm liệt, không đứng dậy được",
}

# Extra selectable symptoms beyond the core catalogue; they contribute to the
# score but are grouped by category for the explainability summary.
EXTRA_SYMPTOMS: dict[str, dict[str, Any]] = {
    "severe_breathing": {"label": "Khó thở nghiêm trọng", "weight": 3, "category": "respiratory"},
    "seizure": {"label": "Co giật", "weight": 3, "category": "neurological"},
    "paralysis": {"label": "Nằm liệt", "weight": 3, "category": "neurological"},
}

ALL_SYMPTOMS: dict[str, dict[str, Any]] = {**SYMPTOM_CATALOG, **EXTRA_SYMPTOMS}

RISK_LABELS = {
    "low": "Thấp",
    "medium": "Trung bình",
    "high": "Cao",
}


@dataclass(frozen=True)
class DiseaseRiskResult:
    risk_level: str
    risk_label: str
    score: int
    signs_used: list[str]
    categories: list[str]
    explanation: str
    recommendations: list[str]
    emergency_warning: bool
    emergency_signs: list[str]
    method: str = "rule_based_screening_v1"


def normalize_symptoms(raw: Any) -> list[str]:
    """Return a validated, de-duplicated list of known symptom keys."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw_values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        raw_values = list(raw)
    else:
        raise ValueError("Danh sách triệu chứng không hợp lệ.")
    result: list[str] = []
    for value in raw_values:
        if not isinstance(value, str):
            raise ValueError("Triệu chứng phải là chuỗi ký tự.")
        key = value.strip()
        if not key:
            continue
        if key not in ALL_SYMPTOMS:
            raise ValueError(f"Triệu chứng không được hỗ trợ: {key}")
        if key not in result:
            result.append(key)
    return result


def assess_disease_risk(raw_symptoms: Any) -> DiseaseRiskResult:
    """Transparent rule-based screening. Not a clinical diagnosis."""
    symptoms = normalize_symptoms(raw_symptoms)
    if not symptoms:
        raise ValueError("Vui lòng chọn ít nhất một triệu chứng để phân tích.")

    score = sum(int(ALL_SYMPTOMS[key]["weight"]) for key in symptoms)
    emergency_signs = [EMERGENCY_SYMPTOMS[key] for key in symptoms if key in EMERGENCY_SYMPTOMS]
    emergency = bool(emergency_signs)

    categories: list[str] = []
    for key in symptoms:
        category = ALL_SYMPTOMS[key]["category"]
        if category not in categories:
            categories.append(category)

    if emergency:
        risk_level = "high"
    elif score >= 5 or len(symptoms) >= 4:
        risk_level = "high"
    elif score >= 3 or len(symptoms) >= 2:
        risk_level = "medium"
    else:
        risk_level = "low"

    signs_used = [ALL_SYMPTOMS[key]["label"] for key in symptoms]

    if emergency:
        explanation = (
            "Có dấu hiệu nguy hiểm cần can thiệp ngay: "
            + ", ".join(emergency_signs)
            + ". Hệ thống nâng mức cảnh báo lên cao bất kể số triệu chứng khác."
        )
    elif risk_level == "high":
        explanation = (
            f"Có {len(symptoms)} dấu hiệu với tổng điểm quy tắc {score}. "
            "Nhiều dấu hiệu bất thường cùng lúc hoặc mức điểm cao cho thấy cần kiểm tra sớm."
        )
    elif risk_level == "medium":
        explanation = (
            f"Có {len(symptoms)} dấu hiệu với tổng điểm quy tắc {score}. "
            "Mức này gợi ý nên theo dõi sát và ghi nhận thêm quan sát."
        )
    else:
        explanation = (
            f"Chỉ có {len(symptoms)} dấu hiệu nhẹ với tổng điểm quy tắc {score}. "
            "Chưa đủ để cảnh báo cao nhưng vẫn cần theo dõi."
        )

    recommendations: list[str] = []
    if emergency:
        recommendations.append(
            "Liên hệ bác sĩ thú y ngay lập tức; đây là tình huống có thể nguy hiểm."
        )
    if "respiratory" in categories:
        recommendations.append("Theo dõi nhịp thở, giữ nơi thoáng và tránh bụi.")
    if "digestive" in categories:
        recommendations.append("Theo dõi nước uống, phân và bù nước theo hướng dẫn thú y.")
    if "general" in categories:
        recommendations.append("Đo nhiệt độ và theo dõi khả năng ăn uống mỗi ngày.")
    if "growth" in categories:
        recommendations.append("Ghi lại cân nặng và so sánh với lần cân trước.")
    if risk_level in {"medium", "high"} and not emergency:
        recommendations.append("Cân nhắc đặt lịch kiểm tra với bác sĩ thú y.")
    recommendations.append(
        "Đây là kết quả sàng lọc tham khảo bằng quy tắc, không phải chẩn đoán bệnh."
    )

    return DiseaseRiskResult(
        risk_level=risk_level,
        risk_label=RISK_LABELS[risk_level],
        score=score,
        signs_used=signs_used,
        categories=categories,
        explanation=explanation,
        recommendations=recommendations,
        emergency_warning=emergency,
        emergency_signs=emergency_signs,
    )


@dataclass(frozen=True)
class WeightPoint:
    measured_at: datetime
    weight_kg: float


@dataclass(frozen=True)
class GrowthAnalysis:
    available: bool
    message: str
    history: list[WeightPoint]
    deltas: list[dict[str, Any]] = field(default_factory=list)
    average_daily_gain: float | None = None
    forecasts: dict[int, float] = field(default_factory=dict)
    method: str = "linear_regression_baseline_v1"
    model_note: str = ""
    validation: dict[str, Any] | None = None


def _linear_regression(points: list[WeightPoint]) -> tuple[float, float]:
    """Least-squares fit of weight against days since first measurement."""
    origin = points[0].measured_at
    xs = [(p.measured_at - origin).total_seconds() / 86400.0 for p in points]
    ys = [p.weight_kg for p in points]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0, mean_y
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    intercept = mean_y - slope * mean_x
    return slope, intercept


def analyze_growth(
    raw_history: list[tuple[datetime, float]],
    *,
    horizons: tuple[int, ...] = (7, 14, 30),
) -> GrowthAnalysis:
    """Estimate weight trend from dated samples.

    Uses an ordinary least-squares line when there are enough samples, and a
    simple last-vs-first slope when only two samples exist. Forecasts are bounded
    to a small horizon and never below zero.
    """
    cleaned: list[WeightPoint] = []
    for measured_at, weight in raw_history:
        if measured_at is None:
            continue
        try:
            value = float(weight)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        cleaned.append(WeightPoint(measured_at=measured_at, weight_kg=value))
    cleaned.sort(key=lambda p: p.measured_at)

    if len(cleaned) < MIN_WEIGHT_RECORDS:
        return GrowthAnalysis(
            available=False,
            message=(
                "Cần ít nhất 2 lần cân để tính xu hướng. "
                f"Nên có từ {RECOMMENDED_WEIGHT_RECORDS} lần cân trở lên để dự báo đáng tin cậy hơn."
            ),
            history=cleaned,
        )

    deltas: list[dict[str, Any]] = []
    for previous, current in zip(cleaned, cleaned[1:]):
        days = (current.measured_at - previous.measured_at).total_seconds() / 86400.0
        if days <= 0:
            continue
        change = current.weight_kg - previous.weight_kg
        deltas.append(
            {
                "from": previous.measured_at,
                "to": current.measured_at,
                "days": round(days, 2),
                "change_kg": round(change, 3),
                "daily_gain": round(change / days, 4),
            }
        )

    first, last = cleaned[0], cleaned[-1]
    total_days = (last.measured_at - first.measured_at).total_seconds() / 86400.0
    average_daily_gain = None
    if total_days > 0:
        average_daily_gain = round((last.weight_kg - first.weight_kg) / total_days, 4)

    slope, intercept = _linear_regression(cleaned)
    origin = cleaned[0].measured_at
    last_day = (last.measured_at - origin).total_seconds() / 86400.0

    forecasts: dict[int, float] = {}
    for horizon in horizons:
        if horizon <= 0 or horizon > MAX_HORIZON_DAYS:
            continue
        predicted = slope * (last_day + horizon) + intercept
        forecasts[horizon] = round(max(predicted, 0.0), 2)

    validation = _time_holdout_validation(cleaned) if len(cleaned) >= 3 else None

    note = (
        "Ước lượng tham khảo bằng hồi quy tuyến tính trên lịch sử cân nặng; "
        "không phải cam kết tăng trưởng thực tế."
    )
    if len(cleaned) < RECOMMENDED_WEIGHT_RECORDS:
        note += f" Chỉ có {len(cleaned)} lần cân, nên kết quả kém chắc chắn."
    return GrowthAnalysis(
        available=True,
        message="Đã tính xu hướng từ lịch sử cân nặng.",
        history=cleaned,
        deltas=deltas,
        average_daily_gain=average_daily_gain,
        forecasts=forecasts,
        model_note=note,
        validation=validation,
    )


def _time_holdout_validation(points: list[WeightPoint]) -> dict[str, Any]:
    """Hold out the last point in time; fit on the earlier points only."""
    train = points[:-1]
    test = points[-1]
    if len(train) < MIN_WEIGHT_RECORDS:
        return {"available": False, "reason": "Không đủ mẫu để kiểm tra theo thời gian."}
    slope, intercept = _linear_regression(train)
    origin = train[0].measured_at
    test_day = (test.measured_at - origin).total_seconds() / 86400.0
    predicted = max(slope * test_day + intercept, 0.0)
    error = predicted - test.weight_kg
    mae = abs(error)
    return {
        "available": True,
        "train_count": len(train),
        "test_at": test.measured_at,
        "actual_kg": round(test.weight_kg, 3),
        "predicted_kg": round(predicted, 3),
        "absolute_error_kg": round(mae, 3),
        "note": "Chỉ dùng một điểm cuối làm kiểm tra, không đánh giá trên dữ liệu huấn luyện.",
    }


@dataclass(frozen=True)
class FeedLineResult:
    feed_name: str
    unit: str
    price_per_unit: float
    amount_per_day: float
    days: int
    total_amount: float
    daily_cost: float
    period_cost: float


@dataclass(frozen=True)
class FeedPlanResult:
    period_days: int
    lines: list[FeedLineResult]
    total_amount_per_day: float
    total_amount_period: float
    total_daily_cost: float
    total_period_cost: float
    nutritional_note: str


def _to_decimal(value: Any, label: str, *, allow_zero: bool) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} không hợp lệ.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} không phải số hợp lệ.") from None
    if not number.is_finite():
        raise ValueError(f"{label} không hợp lệ.")
    if number < 0 or (not allow_zero and number == 0):
        raise ValueError(f"{label} phải lớn hơn 0.")
    return number


def calculate_feed_plan(
    items: list[dict[str, Any]],
    *,
    period_days: int,
    has_nutrition: bool = False,
) -> FeedPlanResult:
    """Compute feed totals and cost from user-supplied quantities and prices."""
    if isinstance(period_days, bool) or not isinstance(period_days, int) or period_days <= 0:
        raise ValueError("Số ngày của kỳ phải là số nguyên dương.")
    if not items:
        raise ValueError("Cần ít nhất một loại thức ăn trong khẩu phần.")

    lines: list[FeedLineResult] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError("Thông tin thức ăn không hợp lệ.")
        name = str(item.get("feed_name", "")).strip()
        if not name or len(name) > 160:
            raise ValueError(f"Tên thức ăn #{index} phải có 1 đến 160 ký tự.")
        unit = str(item.get("unit", "kg")).strip() or "kg"
        if len(unit) > 32:
            raise ValueError(f"Đơn vị thức ăn #{index} quá dài.")
        price = _to_decimal(item.get("price_per_unit"), f"Giá thức ăn #{index}", allow_zero=True)
        amount = _to_decimal(item.get("amount_per_day"), f"Lượng mỗi ngày #{index}", allow_zero=False)
        total_amount = amount * period_days
        daily_cost = amount * price
        period_cost = daily_cost * period_days
        lines.append(
            FeedLineResult(
                feed_name=name,
                unit=unit,
                price_per_unit=round(float(price), 3),
                amount_per_day=round(float(amount), 3),
                days=period_days,
                total_amount=round(float(total_amount), 3),
                daily_cost=round(float(daily_cost), 2),
                period_cost=round(float(period_cost), 2),
            )
        )

    total_amount_per_day = sum(line.amount_per_day for line in lines)
    total_daily_cost = sum(line.daily_cost for line in lines)
    if has_nutrition:
        note = (
            "Thành phần dinh dưỡng do người dùng nhập và chưa được kiểm chứng độc lập; "
            "không đảm bảo khẩu phần đáp ứng nhu cầu."
        )
    else:
        note = (
            "Chưa có đủ dữ liệu dinh dưỡng đáng tin cậy. Chỉ tính lượng thức ăn và chi phí "
            "từ số liệu bạn nhập; phần gợi ý dinh dưỡng bị bỏ trống."
        )
    return FeedPlanResult(
        period_days=period_days,
        lines=lines,
        total_amount_per_day=round(total_amount_per_day, 3),
        total_amount_period=round(total_amount_per_day * period_days, 3),
        total_daily_cost=round(total_daily_cost, 2),
        total_period_cost=round(total_daily_cost * period_days, 2),
        nutritional_note=note,
    )


def parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("Ngày không đúng định dạng YYYY-MM-DD.") from None
    raise ValueError("Ngày không hợp lệ.")
