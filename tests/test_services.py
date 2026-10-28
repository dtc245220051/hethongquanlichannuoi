from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from livestock_app import db
from livestock_app.models import (
    Animal,
    AuditLog,
    Barn,
    Farm,
    FarmMembership,
    FarmPermissionGrant,
    FeedInventoryBalance,
    FeedItem,
    FeedTransaction,
    Herd,
    HerdEvent,
    Species,
    User,
)
from livestock_app.services import (
    AuthorizationError,
    InsufficientStockError,
    NotFoundError,
    ValidationError,
)
from livestock_app.services.feed import FeedInventoryService
from livestock_app.services.livestock import LivestockService
from livestock_app.services.workflows import HealthRecordService


@pytest.fixture
def service_data(app):
    with app.app_context():
        db.create_all()
        farm = Farm(name="Synthetic Farm")
        other_farm = Farm(name="Other Synthetic Farm")
        owner = User(
            email="owner@example.test",
            password_hash="test-hash",
            display_name="Owner",
        )
        staff = User(
            email="staff@example.test",
            password_hash="test-hash",
            display_name="Staff",
        )
        outsider = User(
            email="outsider@example.test",
            password_hash="test-hash",
            display_name="Outsider",
        )
        db.session.add_all([farm, other_farm, owner, staff, outsider])
        db.session.flush()
        owner_membership = FarmMembership(
            farm_id=farm.id, user_id=owner.id, role="owner"
        )
        staff_membership = FarmMembership(
            farm_id=farm.id, user_id=staff.id, role="care_staff"
        )
        outsider_membership = FarmMembership(
            farm_id=other_farm.id, user_id=outsider.id, role="owner"
        )
        barn = Barn(farm_id=farm.id, name="Barn", capacity_heads=30)
        small_barn = Barn(farm_id=farm.id, name="Small barn", capacity_heads=5)
        feed = FeedItem(farm_id=farm.id, name="Feed", base_unit_code="kg")
        other_feed = FeedItem(
            farm_id=other_farm.id, name="Other feed", base_unit_code="kg"
        )
        db.session.add_all(
            [
                owner_membership,
                staff_membership,
                outsider_membership,
                barn,
                small_barn,
                feed,
                other_feed,
                Species(code="cattle", name_vi="Bò"),
                Species(code="poultry", name_vi="Gia cầm"),
            ]
        )
        db.session.flush()
        result = {
            "farm_id": farm.id,
            "other_farm_id": other_farm.id,
            "owner_id": owner.id,
            "staff_id": staff.id,
            "outsider_id": outsider.id,
            "barn_id": barn.id,
            "small_barn_id": small_barn.id,
            "feed_id": feed.id,
            "other_feed_id": other_feed.id,
        }
        db.session.commit()
        yield result
        db.session.remove()
        db.drop_all()


def _livestock():
    return LivestockService(db.session)


def _feed():
    return FeedInventoryService(db.session)


def test_veterinarian_cannot_write_through_services(service_data, app):
    with app.app_context():
        membership = db.session.scalar(
            select(FarmMembership).where(
                FarmMembership.farm_id == service_data["farm_id"],
                FarmMembership.user_id == service_data["staff_id"],
            )
        )
        membership.role = "veterinarian"
        db.session.commit()

        with pytest.raises(AuthorizationError, match="read-only"):
            _livestock().create_herd(
                farm_id=service_data["farm_id"],
                actor_user_id=service_data["staff_id"],
                name="Unauthorized herd",
                species_code="cattle",
                heads=1,
                reason="Test authorization",
                event_type="acquisition",
            )

        with pytest.raises(AuthorizationError, match="read-only"):
            HealthRecordService(db.session).record(
                farm_id=service_data["farm_id"],
                actor_user_id=service_data["staff_id"],
                observations="Test observation",
                animal_id="nonexistent-animal",
            )

        assert db.session.scalar(
            select(func.count(Herd.id)).where(Herd.farm_id == service_data["farm_id"])
        ) == 0


def test_herd_split_identification_and_count_adjustments_are_atomic(service_data):
    data = service_data
    service = _livestock()
    herd_id = service.create_herd(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        name="Synthetic flock",
        species_code="poultry",
        heads=8,
        event_type="acquisition",
        reason="Initial synthetic inventory",
        barn_id=data["barn_id"],
    )
    service.identify_unidentified_animal(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        herd_id=herd_id,
        identifier="SYN-001",
        reason="Tagging one bird",
    )
    target_id = service.split_herd(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        source_herd_id=herd_id,
        target_name="Split flock",
        heads=3,
        reason="Synthetic split",
    )

    assert service.farm_head_count(
        farm_id=data["farm_id"], actor_user_id=data["owner_id"]
    ) == 8
    assert db.session.get(Herd, herd_id).head_count == 5
    assert db.session.get(Herd, target_id).head_count == 3
    with pytest.raises(ValidationError):
        service.adjust_unidentified_count(
            farm_id=data["farm_id"],
            actor_user_id=data["staff_id"],
            herd_id=target_id,
            delta=-4,
            event_type="loss",
            reason="Invalid negative count",
        )
    db.session.remove()
    assert db.session.get(Herd, target_id).head_count == 3


