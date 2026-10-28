from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from livestock_app import db
from livestock_app.models import (
    Animal,
    Barn,
    BarnCapacityExceptionRequest,
    Farm,
    FarmMembership,
    FeedInventoryBalance,
    FeedItem,
    FeedTransaction,
    Herd,
    Species,
    User,
    VaccinationOutcome,
    VaccinationSchedule,
)


@pytest.fixture
def schema(app):
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def _farm_with_member(name: str, email: str) -> tuple[Farm, User, FarmMembership]:
    farm = Farm(name=name)
    user = User(email=email, password_hash="test-hash", display_name=name)
    db.session.add_all([farm, user])
    db.session.flush()
    membership = FarmMembership(farm_id=farm.id, user_id=user.id, role="owner")
    db.session.add(membership)
    db.session.flush()
    return farm, user, membership


def test_all_core_tables_are_created_with_uuid_primary_keys(schema):
    tables = set(inspect(db.engine).get_table_names())

    assert {
        "farms",
        "users",
        "user_farm_roles",
        "farm_permission_grants",
        "platform_authorizations",
        "support_access_requests",
        "support_access_grants",
        "barns",
        "herds",
        "animals",
        "feed_items",
        "feed_inventory_balances",
        "feed_transactions",
        "herd_events",
        "health_records",
        "barn_capacity_exception_requests",
        "vaccination_schedules",
        "vaccination_schedule_events",
        "vaccination_outcomes",
        "alerts",
        "alert_events",
        "audit_logs",
        "data_retention_policies",
    }.issubset(tables)

    inspector = inspect(db.engine)
    for table_name in tables:
        primary_key_columns = inspector.get_pk_constraint(table_name)[
            "constrained_columns"
        ]
        assert primary_key_columns == ["id"]
        primary_key = next(
            column
            for column in inspector.get_columns(table_name)
            if column["name"] == "id"
        )
        assert primary_key["type"].length == 36


def test_sqlite_enforces_foreign_keys_and_farm_scoped_barn_references(schema):
    farm_a, owner_a, _ = _farm_with_member("Farm A", "a@example.test")
    farm_b, _, _ = _farm_with_member("Farm B", "b@example.test")
    db.session.add(Species(code="cattle", name_vi="Bò"))
    db.session.flush()
    barn_b = Barn(farm_id=farm_b.id, name="Barn B", capacity_heads=10)
    db.session.add(barn_b)
    db.session.flush()
    db.session.add(
        Herd(
            farm_id=farm_a.id,
            name="Herd A",
            species_code="cattle",
            barn_id=barn_b.id,
            unidentified_count=1,
        )
    )

    with pytest.raises(IntegrityError):
        db.session.flush()

    db.session.rollback()
    assert owner_a.id


def test_feed_item_rejects_units_other_than_kg(schema):
    farm, _, _ = _farm_with_member("Farm", "owner@example.test")
    item = FeedItem(
        farm_id=farm.id,
        name="Feed",
        base_unit_code="bag",
    )
    db.session.add(item)

    with pytest.raises(IntegrityError):
        db.session.flush()


def test_feed_balance_must_be_nonnegative(schema):
    farm, _, _ = _farm_with_member("Farm", "owner@example.test")
    item = FeedItem(farm_id=farm.id, name="Feed", base_unit_code="kg")
    db.session.add(item)
    db.session.flush()
    db.session.add(
        FeedInventoryBalance(
            farm_id=farm.id,
            feed_item_id=item.id,
            quantity_kg=Decimal("-0.001"),
            updated_at=datetime.now(timezone.utc),
        )
    )

    with pytest.raises(IntegrityError):
        db.session.flush()


def test_feed_transaction_requires_nonnegative_balances_and_consistent_delta(schema):
    farm, _, membership = _farm_with_member("Farm", "owner@example.test")
    item = FeedItem(farm_id=farm.id, name="Feed", base_unit_code="kg")
    db.session.add(item)
    db.session.flush()
    db.session.add(
        FeedTransaction(
            farm_id=farm.id,
            feed_item_id=item.id,
            transaction_type="consumption",
            quantity_delta_kg=Decimal("-1.000"),
            unit_code="kg",
            balance_before_kg=Decimal("0.500"),
            balance_after_kg=Decimal("-0.500"),
            occurred_at=datetime.now(timezone.utc),
            recorded_by_user_id=membership.user_id,
        )
    )

    with pytest.raises(IntegrityError):
        db.session.flush()


