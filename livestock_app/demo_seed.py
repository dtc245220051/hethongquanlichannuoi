import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from livestock_app.models import (
    Alert,
    Animal,
    AuditLog,
    Barn,
    Farm,
    FarmMembership,
    FeedInventoryBalance,
    FeedItem,
    FeedTransaction,
    Herd,
    HealthRecord,
    Species,
    SyntheticDataset,
    User,
    VaccinationOutcome,
    VaccinationSchedule,
)
from livestock_app.extensions import db

DEMO_DATASET_MARKER = "livestock-ai-agent-demo-v1"
DEMO_SEED_VERSION = "2026-10-demo-v1"
DEMO_OWNER_EMAIL = "demo-owner@example.invalid"
DEMO_DATABASE_ATTESTATION = "VERIFIED_PRIVATE_NON_PRODUCTION_DEMO_DATABASE"


class DemoSeedError(RuntimeError):
    """Raised when demo seeding cannot be proven safe or consistent."""


@dataclass(frozen=True)
class DemoSeedResult:
    created: bool
    farm_id: str
    dataset_marker: str


_SEEDED_MODELS = (
    (Barn, 3),
    (Herd, 5),
    (Animal, 4),
    (FeedItem, 2),
    (FeedInventoryBalance, 2),
    (FeedTransaction, 2),
    (VaccinationSchedule, 2),
    (VaccinationOutcome, 2),
    (HealthRecord, 2),
    (Alert, 2),
    (AuditLog, 1),
)


def _validate_test_database(session: Session, *, app_env: str, testing: bool) -> None:
    bind = session.get_bind()
    if (
        app_env != "test"
        or not testing
        or bind.dialect.name != "sqlite"
        or bind.url.database != ":memory:"
    ):
        raise DemoSeedError(
            "Test seed chỉ chạy trên SQLite in-memory với TESTING=True."
        )


def validate_demo_database(
    session: Session,
    *,
    app_env: str,
    database_url: str | URL,
    configured_database_name: str,
    confirmed_database_name: str,
    expected_server_uuid: str,
    observed_attestation: str,
) -> None:
    if app_env != "demo":
        raise DemoSeedError("Seed demo chỉ chạy khi APP_ENV=demo.")
    if observed_attestation != DEMO_DATABASE_ATTESTATION:
        raise DemoSeedError(
            "Thiếu xác nhận database riêng, không production từ operator."
        )
    try:
        url = make_url(database_url)
    except (ArgumentError, ValueError) as exc:
        raise DemoSeedError("Không xác minh được URL database demo.") from exc
    if url.drivername != "mysql+pymysql" or not url.database:
        raise DemoSeedError("Database demo phải là MySQL riêng có tên rõ ràng.")
    if not re.fullmatch(r"livestock_demo_[a-z0-9_]{1,48}", url.database):
        raise DemoSeedError("Tên database phải theo tiền tố livestock_demo_.")
    if configured_database_name != url.database or confirmed_database_name != url.database:
        raise DemoSeedError(
            "Tên database trong cấu hình, xác nhận và kết nối không khớp."
        )
    if not expected_server_uuid or len(expected_server_uuid) > 64:
        raise DemoSeedError("Cần cấu hình UUID MySQL đã xác minh cho database demo.")
    try:
        actual_name, actual_uuid = session.execute(
            text("SELECT DATABASE(), @@server_uuid")
        ).one()
    except SQLAlchemyError as exc:
        raise DemoSeedError(
            "Không thể xác minh danh tính máy chủ MySQL demo."
        ) from exc
    if actual_name != url.database or actual_uuid != expected_server_uuid:
        raise DemoSeedError(
            "Danh tính database/máy chủ thực tế không khớp cấu hình đã xác nhận."
        )


def _counts_for_dataset(session: Session, dataset: SyntheticDataset) -> dict[str, int]:
    return {
        model.__tablename__: int(
            session.scalar(
                select(func.count(model.id)).where(
                    model.farm_id == dataset.farm_id,
                    model.synthetic_dataset_id == dataset.id,
                )
            )
            or 0
        )
        for model, _expected_count in _SEEDED_MODELS
    }


def _existing_dataset(
    session: Session, dataset: SyntheticDataset
) -> DemoSeedResult:
    expected = {
        model.__tablename__: count for model, count in _SEEDED_MODELS
    }
    actual = _counts_for_dataset(session, dataset)
    if dataset.seed_version != DEMO_SEED_VERSION or actual != expected:
        raise DemoSeedError(
            "Dataset demo đã tồn tại nhưng không còn đúng phiên bản/cấu trúc; "
            "không sửa hoặc ghi đè dữ liệu hiện có."
        )
    return DemoSeedResult(
        created=False,
        farm_id=dataset.farm_id,
        dataset_marker=dataset.marker,
    )


def _require_empty_business_tables(session: Session) -> None:
    for table in db.Model.metadata.sorted_tables:
        if table.name == Species.__tablename__:
            continue
        try:
            row_count = session.scalar(select(func.count()).select_from(table))
        except SQLAlchemyError as exc:
            raise DemoSeedError(
                "Không thể xác minh database trống và đã migrate; seed bị từ chối."
            ) from exc
        if row_count:
            raise DemoSeedError(
                "Database đã có dữ liệu nghiệp vụ; seed không sửa hoặc thêm dữ liệu."
            )