def test_merge_moves_identified_animals_and_closes_source_without_double_count(
    service_data,
):
    data = service_data
    service = _livestock()
    source_id = service.create_herd(
        farm_id=data["farm_id"],
        actor_user_id=data["owner_id"],
        name="Source",
        species_code="cattle",
        heads=4,
        event_type="acquisition",
        reason="Synthetic source",
    )
    target_id = service.create_herd(
        farm_id=data["farm_id"],
        actor_user_id=data["owner_id"],
        name="Target",
        species_code="cattle",
        heads=2,
        event_type="birth",
        reason="Synthetic target",
    )
    animal_id = service.create_animal(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        species_code="cattle",
        herd_id=source_id,
        identifier="CATTLE-01",
        event_type="birth",
        reason="Synthetic birth",
    )

    moved_heads = service.merge_herds(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        source_herd_id=source_id,
        target_herd_id=target_id,
        reason="Synthetic merge",
    )

    assert moved_heads == 5
    assert db.session.get(Herd, source_id).head_count == 0
    assert db.session.get(Herd, source_id).status == "closed"
    assert db.session.get(Herd, target_id).head_count == 7
    assert db.session.get(Animal, animal_id).herd_id == target_id
    assert service.farm_head_count(
        farm_id=data["farm_id"], actor_user_id=data["owner_id"]
    ) == 7


def test_animal_status_changes_are_evented_and_farm_scoped(service_data):
    data = service_data
    service = _livestock()
    animal_id = service.create_animal(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        species_code="cattle",
        barn_id=data["barn_id"],
        identifier="CATTLE-02",
        event_type="acquisition",
        reason="Synthetic purchase",
    )
    service.change_animal_status(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        animal_id=animal_id,
        status="sold",
        reason="Synthetic sale",
    )
    assert service.farm_head_count(
        farm_id=data["farm_id"], actor_user_id=data["owner_id"]
    ) == 0
    assert db.session.scalar(
        select(HerdEvent).where(
            HerdEvent.animal_id == animal_id,
            HerdEvent.event_type == "sale",
        )
    ).event_type == "sale"

    with pytest.raises(AuthorizationError):
        service.change_animal_status(
            farm_id=data["farm_id"],
            actor_user_id=data["outsider_id"],
            animal_id=animal_id,
            status="deceased",
            reason="Cross-farm attempt",
        )
    foreign_animal_id = service.create_animal(
        farm_id=data["other_farm_id"],
        actor_user_id=data["outsider_id"],
        species_code="cattle",
        identifier="FOREIGN-ANIMAL",
        event_type="acquisition",
        reason="Synthetic foreign animal",
    )
    with pytest.raises(NotFoundError):
        service.change_animal_status(
            farm_id=data["farm_id"],
            actor_user_id=data["owner_id"],
            animal_id=foreign_animal_id,
            status="deceased",
            reason="Must not cross farm",
        )


def test_moving_identified_animal_out_of_herd_preserves_total_count(service_data):
    data = service_data
    service = _livestock()
    herd_id = service.create_herd(
        farm_id=data["farm_id"],
        actor_user_id=data["owner_id"],
        name="Tagged flock",
        species_code="poultry",
        heads=3,
        event_type="acquisition",
        reason="Synthetic flock",
        barn_id=data["barn_id"],
    )
    animal_id = service.identify_unidentified_animal(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        herd_id=herd_id,
        identifier="BIRD-1",
        reason="Synthetic identification",
    )
    service.move_animal(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        animal_id=animal_id,
        target_barn_id=data["small_barn_id"],
        reason="Synthetic individual move",
    )

    assert db.session.get(Animal, animal_id).herd_id is None
    assert db.session.get(Animal, animal_id).barn_id == data["small_barn_id"]
    assert db.session.get(Herd, herd_id).head_count == 2
    assert service.farm_head_count(
        farm_id=data["farm_id"], actor_user_id=data["owner_id"]
    ) == 3


def test_livestock_failure_rolls_back_animal_event_and_audit(service_data):
    data = service_data
    service = _livestock()
    service.create_animal(
        farm_id=data["farm_id"],
        actor_user_id=data["owner_id"],
        species_code="cattle",
        identifier="DUPLICATE",
        event_type="acquisition",
        reason="First synthetic animal",
    )
    before_events = db.session.scalar(select(func.count()).select_from(HerdEvent))
    before_audits = db.session.scalar(select(func.count()).select_from(AuditLog))

    with pytest.raises(IntegrityError):
        service.create_animal(
            farm_id=data["farm_id"],
            actor_user_id=data["owner_id"],
            species_code="cattle",
            identifier="DUPLICATE",
            event_type="birth",
            reason="Duplicate tag should roll back",
        )

    db.session.remove()
    assert db.session.scalar(select(func.count()).select_from(Animal)) == 1
    assert db.session.scalar(select(func.count()).select_from(HerdEvent)) == before_events
    assert db.session.scalar(select(func.count()).select_from(AuditLog)) == before_audits