def test_vaccination_schedule_is_not_a_vaccination_outcome(schema):
    farm, _, membership = _farm_with_member("Farm", "owner@example.test")
    db.session.add(Species(code="poultry", name_vi="Gia cầm"))
    db.session.flush()
    herd = Herd(
        farm_id=farm.id,
        name="Flock",
        species_code="poultry",
        unidentified_count=12,
    )
    db.session.add(herd)
    db.session.flush()
    schedule = VaccinationSchedule(
        farm_id=farm.id,
        herd_id=herd.id,
        vaccine_name="Vaccine A",
        due_at=datetime.now(timezone.utc),
        created_by_user_id=membership.user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(schedule)
    db.session.flush()

    assert schedule.status == "scheduled"
    assert not hasattr(schedule, "outcome")

    invalid_outcome = VaccinationOutcome(
        farm_id=farm.id,
        schedule_id=schedule.id,
        outcome="administered",
        recorded_by_user_id=membership.user_id,
    )
    db.session.add(invalid_outcome)
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_vaccination_schedule_requires_exactly_one_target(schema):
    farm, _, membership = _farm_with_member("Farm", "owner@example.test")
    schedule = VaccinationSchedule(
        farm_id=farm.id,
        vaccine_name="Vaccine A",
        due_at=datetime.now(timezone.utc),
        created_by_user_id=membership.user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(schedule)

    with pytest.raises(IntegrityError):
        db.session.flush()


def test_foreign_keys_reject_cross_farm_feed_and_vaccination_records(schema):
    farm_a, _, membership_a = _farm_with_member("Farm A", "a@example.test")
    farm_b, _, _ = _farm_with_member("Farm B", "b@example.test")
    db.session.add(Species(code="cattle", name_vi="Bò"))
    db.session.flush()
    item_b = FeedItem(farm_id=farm_b.id, name="Feed B", base_unit_code="kg")
    herd_b = Herd(farm_id=farm_b.id, name="Herd B", species_code="cattle")
    db.session.add_all([item_b, herd_b])
    db.session.flush()

    db.session.add(
        FeedTransaction(
            farm_id=farm_a.id,
            feed_item_id=item_b.id,
            transaction_type="receipt",
            quantity_delta_kg=Decimal("1.000"),
            unit_code="kg",
            balance_before_kg=Decimal("0.000"),
            balance_after_kg=Decimal("1.000"),
            occurred_at=datetime.now(timezone.utc),
            recorded_by_user_id=membership_a.user_id,
        )
    )

    with pytest.raises(IntegrityError):
        db.session.flush()

    db.session.rollback()
    assert farm_b.id


def test_vaccination_schedule_rejects_target_from_another_farm(schema):
    farm_a, _, membership_a = _farm_with_member("Farm A", "a@example.test")
    farm_b, _, _ = _farm_with_member("Farm B", "b@example.test")
    db.session.add(Species(code="cattle", name_vi="Bò"))
    db.session.flush()
    herd_b = Herd(farm_id=farm_b.id, name="Herd B", species_code="cattle")
    db.session.add(herd_b)
    db.session.flush()

    db.session.add(
        VaccinationSchedule(
            farm_id=farm_a.id,
            herd_id=herd_b.id,
            vaccine_name="Vaccine",
            due_at=datetime.now(timezone.utc),
            created_by_user_id=membership_a.user_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_capacity_exception_rejects_cross_farm_barn(schema):
    farm_a, _, membership_a = _farm_with_member("Farm A", "a@example.test")
    farm_b, _, _ = _farm_with_member("Farm B", "b@example.test")
    barn_b = Barn(farm_id=farm_b.id, name="Barn B", capacity_heads=10)
    db.session.add(barn_b)
    db.session.flush()

    db.session.add(
        BarnCapacityExceptionRequest(
            farm_id=farm_a.id,
            barn_id=barn_b.id,
            current_occupancy_heads=10,
            capacity_heads_snapshot=10,
            proposed_additional_heads=1,
            reason="Synthetic test",
            requested_by_user_id=membership_a.user_id,
            requested_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_capacity_exception_rejects_nonpositive_capacity(schema):
    farm, _, _ = _farm_with_member("Farm", "owner@example.test")
    db.session.add(Barn(farm_id=farm.id, name="Invalid barn", capacity_heads=0))

    with pytest.raises(IntegrityError):
        db.session.flush()


def test_feed_ledger_uses_decimal_quantities(schema):
    farm, _, membership = _farm_with_member("Farm", "owner@example.test")
    item = FeedItem(farm_id=farm.id, name="Feed", base_unit_code="kg")
    db.session.add(item)
    db.session.flush()
    tx = FeedTransaction(
        farm_id=farm.id,
        feed_item_id=item.id,
        transaction_type="receipt",
        quantity_delta_kg=Decimal("1.125"),
        unit_code="kg",
        balance_before_kg=Decimal("0.000"),
        balance_after_kg=Decimal("1.125"),
        occurred_at=datetime.now(timezone.utc),
        recorded_by_user_id=membership.user_id,
    )
    db.session.add(tx)
    db.session.flush()

    assert tx.quantity_delta_kg == Decimal("1.125")


def test_purge_is_disabled_by_default_and_cannot_be_enabled_in_schema(schema):
    from livestock_app.models import RetentionPolicy

    policy = RetentionPolicy(
        data_category="audit",
        retention_days=365,
        purge_execution_enabled=False,
        updated_at=datetime.now(timezone.utc),
    )
    db.session.add(policy)
    db.session.flush()
    assert policy.purge_execution_enabled is False

    policy.purge_execution_enabled = True
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_herd_count_includes_unidentified_and_active_individual_animals(schema):
    farm, _, _ = _farm_with_member("Farm", "owner@example.test")
    db.session.add(Species(code="poultry", name_vi="Gia cầm"))
    db.session.flush()
    herd = Herd(
        farm_id=farm.id,
        name="Flock",
        species_code="poultry",
        unidentified_count=5,
    )
    db.session.add(herd)
    db.session.flush()
    db.session.add(
        Animal(
            farm_id=farm.id,
            herd_id=herd.id,
            species_code="poultry",
            identifier="tag-1",
        )
    )
    db.session.flush()

    assert herd.head_count == 6
