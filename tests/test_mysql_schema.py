import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError

from livestock_app import create_app, db
from livestock_app.models import (
    Farm,
    FarmMembership,
    FeedInventoryBalance,
    FeedItem,
    FeedTransaction,
    User,
)
from livestock_app.services import InsufficientStockError
from livestock_app.services.feed import FeedInventoryService


DATABASE_URL = os.environ.get("MYSQL_TEST_DATABASE_URL")
EXPECTED_DATABASE_NAME = os.environ.get("MYSQL_TEST_DATABASE_NAME", "")
EXPECTED_SERVER_UUID = os.environ.get("MYSQL_TEST_SERVER_UUID", "")
DISPOSABLE_DATABASE_ATTESTATION = os.environ.get(
    "MYSQL_TEST_DISPOSABLE_ATTESTATION", ""
)
_REQUIRED_DISPOSABLE_ATTESTATION = "I_CONFIRM_THIS_IS_A_DISPOSABLE_MYSQL_TEST_DATABASE"

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
EXPECTED_TABLES = {
    "farms",
    "species",
    "users",
    "data_retention_policies",
    "platform_authorizations",
    "synthetic_datasets",
    "user_farm_roles",
    "audit_logs",
    "barns",
    "farm_permission_grants",
    "feed_items",
    "support_access_requests",
    "barn_capacity_exception_requests",
    "feed_inventory_balances",
    "herds",
    "support_access_grants",
    "animals",
    "feed_transactions",
    "alerts",
    "health_records",
    "herd_events",
    "vaccination_schedules",
    "alert_events",
    "vaccination_outcomes",
    "vaccination_schedule_events",
}


def _migration(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "mysql-migration-test-only",
            "DATABASE_URL": DATABASE_URL or "",
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "flask",
            "--app",
            "livestock_app:create_app",
            "db",
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_migration_succeeded(
    result: subprocess.CompletedProcess[str], operation: str
) -> None:
    if result.returncode != 0:
        pytest.fail(
            f"MySQL disposable migration step '{operation}' failed; "
            "captured output is withheld to prevent credential disclosure.",
            pytrace=False,
        )


def _validate_mysql_test_target(
    database_url: str,
    *,
    expected_database_name: str,
    expected_server_uuid: str,
    attestation: str,
    actual_database_name: str | None = None,
    actual_server_uuid: str | None = None,
    existing_tables: set[str] | None = None,
) -> None:
    try:
        url = make_url(database_url)
    except (ArgumentError, ValueError) as exc:
        raise ValueError("MYSQL_TEST_DATABASE_URL is invalid.") from exc
    if url.drivername != "mysql+pymysql" or not url.database:
        raise ValueError(
            "MySQL integration tests require mysql+pymysql and a database name."
        )
    if not re.fullmatch(r"livestock_test_[a-z0-9_]{1,48}", url.database):
        raise ValueError("MySQL test database name must use livestock_test_ prefix.")
    if expected_database_name != url.database:
        raise ValueError("MYSQL_TEST_DATABASE_NAME does not match the URL database.")
    if not expected_server_uuid or len(expected_server_uuid) > 64:
        raise ValueError("MYSQL_TEST_SERVER_UUID must identify the disposable server.")
    if attestation != _REQUIRED_DISPOSABLE_ATTESTATION:
        raise ValueError("Disposable MySQL operator attestation is missing.")
    if actual_database_name is not None and actual_database_name != url.database:
        raise ValueError(
            "Connected MySQL database does not match the configured name."
        )
    if actual_server_uuid is not None and actual_server_uuid != expected_server_uuid:
        raise ValueError(
            "Connected MySQL server UUID does not match the configured UUID."
        )
    if existing_tables:
        raise ValueError("MySQL test database must be empty before migrations.")


@pytest.fixture
def verified_mysql_test_database():
    if not DATABASE_URL:
        pytest.skip(
            "MYSQL_TEST_DATABASE_URL is unset; MySQL disposable integration not run."
        )
    try:
        _validate_mysql_test_target(
            DATABASE_URL,
            expected_database_name=EXPECTED_DATABASE_NAME,
            expected_server_uuid=EXPECTED_SERVER_UUID,
            attestation=DISPOSABLE_DATABASE_ATTESTATION,
        )
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)

    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            actual_database, actual_server_uuid = connection.execute(
                text("SELECT DATABASE(), @@server_uuid")
            ).one()
            existing_tables = set(inspect(connection).get_table_names())
        try:
            _validate_mysql_test_target(
                DATABASE_URL,
                expected_database_name=EXPECTED_DATABASE_NAME,
                expected_server_uuid=EXPECTED_SERVER_UUID,
                attestation=DISPOSABLE_DATABASE_ATTESTATION,
                actual_database_name=actual_database,
                actual_server_uuid=actual_server_uuid,
                existing_tables=existing_tables,
            )
        except ValueError as exc:
            pytest.fail(str(exc), pytrace=False)
        yield
    finally:
        engine.dispose()