def test_capacity_is_enforced_and_failure_rolls_back(service_data):
    data = service_data
    service = _livestock()
    assert db.session.get(Barn, data["small_barn_id"]).capacity_heads == 5
    service.create_herd(
        farm_id=data["farm_id"],
        actor_user_id=data["owner_id"],
        name="At capacity",
        species_code="poultry",
        heads=5,
        event_type="acquisition",
        reason="Synthetic capacity test",
        barn_id=data["small_barn_id"],
    )
    before_herds = db.session.scalar(select(func.count()).select_from(Herd))
    with pytest.raises(ValidationError):
        service.create_herd(
            farm_id=data["farm_id"],
            actor_user_id=data["owner_id"],
            name="Over capacity",
            species_code="poultry",
            heads=1,
            event_type="birth",
            reason="Must reject over capacity",
            barn_id=data["small_barn_id"],
        )
    db.session.remove()
    assert db.session.scalar(select(func.count()).select_from(Herd)) == before_herds


def test_feed_permissions_kg_only_and_negative_stock_rollback(service_data):
    data = service_data
    service = _feed()
    with pytest.raises(AuthorizationError):
        service.record_transaction(
            farm_id=data["farm_id"],
            actor_user_id=data["staff_id"],
            feed_item_id=data["feed_id"],
            transaction_type="receipt",
            quantity_kg=Decimal("3.000"),
        )
    with pytest.raises(InsufficientStockError):
        service.record_transaction(
            farm_id=data["farm_id"],
            actor_user_id=data["staff_id"],
            feed_item_id=data["feed_id"],
            transaction_type="consumption",
            quantity_kg=Decimal("0.001"),
        )
    assert db.session.scalar(
        select(FeedInventoryBalance).where(
            FeedInventoryBalance.farm_id == data["farm_id"],
            FeedInventoryBalance.feed_item_id == data["feed_id"],
        )
    ) is None
    db.session.commit()
    with pytest.raises(ValidationError):
        service.record_transaction(
            farm_id=data["farm_id"],
            actor_user_id=data["owner_id"],
            feed_item_id=data["feed_id"],
            transaction_type="receipt",
            quantity_kg=Decimal("3.000"),
            unit_code="bag",
        )
    with pytest.raises(AuthorizationError):
        service.record_transaction(
            farm_id=data["farm_id"],
            actor_user_id=data["staff_id"],
            feed_item_id=data["feed_id"],
            transaction_type="adjustment",
            quantity_kg=Decimal("1.000"),
            reason="No adjustment permission",
        )
    with pytest.raises(NotFoundError):
        service.record_transaction(
            farm_id=data["farm_id"],
            actor_user_id=data["owner_id"],
            feed_item_id=data["other_feed_id"],
            transaction_type="receipt",
            quantity_kg=Decimal("1.000"),
        )

    receipt = service.record_transaction(
        farm_id=data["farm_id"],
        actor_user_id=data["owner_id"],
        feed_item_id=data["feed_id"],
        transaction_type="receipt",
        quantity_kg=Decimal("2.500"),
    )
    assert receipt.balance_after_kg == Decimal("2.500")
    consumption = service.record_transaction(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        feed_item_id=data["feed_id"],
        transaction_type="consumption",
        quantity_kg=Decimal("1.250"),
    )
    assert consumption.balance_after_kg == Decimal("1.250")
    before_transactions = db.session.scalar(
        select(func.count()).select_from(FeedTransaction)
    )

    with pytest.raises(InsufficientStockError):
        service.record_transaction(
            farm_id=data["farm_id"],
            actor_user_id=data["staff_id"],
            feed_item_id=data["feed_id"],
            transaction_type="consumption",
            quantity_kg=Decimal("1.251"),
        )

    db.session.remove()
    balance = db.session.scalar(
        select(FeedInventoryBalance).where(
            FeedInventoryBalance.farm_id == data["farm_id"],
            FeedInventoryBalance.feed_item_id == data["feed_id"],
        )
    )
    assert balance.quantity_kg == Decimal("1.250")
    assert db.session.scalar(
        select(func.count()).select_from(FeedTransaction)
    ) == before_transactions


def test_feed_delegate_permission_is_farm_scoped(service_data):
    data = service_data
    grant = FarmPermissionGrant(
        farm_id=data["farm_id"],
        user_id=data["staff_id"],
        permission_code="feed_stock_in",
        granted_by_user_id=data["owner_id"],
        reason="Explicit synthetic grant",
        granted_at=datetime.now(timezone.utc),
    )
    db.session.add(grant)
    db.session.commit()

    result = _feed().record_transaction(
        farm_id=data["farm_id"],
        actor_user_id=data["staff_id"],
        feed_item_id=data["feed_id"],
        transaction_type="receipt",
        quantity_kg="1.000",
    )
    assert result.balance_after_kg == Decimal("1.000")