def seed_synthetic_demo(
    session: Session,
    *,
    app_env: str,
    testing: bool,
    database_url: str | URL,
    configured_database_name: str = "",
    confirmed_database_name: str = "",
    expected_server_uuid: str = "",
    observed_attestation: str = "",
    owner_password: str | None,
    agent_mode: str = "disabled",
) -> DemoSeedResult:
    if app_env == "test":
        _validate_test_database(session, app_env=app_env, testing=testing)
    else:
        if agent_mode != "mock":
            raise DemoSeedError(
                "Seed demo chỉ dùng cùng AGENT_MODE=mock đã được phê duyệt cho demo."
            )
        validate_demo_database(
            session,
            app_env=app_env,
            database_url=database_url,
            configured_database_name=configured_database_name,
            confirmed_database_name=confirmed_database_name,
            expected_server_uuid=expected_server_uuid,
            observed_attestation=observed_attestation,
        )

    try:
        dataset = session.scalar(
            select(SyntheticDataset).where(
                SyntheticDataset.marker == DEMO_DATASET_MARKER
            )
        )
        if dataset is not None:
            return _existing_dataset(session, dataset)

        _require_empty_business_tables(session)
        if not isinstance(owner_password, str) or len(owner_password) < 12:
            raise DemoSeedError("Mật khẩu demo phải có ít nhất 12 ký tự.")
        if session.scalar(
            select(User.id).where(User.email == DEMO_OWNER_EMAIL)
        ) is not None:
            raise DemoSeedError(
                "Email demo đã tồn tại mà không có dataset tương ứng; seed bị từ chối."
            )

        now = datetime.now(timezone.utc)
        farm = Farm(name="Trang trại trình diễn (dữ liệu tổng hợp)")
        owner = User(
            email=DEMO_OWNER_EMAIL,
            password_hash=generate_password_hash(owner_password),
            display_name="Chủ trại demo",
            status="active",
        )
        session.add_all([farm, owner])
        session.flush()
        membership = FarmMembership(
            farm_id=farm.id,
            user_id=owner.id,
            role="owner",
        )
        dataset = SyntheticDataset(
            farm_id=farm.id,
            marker=DEMO_DATASET_MARKER,
            seed_version=DEMO_SEED_VERSION,
            created_by_user_id=owner.id,
            created_at=now,
        )
        session.add_all([membership, dataset])
        session.flush()

        species_by_code = {
            species.code: species
            for species in session.scalars(
                select(Species).where(Species.code.in_(("cattle", "swine", "poultry")))
            )
        }
        for code, name in (
            ("cattle", "Bò"),
            ("swine", "Lợn"),
            ("poultry", "Gia cầm"),
        ):
            if code not in species_by_code:
                species = Species(code=code, name_vi=name, active=True)
                session.add(species)
                species_by_code[code] = species

        barns = [
            Barn(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                name=name,
                capacity_heads=capacity,
                location_note="Khu demo tổng hợp",
                status="active",
            )
            for name, capacity in (
                ("Chuồng A - Gia cầm", 100),
                ("Chuồng B - Bò", 20),
                ("Chuồng C - Lợn", 20),
            )
        ]
        session.add_all(barns)
        session.flush()

        herd_specs = (
            ("Đàn gà thịt A", "poultry", barns[0].id, 32),
            ("Đàn vịt B", "poultry", barns[0].id, 26),
            ("Đàn gà hậu bị C", "poultry", barns[0].id, 18),
            ("Đàn bò sữa demo", "cattle", barns[1].id, 0),
            ("Đàn lợn thịt demo", "swine", barns[2].id, 0),
        )
        herds = [
            Herd(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                name=name,
                species_code=species_code,
                barn_id=barn_id,
                unidentified_count=count,
                status="active",
            )
            for name, species_code, barn_id, count in herd_specs
        ]
        session.add_all(herds)
        session.flush()
        animals = [
            Animal(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                species_code="cattle",
                herd_id=herds[3].id,
                barn_id=barns[1].id,
                identifier=f"DEMO-COW-{index:02d}",
                display_name=f"Bò demo {index:02d}",
                status="active",
            )
            for index in (1, 2)
        ] + [
            Animal(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                species_code="swine",
                herd_id=herds[4].id,
                barn_id=barns[2].id,
                identifier=f"DEMO-PIG-{index:02d}",
                display_name=f"Lợn demo {index:02d}",
                status="active",
            )
            for index in (1, 2)
        ]
        session.add_all(animals)
        feed_items = [
            FeedItem(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                name=name,
                base_unit_code="kg",
                low_stock_threshold_kg=Decimal(threshold),
                status="active",
            )
            for name, threshold in (
                ("Cám gia cầm demo", "15.000"),
                ("Thức ăn hỗn hợp demo", "20.000"),
            )
        ]
        session.add_all(feed_items)
        session.flush()
        balances = [
            FeedInventoryBalance(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                feed_item_id=feed_items[0].id,
                quantity_kg=Decimal("42.500"),
                updated_at=now,
            ),
            FeedInventoryBalance(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                feed_item_id=feed_items[1].id,
                quantity_kg=Decimal("8.000"),
                updated_at=now,
            ),
        ]
        session.add_all(balances)
        session.add_all(
            [
                FeedTransaction(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    feed_item_id=feed_items[0].id,
                    transaction_type="receipt",
                    quantity_delta_kg=Decimal("42.500"),
                    unit_code="kg",
                    balance_before_kg=Decimal("0.000"),
                    balance_after_kg=Decimal("42.500"),
                    reason="Tồn đầu kỳ dữ liệu tổng hợp",
                    occurred_at=now,
                    recorded_by_user_id=owner.id,
                ),
                FeedTransaction(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    feed_item_id=feed_items[1].id,
                    transaction_type="receipt",
                    quantity_delta_kg=Decimal("8.000"),
                    unit_code="kg",
                    balance_before_kg=Decimal("0.000"),
                    balance_after_kg=Decimal("8.000"),
                    reason="Tồn đầu kỳ dữ liệu tổng hợp",
                    occurred_at=now,
                    recorded_by_user_id=owner.id,
                ),
            ]
        )

        schedules = [
            VaccinationSchedule(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                herd_id=herds[0].id,
                vaccine_name="Lịch tiêm demo - sắp đến hạn",
                due_at=now + timedelta(days=3),
                status="scheduled",
                created_by_user_id=owner.id,
                created_at=now,
            ),
            VaccinationSchedule(
                farm_id=farm.id,
                synthetic_dataset_id=dataset.id,
                animal_id=animals[0].id,
                vaccine_name="Lịch tiêm demo - quá hạn",
                due_at=now - timedelta(days=2),
                status="scheduled",
                created_by_user_id=owner.id,
                created_at=now,
            ),
        ]
        session.add_all(schedules)
        session.flush()
        session.add_all(
            [
                VaccinationOutcome(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    schedule_id=schedules[0].id,
                    outcome="needs_verification",
                    recorded_by_user_id=owner.id,
                    notes="Dữ liệu demo: cần xác minh kết quả thực tế.",
                ),
                VaccinationOutcome(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    schedule_id=schedules[1].id,
                    outcome="administered",
                    occurred_at=now - timedelta(days=2),
                    performed_by_user_id=owner.id,
                    recorded_by_user_id=owner.id,
                    notes="Kết quả demo có thời điểm/người thực hiện tổng hợp.",
                ),
            ]
        )
        session.add_all(
            [
                HealthRecord(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    animal_id=animals[0].id,
                    recorded_at=now - timedelta(days=1),
                    observations="Dữ liệu tổng hợp: ghi nhận ăn uống bình thường.",
                    action_taken="Theo dõi định kỳ trong tình huống demo.",
                    recorded_by_user_id=owner.id,
                ),
                HealthRecord(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    herd_id=herds[1].id,
                    recorded_at=now - timedelta(days=3),
                    observations="Dữ liệu tổng hợp: một số con cần theo dõi thêm.",
                    action_taken="Ghi nhận để trình diễn workflow.",
                    follow_up_at=now + timedelta(days=1),
                    recorded_by_user_id=owner.id,
                ),
                Alert(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    alert_type="stock",
                    severity="medium",
                    title="Tồn thức ăn demo gần ngưỡng",
                    description="Cảnh báo tổng hợp để trình diễn; không phải dữ liệu thật.",
                    status="new",
                    created_at=now,
                ),
                Alert(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    alert_type="health_follow_up",
                    severity="low",
                    title="Theo dõi ghi nhận demo",
                    description="Nhắc việc tổng hợp; không phải chẩn đoán y khoa.",
                    herd_id=herds[1].id,
                    status="acknowledged",
                    created_at=now - timedelta(hours=2),
                ),
                AuditLog(
                    farm_id=farm.id,
                    synthetic_dataset_id=dataset.id,
                    actor_user_id=owner.id,
                    action="demo.dataset_seeded",
                    entity_type="synthetic_dataset",
                    entity_id=dataset.id,
                    outcome="success",
                    request_id="demo-seed-v1",
                    occurred_at=now,
                    metadata_redacted={"marker": DEMO_DATASET_MARKER},
                ),
            ]
        )
        session.flush()
        counts = _counts_for_dataset(session, dataset)
        expected = {
            model.__tablename__: count for model, count in _SEEDED_MODELS
        }
        if counts != expected:
            raise DemoSeedError(
                "Số lượng bản ghi demo tạo ra không khớp; transaction bị hủy."
            )
        session.commit()
        return DemoSeedResult(
            created=True,
            farm_id=farm.id,
            dataset_marker=dataset.marker,
        )
    except DemoSeedError:
        session.rollback()
        raise
    except (SQLAlchemyError, ValueError) as exc:
        session.rollback()
        raise DemoSeedError(
            "Không thể tạo dữ liệu demo; transaction đã được hủy."
        ) from exc