def _expect_database_rejection(connection, statement: str, parameters: dict) -> None:
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(statement), parameters)


def test_mysql_initial_migration_constraints_triggers_and_downgrade(
    verified_mysql_test_database,
):
    del verified_mysql_test_database
    upgrade_result = _migration("upgrade")
    _assert_migration_succeeded(upgrade_result, "upgrade")
    check_result = _migration("check")
    _assert_migration_succeeded(check_result, "check")

    engine = create_engine(DATABASE_URL)
    farm_a, farm_b, farm_without_children, user_id, member_id, barn_b, feed_id = (
        str(uuid4()) for _ in range(7)
    )
    species_id, herd_id, herd_b_id, schedule_id = (str(uuid4()) for _ in range(4))
    try:
        inspector = inspect(engine)
        assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))

        expected_checks = {
            constraint.name
            for table in db.metadata.tables.values()
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        actual_checks = {
            constraint["name"]
            for table in EXPECTED_TABLES
            for constraint in inspector.get_check_constraints(table)
        }
        assert expected_checks.issubset(actual_checks)

        expected_foreign_keys = {
            constraint.name
            for table in db.metadata.tables.values()
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        actual_foreign_keys = {
            foreign_key["name"]
            for table in EXPECTED_TABLES
            for foreign_key in inspector.get_foreign_keys(table)
        }
        assert expected_foreign_keys.issubset(actual_foreign_keys)

        expected_unique_constraints = {
            constraint.name
            for table in db.metadata.tables.values()
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        actual_unique_constraints = {
            constraint["name"]
            for table in EXPECTED_TABLES
            for constraint in inspector.get_unique_constraints(table)
        }
        assert expected_unique_constraints.issubset(actual_unique_constraints)

        with engine.begin() as connection:
            version = connection.execute(text("SELECT VERSION()")).scalar_one()
            assert version
            print(f"MySQL disposable version: {version}")
            connection.execute(
                text(
                    "INSERT INTO farms (id, name, timezone, status) "
                    "VALUES (:id, 'Farm A', 'UTC', 'active'), "
                    "(:other, 'Farm B', 'UTC', 'active'), "
                    "(:empty, 'Empty Farm', 'UTC', 'active')"
                ),
                {"id": farm_a, "other": farm_b, "empty": farm_without_children},
            )
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, email, password_hash, display_name, status) "
                    "VALUES (:id, 'test@example.invalid', 'not-a-secret', "
                    "'Synthetic Test', 'active')"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO user_farm_roles (id, farm_id, user_id, role) "
                    "VALUES (:id, :farm, :user, 'owner')"
                ),
                {"id": member_id, "farm": farm_a, "user": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO species (id, code, name_vi, active) "
                    "VALUES (:id, 'test-species', 'Loài tổng hợp', 1)"
                ),
                {"id": species_id},
            )
            connection.execute(
                text(
                    "INSERT INTO barns (id, farm_id, name, capacity_heads, status) "
                    "VALUES (:id, :farm, 'Barn B', 10, 'active')"
                ),
                {"id": barn_b, "farm": farm_b},
            )
            connection.execute(
                text(
                    "INSERT INTO feed_items "
                    "(id, farm_id, name, base_unit_code, status) "
                    "VALUES (:id, :farm, 'Feed', 'kg', 'active')"
                ),
                {"id": feed_id, "farm": farm_a},
            )
            connection.execute(
                text(
                    "INSERT INTO herds "
                    "(id, farm_id, name, species_code, unidentified_count, status) "
                    "VALUES (:id, :farm, 'Synthetic herd', 'test-species', 3, 'active')"
                ),
                {"id": herd_id, "farm": farm_a},
            )
            connection.execute(
                text(
                    "INSERT INTO herds "
                    "(id, farm_id, name, species_code, unidentified_count, status) "
                    "VALUES (:id, :farm, 'Other synthetic herd', "
                    "'test-species', 1, 'active')"
                ),
                {"id": herd_b_id, "farm": farm_b},
            )
            connection.execute(
                text(
                    "INSERT INTO feed_inventory_balances "
                    "(id, farm_id, feed_item_id, quantity_kg, updated_at) "
                    "VALUES (:id, :farm, :feed, 1.250, UTC_TIMESTAMP())"
                ),
                {"id": str(uuid4()), "farm": farm_a, "feed": feed_id},
            )
            connection.execute(
                text(
                    "INSERT INTO vaccination_schedules "
                    "(id, farm_id, herd_id, vaccine_name, due_at, status, "
                    "created_by_user_id, created_at) "
                    "VALUES (:id, :farm, :herd, 'Vaccine', UTC_TIMESTAMP(), "
                    "'scheduled', :user, UTC_TIMESTAMP())"
                ),
                {
                    "id": schedule_id,
                    "farm": farm_a,
                    "herd": herd_id,
                    "user": user_id,
                },
            )
            _expect_database_rejection(
                connection,
                "INSERT INTO barns "
                "(id, farm_id, name, capacity_heads, status) "
                "VALUES (:id, :farm, 'Invalid capacity', 0, 'active')",
                {"id": str(uuid4()), "farm": farm_a},
            )
            _expect_database_rejection(
                connection,
                "INSERT INTO herds "
                "(id, farm_id, name, species_code, barn_id, "
                "unidentified_count, status) "
                "VALUES (:id, :farm, 'Cross-farm herd', 'test-species', "
                ":barn, 1, 'active')",
                {"id": str(uuid4()), "farm": farm_a, "barn": barn_b},
            )
            _expect_database_rejection(
                connection,
                "INSERT INTO feed_items "
                "(id, farm_id, name, base_unit_code, status) "
                "VALUES (:id, :farm, 'Invalid unit', 'bag', 'active')",
                {"id": str(uuid4()), "farm": farm_a},
            )
            _expect_database_rejection(
                connection,
                "INSERT INTO feed_inventory_balances "
                "(id, farm_id, feed_item_id, quantity_kg, updated_at) "
                "VALUES (:id, :farm, :feed, -1, UTC_TIMESTAMP())",
                {"id": str(uuid4()), "farm": farm_a, "feed": feed_id},
            )
            _expect_database_rejection(
                connection,
                "INSERT INTO vaccination_schedules "
                "(id, farm_id, vaccine_name, due_at, status, "
                "created_by_user_id, created_at) "
                "VALUES (:id, :farm, 'Vaccine', UTC_TIMESTAMP(), 'scheduled', "
                ":user, UTC_TIMESTAMP())",
                {"id": str(uuid4()), "farm": farm_a, "user": user_id},
            )
            _expect_database_rejection(
                connection,
                "INSERT INTO vaccination_schedules "
                "(id, farm_id, herd_id, vaccine_name, due_at, status, "
                "created_by_user_id, created_at) "
                "VALUES (:id, :farm, :herd, 'Cross-farm vaccine', "
                "UTC_TIMESTAMP(), 'scheduled', :user, UTC_TIMESTAMP())",
                {
                    "id": str(uuid4()),
                    "farm": farm_a,
                    "herd": herd_b_id,
                    "user": user_id,
                },
            )
            _expect_database_rejection(
                connection,
                "INSERT INTO vaccination_outcomes "
                "(id, farm_id, schedule_id, outcome, recorded_by_user_id) "
                "VALUES (:id, :farm, :schedule, 'administered', :user)",
                {
                    "id": str(uuid4()),
                    "farm": farm_a,
                    "schedule": schedule_id,
                    "user": user_id,
                },
            )

        with engine.connect() as connection:
            with pytest.raises(DBAPIError):
                with connection.begin():
                    connection.execute(
                        text("DELETE FROM farms WHERE id = :id"),
                        {"id": farm_without_children},
                    )

        with engine.connect() as connection:
            trigger_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.triggers "
                    "WHERE trigger_schema = DATABASE() AND trigger_name LIKE "
                    "'block_delete\\_%'"
                )
            ).scalar_one()
            assert trigger_count == 24
    finally:
        engine.dispose()

    downgrade_result = _migration("downgrade", "base")
    _assert_migration_succeeded(downgrade_result, "downgrade")
    engine = create_engine(DATABASE_URL)
    try:
        inspector = inspect(engine)
        assert not EXPECTED_TABLES.intersection(inspector.get_table_names())
        with engine.connect() as connection:
            trigger_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.triggers "
                    "WHERE trigger_schema = DATABASE() AND trigger_name LIKE "
                    "'block_delete\\_%'"
                )
            ).scalar_one()
            assert trigger_count == 0
    finally:
        engine.dispose()


