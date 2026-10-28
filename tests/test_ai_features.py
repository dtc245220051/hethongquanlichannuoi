import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

import livestock_app.web as web_module

from livestock_app import db
from livestock_app.models import (
    Animal,
    Barn,
    DiseaseRiskAssessment,
    Farm,
    FarmMembership,
    FeedInventoryBalance,
    FeedItem,
    FeedPlan,
    GrowthPrediction,
    Herd,
    Species,
    User,
    WeightRecord,
)
from livestock_app.services import AuthorizationError, NotFoundError, ValidationError
from livestock_app.services.ai import (
    DiseaseRiskService,
    FeedPlanService,
    GrowthPredictionService,
    WeightRecordService,
)
from livestock_app.services.ai_analysis import (
    AIAnalysisError,
    AIProviderUnavailableError,
    FeedAIResult,
    FeedRecommendationLine,
    GrowthAIResult,
    GrowthForecast,
)
from livestock_app.services.analytics import (
    analyze_growth,
    assess_disease_risk,
    calculate_feed_plan,
)


class FakeStructuredAI:
    model = "gemini-test-structured"

    def generate_growth(self, context):
        latest = float(context["latest_weight_kg"])
        return GrowthAIResult(
            forecasts=(
                GrowthForecast(horizon_days=7, predicted_weight_kg=latest + 4),
                GrowthForecast(horizon_days=14, predicted_weight_kg=latest + 8),
                GrowthForecast(horizon_days=30, predicted_weight_kg=latest + 15),
            ),
            trend="increasing",
            confidence="medium",
            explanation="Xu hướng tăng dựa trên các lần cân đã có.",
            limitations=("Chưa có dữ liệu giống.",),
            model=self.model,
            raw={"provider": "fake"},
        )

    def generate_feed(self, context):
        return FeedAIResult(
            items=(
                FeedRecommendationLine(
                    feed_name="Cỏ voi",
                    amount_per_day_kg=5,
                    reason="Nguồn xơ thô sẵn có.",
                ),
                FeedRecommendationLine(
                    feed_name="Cám hỗn hợp",
                    amount_per_day_kg=2,
                    reason="Bổ sung năng lượng.",
                ),
            ),
            feedings_per_day=2,
            schedule_note="Chia hai bữa sáng và chiều.",
            rationale="Cân đối thức ăn thô và thức ăn tinh theo dữ liệu hiện có.",
            assumptions=("Giả định vật nuôi ăn hết khẩu phần.",),
            limitations=("Chưa có thành phần dinh dưỡng.",),
            confidence="low",
            model=self.model,
            raw={"provider": "fake"},
        )


class CapturingFakeStructuredAI(FakeStructuredAI):
    def __init__(self):
        self.growth_context = None
        self.feed_context = None

    def generate_growth(self, context):
        self.growth_context = context
        return super().generate_growth(context)

    def generate_feed(self, context):
        self.feed_context = context
        return super().generate_feed(context)


class CrossSpeciesFeedAI(FakeStructuredAI):
    def generate_feed(self, context):
        return FeedAIResult(
            items=(
                FeedRecommendationLine(
                    feed_name="Cám gia cầm demo",
                    amount_per_day_kg=1.5,
                    reason="Thức ăn sẵn có trong kho.",
                ),
            ),
            feedings_per_day=2,
            schedule_note="Chia hai bữa.",
            rationale="Đề xuất thử nghiệm từ kho hiện có.",
            assumptions=("Giả định vật nuôi ăn hết khẩu phần.",),
            limitations=("Chưa có thành phần dinh dưỡng.",),
            confidence="low",
            model=self.model,
            raw={"provider": "fake"},
        )


class FailingStructuredAI:
    model = "gemini-test-failing"

    def generate_growth(self, context):
        raise AIAnalysisError("AI tạm thời không khả dụng trong bài kiểm thử.")

    def generate_feed(self, context):
        raise AIAnalysisError("AI tạm thời không khả dụng trong bài kiểm thử.")


class UnavailableStructuredAI:
    model = "gemini-test-unavailable"

    def generate_growth(self, context):
        raise AIProviderUnavailableError(
            "Gemini vẫn tạm thời không khả dụng (HTTP 503 sau 3 lần thử).",
            status_code=503,
        )

    def generate_feed(self, context):
        raise AIProviderUnavailableError(
            "Gemini vẫn tạm thời không khả dụng (HTTP 503 sau 3 lần thử).",
            status_code=503,
        )