def test_mysql_concurrent_consumption_cannot_make_stock_negative(
    verified_mysql_test_database,
):
    del verified_mysql_test_database
    upgrade_result = _migration("upgrade")
    _assert_migration_succeeded(upgrade_result, "upgrade")
    app = create_app(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "mysql-migration-test-only",
            "SQLALCHEMY_DATABASE_URI": DATABASE_URL,
            "TESTING": True,
        }
    )
    farm_id, owner_id, member_id, item_id, balance_id = (
        str(uuid4()) for _ in range(5)
    )
    with app.app_context():
        db.session.add_all(
            [
                Farm(id=farm_id, name="Synthetic concurrency farm"),
                User(
                    id=owner_id,
                    email="concurrency@example.invalid",
                    password_hash="not-a-secret",
                    display_name="Synthetic Owner",
                ),
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                FarmMembership(
                    id=member_id, farm_id=farm_id, user_id=owner_id, role="owner"
                ),
                FeedItem(
                    id=item_id,
                    farm_id=farm_id,
                    name="Synthetic feed",
                    base_unit_code="kg",
                    status="active",
                ),
            ]
        )
        db.session.flush()
        db.session.add(
            FeedInventoryBalance(
                id=balance_id,
                farm_id=farm_id,
                feed_item_id=item_id,
                quantity_kg=Decimal("1.000"),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        db.session.remove()

    start = Barrier(2)

    def consume_once():
        with app.app_context():
            try:
                start.wait(timeout=15)
                result = FeedInventoryService(db.session).record_transaction(
                    farm_id=farm_id,
                    actor_user_id=owner_id,
                    feed_item_id=item_id,
                    transaction_type="consumption",
                    quantity_kg=Decimal("0.750"),
                )
                return ("success", result.balance_after_kg)
            except InsufficientStockError:
                return ("insufficient", None)
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume_once(), range(2)))

    assert sorted(result[0] for result in results) == ["insufficient", "success"]
    with app.app_context():
        balance = db.session.get(FeedInventoryBalance, balance_id)
        transaction_count = db.session.scalar(
            select(func.count()).select_from(FeedTransaction)
        )
        assert balance.quantity_kg == Decimal("0.250")
        assert transaction_count == 1
        db.session.remove()

    downgrade_result = _migration("downgrade", "base")
    _assert_migration_succeeded(downgrade_result, "downgrade")