@pytest.fixture
def ai_data(app):
    with app.app_context():
        db.create_all()
        farm = Farm(name="AI Farm")
        other_farm = Farm(name="Other AI Farm")
        owner = User(
            email="ai-owner@example.test",
            password_hash=generate_password_hash("synthetic-owner-password"),
            display_name="Chủ trại",
        )
        staff = User(
            email="ai-staff@example.test",
            password_hash=generate_password_hash("synthetic-staff-password"),
            display_name="Nhân viên",
        )
        vet = User(
            email="ai-vet@example.test",
            password_hash=generate_password_hash("synthetic-vet-password"),
            display_name="Bác sĩ thú y",
        )
        outsider = User(
            email="ai-outsider@example.test",
            password_hash=generate_password_hash("synthetic-outsider-password"),
            display_name="Người ngoài",
        )
        db.session.add_all([farm, other_farm, owner, staff, vet, outsider])
        db.session.flush()
        db.session.add_all(
            [
                FarmMembership(farm_id=farm.id, user_id=owner.id, role="owner"),
                FarmMembership(farm_id=farm.id, user_id=staff.id, role="care_staff"),
                FarmMembership(farm_id=farm.id, user_id=vet.id, role="veterinarian"),
                FarmMembership(farm_id=other_farm.id, user_id=outsider.id, role="owner"),
            ]
        )
        db.session.add(Species(code="cattle", name_vi="Bò"))
        barn = Barn(farm_id=farm.id, name="Chuồng AI", capacity_heads=20)
        db.session.add(barn)
        db.session.flush()
        herd = Herd(
            farm_id=farm.id,
            name="Đàn AI",
            species_code="cattle",
            barn_id=barn.id,
            unidentified_count=2,
            status="active",
        )
        db.session.add(herd)
        db.session.flush()
        animal = Animal(
            farm_id=farm.id,
            species_code="cattle",
            herd_id=herd.id,
            barn_id=barn.id,
            identifier="AI-01",
            status="active",
        )
        unweighed_animal = Animal(
            farm_id=farm.id,
            species_code="cattle",
            herd_id=herd.id,
            barn_id=barn.id,
            identifier="AI-02",
            status="active",
        )
        other_animal = Animal(
            farm_id=other_farm.id,
            species_code="cattle",
            identifier="OTHER-01",
            status="active",
        )
        db.session.add_all([animal, unweighed_animal, other_animal])
        hay = FeedItem(
            farm_id=farm.id,
            name="Cỏ voi",
            base_unit_code="kg",
            low_stock_threshold_kg=Decimal("10"),
            status="active",
        )
        concentrate = FeedItem(
            farm_id=farm.id,
            name="Cám hỗn hợp",
            base_unit_code="kg",
            low_stock_threshold_kg=Decimal("5"),
            status="active",
        )
        poultry = FeedItem(
            farm_id=farm.id,
            name="Cám gia cầm demo",
            base_unit_code="kg",
            low_stock_threshold_kg=Decimal("5"),
            status="active",
        )
        db.session.add_all([hay, concentrate, poultry])
        db.session.flush()
        now = datetime.now(timezone.utc)
        db.session.add_all(
            [
                FeedInventoryBalance(
                    farm_id=farm.id,
                    feed_item_id=hay.id,
                    quantity_kg=Decimal("100"),
                    updated_at=now,
                ),
                FeedInventoryBalance(
                    farm_id=farm.id,
                    feed_item_id=concentrate.id,
                    quantity_kg=Decimal("50"),
                    updated_at=now,
                ),
                FeedInventoryBalance(
                    farm_id=farm.id,
                    feed_item_id=poultry.id,
                    quantity_kg=Decimal("30"),
                    updated_at=now,
                ),
            ]
        )
        db.session.commit()
        data = {
            "farm_id": farm.id,
            "other_farm_id": other_farm.id,
            "owner_id": owner.id,
            "owner_email": owner.email,
            "staff_id": staff.id,
            "staff_email": staff.email,
            "vet_id": vet.id,
            "vet_email": vet.email,
            "outsider_email": outsider.email,
            "animal_id": animal.id,
            "unweighed_animal_id": unweighed_animal.id,
            "other_animal_id": other_animal.id,
            "herd_id": herd.id,
        }
        db.session.remove()
        yield data
        db.session.remove()
        db.drop_all()