@pytest.mark.parametrize(
    (
        "url",
        "expected_name",
        "expected_uuid",
        "attestation",
        "observed_name",
        "observed_uuid",
        "tables",
    ),
    [
        (
            "mysql+pymysql://tester:synthetic@localhost/livestock_test_ci",
            "livestock_test_ci",
            "test-server-uuid",
            _REQUIRED_DISPOSABLE_ATTESTATION,
            "livestock_test_ci",
            "test-server-uuid",
            set(),
        ),
    ],
)
def test_mysql_target_guard_accepts_only_explicit_verified_empty_target(
    url,
    expected_name,
    expected_uuid,
    attestation,
    observed_name,
    observed_uuid,
    tables,
):
    _validate_mysql_test_target(
        url,
        expected_database_name=expected_name,
        expected_server_uuid=expected_uuid,
        attestation=attestation,
        actual_database_name=observed_name,
        actual_server_uuid=observed_uuid,
        existing_tables=tables,
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"expected_database_name": "livestock_test_other"}, "does not match"),
        ({"expected_server_uuid": ""}, "must identify"),
        ({"attestation": ""}, "attestation is missing"),
        ({"actual_database_name": "livestock_shared"}, "does not match"),
        ({"actual_server_uuid": "different-server"}, "UUID does not match"),
        ({"existing_tables": {"farms"}}, "must be empty"),
    ],
)
def test_mysql_target_guard_rejects_unverified_targets(overrides, message):
    arguments = {
        "expected_database_name": "livestock_test_ci",
        "expected_server_uuid": "test-server-uuid",
        "attestation": _REQUIRED_DISPOSABLE_ATTESTATION,
        "actual_database_name": "livestock_test_ci",
        "actual_server_uuid": "test-server-uuid",
        "existing_tables": set(),
    }
    arguments.update(overrides)
    with pytest.raises(ValueError, match=message):
        _validate_mysql_test_target(
            "mysql+pymysql://tester:synthetic@localhost/livestock_test_ci",
            **arguments,
        )