def _csrf(client) -> str:
    response = client.get("/login")
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode()


def _login(client, email: str, password: str) -> None:
    response = client.post(
        "/login",
        data={
            "_csrf_token": _csrf(client),
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 302


def test_analytics_risk_escalates_for_emergency_symptom():
    result = assess_disease_risk(["diarrhea", "seizure"])

    assert result.risk_level == "high"
    assert result.emergency_warning is True
    assert "Co giật" in result.emergency_signs
    assert any("bác sĩ thú y" in line.lower() for line in result.recommendations)
    with pytest.raises(ValueError):
        assess_disease_risk(["unknown-symptom"])
    with pytest.raises(ValueError):
        assess_disease_risk([])


def test_analytics_growth_requires_two_points_and_bounds_forecast():
    empty = analyze_growth([])
    assert empty.available is False

    origin = datetime(2025, 1, 1, tzinfo=timezone.utc)
    points = [
        (origin, 100.0),
        (origin + timedelta(days=10), 110.0),
        (origin + timedelta(days=20), 120.0),
    ]
    result = analyze_growth(points)

    assert result.available is True
    assert result.average_daily_gain == pytest.approx(1.0)
    assert result.forecasts[7] == pytest.approx(127.0)
    assert all(value >= 0 for value in result.forecasts.values())


def test_analytics_feed_plan_totals_from_user_inputs():
    result = calculate_feed_plan(
        [
            {"feed_name": "Cỏ voi", "unit": "kg", "price_per_unit": 2, "amount_per_day": 5},
            {"feed_name": "Cám", "unit": "kg", "price_per_unit": 10, "amount_per_day": 2},
        ],
        period_days=10,
    )

    assert result.total_amount_per_day == pytest.approx(7)
    assert result.total_amount_period == pytest.approx(70)
    assert result.total_daily_cost == pytest.approx(30)
    assert result.total_period_cost == pytest.approx(300)
    assert "Chưa có đủ dữ liệu dinh dưỡng" in result.nutritional_note

    with pytest.raises(ValueError):
        calculate_feed_plan([], period_days=10)


def test_services_enforce_farm_scope_and_veterinarian_read_only(app, ai_data):
    with app.app_context():
        risk = DiseaseRiskService(db.session).assess(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            symptoms=["fever", "cough"],
        )
        assert risk.risk_level == "medium"
        assert db.session.get(DiseaseRiskAssessment, risk.id) is not None

        with pytest.raises(AuthorizationError):
            DiseaseRiskService(db.session).assess(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["vet_id"],
                animal_id=ai_data["animal_id"],
                symptoms=["fever"],
            )

        with pytest.raises(ValidationError):
            WeightRecordService(db.session).add(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                weight_kg="0",
                measured_at=datetime.now(timezone.utc),
            )

        origin = datetime(2025, 1, 1, tzinfo=timezone.utc)
        weights = WeightRecordService(db.session)
        for day, weight in [(1, 100), (11, 110), (21, 120), (31, 130)]:
            weights.add(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                weight_kg=weight,
                measured_at=origin + timedelta(days=day),
            )
        _, analysis, prediction = GrowthPredictionService(db.session).analyze(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            save=True,
            ai_client=FakeStructuredAI(),
        )
        assert analysis.trend == "increasing"
        assert prediction is not None
        assert prediction.ai_model == FakeStructuredAI.model
        assert prediction.confidence == "medium"
        assert prediction.input_snapshot["record_count"] == 4
        assert db.session.get(GrowthPrediction, prediction.id) is not None

        with pytest.raises(AuthorizationError):
            GrowthPredictionService(db.session).analyze(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["vet_id"],
                animal_id=ai_data["animal_id"],
                save=True,
                ai_client=FakeStructuredAI(),
            )
        with pytest.raises(NotFoundError):
            GrowthPredictionService(db.session).analyze(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["other_animal_id"],
                save=True,
                ai_client=FakeStructuredAI(),
            )

        with pytest.raises(AIAnalysisError):
            GrowthPredictionService(db.session).analyze(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                save=False,
                ai_client=FailingStructuredAI(),
            )

        plan, result = FeedPlanService(db.session).create(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            herd_id=None,
            name="Khẩu phần thử",
            period_days=5,
            notes=None,
            items=[
                {
                    "feed_name": "Cỏ voi",
                    "unit": "kg",
                    "price_per_unit": "2",
                    "amount_per_day": "4",
                }
            ],
        )
        assert plan.id is not None
        assert plan.source == "manual"
        assert plan.status == "accepted"
        assert result.total_period_cost == pytest.approx(40)
        assert db.session.get(FeedPlan, plan.id) is not None
        assert len(WeightRecordService(db.session).history(
            farm_id=ai_data["farm_id"], animal_id=ai_data["animal_id"]
        )) == 4


def test_ai_feed_suggestion_is_separate_from_manual_adjustment(app, ai_data):
    with app.app_context():
        origin = datetime(2025, 2, 1, tzinfo=timezone.utc)
        weights = WeightRecordService(db.session)
        for day, weight in [(1, 110), (10, 118)]:
            weights.add(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                weight_kg=weight,
                measured_at=origin + timedelta(days=day),
            )

        service = FeedPlanService(db.session)
        ai_client = CapturingFakeStructuredAI()
        plan, result = service.suggest(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            herd_id=None,
            period_days=10,
            goal="growth",
            ai_client=ai_client,
        )
        assert {
            item["name"] for item in ai_client.feed_context["available_feed"]
        } == {"Cỏ voi", "Cám hỗn hợp"}
        assert plan.source == "ai"
        assert plan.status == "proposed"
        assert plan.ai_model == FakeStructuredAI.model
        assert result.feedings_per_day == 2
        assert plan.confidence == "low"
        assert "Chưa có thành phần dinh dưỡng" in plan.limitations
        assert "Cám gia cầm demo" in plan.limitations
        items = service.items(farm_id=ai_data["farm_id"], plan_id=plan.id)
        assert {item.feed_name for item in items} == {"Cỏ voi", "Cám hỗn hợp"}
        assert all(
            item.nutrients["source"] == "ai_gemini"
            and item.nutrients["price_source"] == "missing"
            for item in items
        )

        with pytest.raises(AIAnalysisError):
            service.suggest(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                herd_id=None,
                period_days=10,
                goal="growth",
                ai_client=CrossSpeciesFeedAI(),
            )
        assert len(db.session.scalars(select(FeedPlan)).all()) == 1

        with pytest.raises(AuthorizationError):
            service.suggest(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["vet_id"],
                animal_id=ai_data["animal_id"],
                herd_id=None,
                period_days=10,
                goal="growth",
                ai_client=FakeStructuredAI(),
            )

        accepted = service.accept(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            plan_id=plan.id,
        )
        assert accepted.status == "accepted"
        assert accepted.accepted_by_user_id == ai_data["owner_id"]
        with pytest.raises(ValidationError):
            service.accept(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                plan_id=plan.id,
            )

        manual, _ = service.create(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            herd_id=None,
            name="Điều chỉnh thủ công",
            period_days=10,
            notes="Tự điều chỉnh lượng ăn.",
            items=[
                {
                    "feed_name": "Cỏ voi",
                    "unit": "kg",
                    "price_per_unit": "2",
                    "amount_per_day": "6",
                }
            ],
        )
        assert manual.source == "manual"
        assert manual.status == "accepted"
        assert db.session.get(FeedPlan, plan.id).status == "accepted"
        assert db.session.get(FeedPlan, plan.id).source == "ai"


def test_owner_flow_disease_risk_growth_and_feed_plan(
    client, app, ai_data, monkeypatch
):
    monkeypatch.setattr(web_module, "_ai_client", lambda: FakeStructuredAI())
    _login(client, ai_data["owner_email"], "synthetic-owner-password")
    csrf = _csrf(client)

    assert client.get("/disease-risk").status_code == 200
    response = client.post(
        "/disease-risk",
        data={
            "_csrf_token": csrf,
            "animal_id": ai_data["animal_id"],
            "symptoms": ["fever", "cough"],
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assessment = db.session.scalar(select(DiseaseRiskAssessment))
        assert assessment is not None
        assessment_id = assessment.id
    detail = client.get(f"/disease-risk/{assessment_id}")
    assert detail.status_code == 200
    assert "Sàng lọc tham khảo".encode() in detail.data

    growth_page = client.get(f"/growth?animal_id={ai_data['animal_id']}")
    assert growth_page.status_code == 200
    assert f'value="{ai_data["animal_id"]}" selected'.encode() in growth_page.data
    assert "Bò".encode() in growth_page.data
    assert "Đàn AI".encode() in growth_page.data

    other_farm_page = client.get(f"/growth?animal_id={ai_data['other_animal_id']}")
    assert other_farm_page.status_code == 200
    assert f'value="{ai_data["other_animal_id"]}" selected'.encode() not in other_farm_page.data
    assert "Không tìm thấy vật nuôi trong trang trại của bạn.".encode() in other_farm_page.data

    unweighed_page = client.get(f"/growth?animal_id={ai_data['unweighed_animal_id']}")
    assert unweighed_page.status_code == 200
    assert f'value="{ai_data["unweighed_animal_id"]}" selected'.encode() in unweighed_page.data
    assert "Chưa có lần cân nào cho vật nuôi này.".encode() in unweighed_page.data

    with app.app_context():
        prediction_count = len(db.session.scalars(select(GrowthPrediction)).all())
    response = client.post(
        "/growth",
        data={
            "_csrf_token": csrf,
            "action": "analyze_growth",
            "animal_id": ai_data["unweighed_animal_id"],
        },
    )
    assert response.status_code == 302
    assert "/growth/predictions/" not in response.headers["Location"]
    with app.app_context():
        assert len(db.session.scalars(select(GrowthPrediction)).all()) == prediction_count

    for day, weight in [(1, 100), (11, 110), (21, 120)]:
        response = client.post(
            "/growth",
            data={
                "_csrf_token": csrf,
                "action": "add_weight",
                "animal_id": ai_data["animal_id"],
                "weight_kg": str(weight),
                "measured_at": f"2025-01-{day:02d}T08:00",
            },
        )
        assert response.status_code == 302
    response = client.post(
        "/growth",
        data={
            "_csrf_token": csrf,
            "action": "analyze_growth",
            "animal_id": ai_data["animal_id"],
        },
    )
    assert response.status_code == 302
    assert "/growth/predictions/" in response.headers["Location"]
    with app.app_context():
        assert db.session.scalar(select(WeightRecord)) is not None
        prediction = db.session.scalar(select(GrowthPrediction))
        assert prediction is not None
        assert prediction.ai_model == FakeStructuredAI.model
        prediction_id = prediction.id
    growth_detail = client.get(f"/growth/predictions/{prediction_id}")
    assert growth_detail.status_code == 200
    assert "Kết quả dự báo AI".encode() in growth_detail.data
    assert "gemini-test-structured".encode() in growth_detail.data

    assert client.get("/feed-plans").status_code == 200
    response = client.post(
        "/feed-plans/suggest",
        data={
            "_csrf_token": csrf,
            "animal_id": ai_data["animal_id"],
            "period_days": "10",
            "goal": "growth",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        plan = db.session.scalar(select(FeedPlan).where(FeedPlan.source == "ai"))
        assert plan is not None
        assert plan.status == "proposed"
        plan_id = plan.id
    plan_page = client.get(f"/feed-plans/{plan_id}")
    assert plan_page.status_code == 200
    assert "Đề xuất AI".encode() in plan_page.data
    assert "Chấp nhận đề xuất".encode() in plan_page.data
    assert client.post(
        f"/feed-plans/{plan_id}/accept",
        data={"_csrf_token": csrf},
    ).status_code == 302
    with app.app_context():
        assert db.session.get(FeedPlan, plan_id).status == "accepted"

    response = client.post(
        "/feed-plans",
        data={
            "_csrf_token": csrf,
            "name": "Khẩu phần vỗ béo",
            "target_kind": "animal",
            "animal_id": ai_data["animal_id"],
            "period_days": "10",
            "feed_name": ["Cỏ voi"],
            "unit": ["kg"],
            "price_per_unit": ["2"],
            "amount_per_day": ["5"],
            "item_notes": [""],
        },
    )
    assert response.status_code == 302
    with app.app_context():
        plan = db.session.scalar(select(FeedPlan).where(FeedPlan.source == "manual"))
        assert plan is not None
        plan_id = plan.id
    plan_page = client.get(f"/feed-plans/{plan_id}")
    assert plan_page.status_code == 200
    assert "Khẩu phần vỗ béo".encode() in plan_page.data


def test_growth_local_fallback_is_labeled_and_deduplicated(app, ai_data):
    origin = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with app.app_context():
        weights = WeightRecordService(db.session)
        for day, weight in [(1, 100), (11, 110), (21, 120), (31, 130)]:
            weights.add(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                weight_kg=weight,
                measured_at=origin + timedelta(days=day),
            )

        service = GrowthPredictionService(db.session)
        _, result, prediction = service.analyze(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            save=True,
            ai_client=UnavailableStructuredAI(),
        )
        assert result.raw["provider"] == "local"
        assert result.model == "local-linear-regression-v1"
        assert prediction.method == "local-growth-fallback-v1"
        assert prediction.ai_model == "local-linear-regression-v1"
        assert "không phải Gemini" in prediction.limitations
        assert "không phải do Gemini" in prediction.explanation
        first_id = prediction.id

        _, _, repeated = service.analyze(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            save=True,
            ai_client=UnavailableStructuredAI(),
        )
        assert repeated.id == first_id
        assert len(db.session.scalars(select(GrowthPrediction)).all()) == 1


def test_growth_transient_failure_without_fallback_saves_nothing(app, ai_data):
    origin = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with app.app_context():
        weights = WeightRecordService(db.session)
        for day, weight in [(1, 100), (11, 110)]:
            weights.add(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                weight_kg=weight,
                measured_at=origin + timedelta(days=day),
            )

        with pytest.raises(AIProviderUnavailableError):
            GrowthPredictionService(db.session).analyze(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                save=True,
                ai_client=UnavailableStructuredAI(),
                allow_fallback=False,
            )
        assert db.session.scalars(select(GrowthPrediction)).all() == []


def test_growth_web_shows_local_fallback_source(client, app, ai_data, monkeypatch):
    origin = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with app.app_context():
        weights = WeightRecordService(db.session)
        for day, weight in [(1, 100), (11, 110), (21, 120)]:
            weights.add(
                farm_id=ai_data["farm_id"],
                actor_user_id=ai_data["owner_id"],
                animal_id=ai_data["animal_id"],
                weight_kg=weight,
                measured_at=origin + timedelta(days=day),
            )

    monkeypatch.setattr(
        web_module,
        "_ai_client",
        lambda: UnavailableStructuredAI(),
    )
    _login(client, ai_data["owner_email"], "synthetic-owner-password")
    csrf = _csrf(client)
    response = client.post(
        "/growth",
        data={
            "_csrf_token": csrf,
            "action": "analyze_growth",
            "animal_id": ai_data["animal_id"],
        },
    )
    assert response.status_code == 302
    assert "/growth/predictions/" in response.headers["Location"]

    detail = client.get(response.headers["Location"])
    assert detail.status_code == 200
    assert "Kết quả mô hình nội bộ".encode() in detail.data
    assert "mô hình dự phòng nội bộ".encode() in detail.data
    assert "không phải do Gemini tạo".encode() in detail.data
    assert "local-growth-fallback-v1".encode() in detail.data

    client.post(
        "/growth",
        data={
            "_csrf_token": csrf,
            "action": "analyze_growth",
            "animal_id": ai_data["animal_id"],
        },
    )
    with app.app_context():
        assert len(db.session.scalars(select(GrowthPrediction)).all()) == 1


def test_outsider_and_veterinarian_cannot_bypass_scope(client, app, ai_data):
    with app.app_context():
        assessment = DiseaseRiskService(db.session).assess(
            farm_id=ai_data["farm_id"],
            actor_user_id=ai_data["owner_id"],
            animal_id=ai_data["animal_id"],
            symptoms=["fever"],
        )
        assessment_id = assessment.id

    outsider = app.test_client()
    _login(outsider, ai_data["outsider_email"], "synthetic-outsider-password")
    assert outsider.get(f"/disease-risk/{assessment_id}").status_code == 404

    vet = app.test_client()
    _login(vet, ai_data["vet_email"], "synthetic-vet-password")
    assert vet.get("/disease-risk").status_code == 200
    response = vet.post(
        "/disease-risk",
        data={
            "_csrf_token": _csrf(vet),
            "animal_id": ai_data["animal_id"],
            "symptoms": ["fever"],
        },
    )
    assert response.status_code == 403
    with app.app_context():
        assert len(db.session.scalars(select(DiseaseRiskAssessment)).all()) == 1
