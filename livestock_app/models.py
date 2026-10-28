from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import validates

from livestock_app.extensions import db


def new_id() -> str:
    return str(uuid4())


class UUIDMixin:
    id = db.Column(db.String(36), primary_key=True, default=new_id)


class FarmScopedMixin(UUIDMixin):
    farm_id = db.Column(
        db.String(36), ForeignKey("farms.id", ondelete="RESTRICT"), nullable=False
    )
    synthetic_dataset_id = db.Column(db.String(36), nullable=True)


class Farm(UUIDMixin, db.Model):
    __tablename__ = "farms"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive')", name="valid_status"
        ),
    )

    name = db.Column(db.String(160), nullable=False)
    timezone = db.Column(db.String(64), nullable=False, default="Asia/Ho_Chi_Minh")
    status = db.Column(db.String(16), nullable=False, default="active")


class User(UUIDMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled')", name="valid_status"
        ),
    )

    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="active")


class FarmMembership(UUIDMixin, db.Model):
    __tablename__ = "user_farm_roles"
    __table_args__ = (
        UniqueConstraint("farm_id", "user_id", name="farm_user"),
        UniqueConstraint("farm_id", "user_id", "id", name="farm_user_id"),
        CheckConstraint(
            "role IN ('owner', 'care_staff', 'manager', 'veterinarian')",
            name="valid_role",
        ),
        ForeignKeyConstraint(
            ["farm_id", "created_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="membership_created_by_same_farm",
            ondelete="RESTRICT",
        ),
    )

    farm_id = db.Column(
        db.String(36), ForeignKey("farms.id", ondelete="RESTRICT"), nullable=False
    )
    user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role = db.Column(db.String(16), nullable=False)
    created_by_user_id = db.Column(db.String(36), nullable=True)


class FarmPermissionGrant(FarmScopedMixin, db.Model):
    __tablename__ = "farm_permission_grants"
    __table_args__ = (
        UniqueConstraint(
            "farm_id", "id", "user_id", name="farm_permission_grant_subject"
        ),
        ForeignKeyConstraint(
            ["farm_id", "user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="permission_subject_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "granted_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="permission_grantor_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(reason) > 0", name="reason_required"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="valid_revocation_time",
        ),
        ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="permission_synthetic_dataset",
            ondelete="RESTRICT",
        ),
    )

    user_id = db.Column(db.String(36), nullable=False)
    permission_code = db.Column(db.String(64), nullable=False)
    granted_by_user_id = db.Column(db.String(36), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    granted_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)


class PlatformAuthorization(UUIDMixin, db.Model):
    __tablename__ = "platform_authorizations"
    __table_args__ = (
        CheckConstraint(
            "permission_code IN ('support_grant_approve', 'support_grant_revoke')",
            name="valid_permission",
        ),
        CheckConstraint("user_id <> granted_by_user_id", name="no_self_assignment"),
    )

    user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    permission_code = db.Column(db.String(64), nullable=False)
    granted_by_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    granted_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)


class SupportAccessRequest(FarmScopedMixin, db.Model):
    __tablename__ = "support_access_requests"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_support_request"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'cancelled', 'expired')",
            name="valid_status",
        ),
        CheckConstraint(
            "admin_user_id <> COALESCE(decided_by_user_id, admin_user_id)",
            name="requester_cannot_approve",
        ),
        CheckConstraint("length(reason) > 0", name="reason_required"),
        CheckConstraint(
            "expires_at > starts_at", name="valid_grant_window"
        ),
        ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="support_request_synthetic_dataset",
            ondelete="RESTRICT",
        ),
    )

    admin_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_scope = db.Column(db.String(500), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending")
    requested_at = db.Column(db.DateTime(timezone=True), nullable=False)
    decided_by_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decision_reason = db.Column(db.String(500), nullable=True)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)


class SupportAccessGrant(FarmScopedMixin, db.Model):
    __tablename__ = "support_access_grants"
    __table_args__ = (
        UniqueConstraint("farm_id", "request_id", name="one_grant_per_request"),
        CheckConstraint(
            "admin_user_id <> granted_by_user_id", name="issuer_is_not_recipient"
        ),
        CheckConstraint("length(reason) > 0", name="reason_required"),
        CheckConstraint("expires_at > starts_at", name="valid_grant_window"),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')", name="valid_status"
        ),
        ForeignKeyConstraint(
            ["farm_id", "request_id"],
            ["support_access_requests.farm_id", "support_access_requests.id"],
            name="support_grant_request_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="support_grant_synthetic_dataset",
            ondelete="RESTRICT",
        ),
    )

    request_id = db.Column(db.String(36), nullable=False)
    admin_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    scope = db.Column(db.String(500), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    granted_by_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="active")
    revoked_by_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)


class SyntheticDataset(UUIDMixin, db.Model):
    __tablename__ = "synthetic_datasets"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_synthetic_dataset"),
    )

    farm_id = db.Column(
        db.String(36), ForeignKey("farms.id", ondelete="RESTRICT"), nullable=False
    )
    marker = db.Column(db.String(64), nullable=False, unique=True)
    seed_version = db.Column(db.String(64), nullable=False)
    created_by_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)


def _farm_scoped_synthetic_fk(table_name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["farm_id", "synthetic_dataset_id"],
        ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
        name=f"{table_name}_synthetic_dataset",
        ondelete="RESTRICT",
    )


class Barn(FarmScopedMixin, db.Model):
    __tablename__ = "barns"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_barn"),
        CheckConstraint("capacity_heads > 0", name="positive_capacity"),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="valid_status"
        ),
        _farm_scoped_synthetic_fk("barn"),
    )

    name = db.Column(db.String(160), nullable=False)
    location_note = db.Column(db.String(500), nullable=True)
    capacity_heads = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="active")


class BarnCapacityExceptionRequest(FarmScopedMixin, db.Model):
    __tablename__ = "barn_capacity_exception_requests"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_capacity_request"),
        CheckConstraint(
            "current_occupancy_heads >= 0", name="nonnegative_occupancy"
        ),
        CheckConstraint("capacity_heads_snapshot > 0", name="positive_capacity"),
        CheckConstraint(
            "proposed_additional_heads > 0", name="positive_proposal"
        ),
        CheckConstraint("length(reason) > 0", name="reason_required"),
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
            name="valid_decision",
        ),
        CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id <> requested_by_user_id",
            name="requester_cannot_approve",
        ),
        ForeignKeyConstraint(
            ["farm_id", "barn_id"],
            ["barns.farm_id", "barns.id"],
            name="capacity_request_barn_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "requested_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="capacity_requester_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "decided_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="capacity_decider_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("capacity_request"),
    )

    barn_id = db.Column(db.String(36), nullable=False)
    current_occupancy_heads = db.Column(db.Integer, nullable=False)
    capacity_heads_snapshot = db.Column(db.Integer, nullable=False)
    proposed_additional_heads = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    requested_by_user_id = db.Column(db.String(36), nullable=False)
    requested_at = db.Column(db.DateTime(timezone=True), nullable=False)
    decision = db.Column(db.String(16), nullable=False, default="pending")
    decided_by_user_id = db.Column(db.String(36), nullable=True)
    decision_reason = db.Column(db.String(500), nullable=True)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    executed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class Herd(FarmScopedMixin, db.Model):
    __tablename__ = "herds"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_herd"),
        UniqueConstraint("farm_id", "id", "species_code", name="farm_herd_species"),
        CheckConstraint("unidentified_count >= 0", name="nonnegative_count"),
        CheckConstraint(
            "status IN ('active', 'inactive', 'closed')", name="valid_status"
        ),
        ForeignKeyConstraint(
            ["farm_id", "barn_id"],
            ["barns.farm_id", "barns.id"],
            name="herd_barn_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="herd_synthetic_dataset",
            ondelete="RESTRICT",
        ),
    )

    name = db.Column(db.String(160), nullable=False)
    species_code = db.Column(
        db.String(32), ForeignKey("species.code", ondelete="RESTRICT"), nullable=False
    )
    barn_id = db.Column(db.String(36), nullable=True)
    unidentified_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(16), nullable=False, default="active")

    @property
    def head_count(self) -> int:
        if self.status != "active":
            return 0

        identified_count = Animal.query.filter_by(
            farm_id=self.farm_id, herd_id=self.id, status="active"
        ).count()
        return self.unidentified_count + identified_count


class Animal(FarmScopedMixin, db.Model):
    __tablename__ = "animals"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_animal"),
        UniqueConstraint("farm_id", "identifier", name="farm_animal_identifier"),
        CheckConstraint(
            "status IN ('active', 'inactive', 'deceased', 'sold')",
            name="valid_status",
        ),
        ForeignKeyConstraint(
            ["farm_id", "herd_id", "species_code"],
            ["herds.farm_id", "herds.id", "herds.species_code"],
            name="animal_herd_same_farm_species",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "barn_id"],
            ["barns.farm_id", "barns.id"],
            name="animal_barn_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("animal"),
    )

    identifier = db.Column(db.String(100), nullable=True)
    display_name = db.Column(db.String(160), nullable=True)
    species_code = db.Column(
        db.String(32), ForeignKey("species.code", ondelete="RESTRICT"), nullable=False
    )
    herd_id = db.Column(db.String(36), nullable=True)
    barn_id = db.Column(db.String(36), nullable=True)
    status = db.Column(db.String(16), nullable=False, default="active")


class HerdEvent(FarmScopedMixin, db.Model):
    __tablename__ = "herd_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('acquisition', 'birth', 'split', 'merge', 'movement', "
            "'tracking_conversion', 'loss', 'sale', 'closure')",
            name="valid_event_type",
        ),
        CheckConstraint("head_count > 0", name="positive_head_count"),
        CheckConstraint("length(reason) > 0", name="reason_required"),
        CheckConstraint(
            "source_herd_id IS NOT NULL OR target_herd_id IS NOT NULL "
            "OR animal_id IS NOT NULL",
            name="event_has_subject",
        ),
        ForeignKeyConstraint(
            ["farm_id", "source_herd_id"],
            ["herds.farm_id", "herds.id"],
            name="event_source_herd_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "target_herd_id"],
            ["herds.farm_id", "herds.id"],
            name="event_target_herd_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="event_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "recorded_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="event_actor_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("herd_event"),
    )

    event_type = db.Column(db.String(32), nullable=False)
    source_herd_id = db.Column(db.String(36), nullable=True)
    target_herd_id = db.Column(db.String(36), nullable=True)
    animal_id = db.Column(db.String(36), nullable=True)
    head_count = db.Column(db.Integer, nullable=False)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    recorded_by_user_id = db.Column(db.String(36), nullable=False)


class Species(UUIDMixin, db.Model):
    __tablename__ = "species"

    code = db.Column(db.String(32), nullable=False, unique=True)
    name_vi = db.Column(db.String(100), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class FeedItem(FarmScopedMixin, db.Model):
    __tablename__ = "feed_items"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_feed_item"),
        CheckConstraint("base_unit_code = 'kg'", name="kg_only"),
        CheckConstraint(
            "low_stock_threshold_kg IS NULL OR low_stock_threshold_kg >= 0",
            name="nonnegative_threshold",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="valid_status"
        ),
        _farm_scoped_synthetic_fk("feed_item"),
    )

    name = db.Column(db.String(160), nullable=False)
    base_unit_code = db.Column(db.String(8), nullable=False, default="kg")
    low_stock_threshold_kg = db.Column(db.Numeric(12, 3), nullable=True)
    status = db.Column(db.String(16), nullable=False, default="active")


class FeedInventoryBalance(FarmScopedMixin, db.Model):
    __tablename__ = "feed_inventory_balances"
    __table_args__ = (
        UniqueConstraint("farm_id", "feed_item_id", name="farm_feed_balance"),
        CheckConstraint("quantity_kg >= 0", name="nonnegative_quantity"),
        ForeignKeyConstraint(
            ["farm_id", "feed_item_id"],
            ["feed_items.farm_id", "feed_items.id"],
            name="balance_feed_item_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("feed_balance"),
    )

    feed_item_id = db.Column(db.String(36), nullable=False)
    quantity_kg = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False)


class FeedTransaction(FarmScopedMixin, db.Model):
    __tablename__ = "feed_transactions"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_feed_transaction"),
        CheckConstraint(
            "transaction_type IN ('receipt', 'consumption', 'adjustment')",
            name="valid_transaction_type",
        ),
        CheckConstraint(
            "(transaction_type = 'receipt' AND quantity_delta_kg > 0) OR "
            "(transaction_type = 'consumption' AND quantity_delta_kg < 0) OR "
            "(transaction_type = 'adjustment' AND quantity_delta_kg <> 0)",
            name="transaction_delta_matches_type",
        ),
        CheckConstraint("balance_before_kg >= 0", name="nonnegative_opening_balance"),
        CheckConstraint("balance_after_kg >= 0", name="nonnegative_closing_balance"),
        CheckConstraint(
            "balance_after_kg = balance_before_kg + quantity_delta_kg",
            name="balance_matches_delta",
        ),
        CheckConstraint("unit_code = 'kg'", name="kg_only"),
        CheckConstraint(
            "transaction_type <> 'adjustment' OR length(reason) > 0",
            name="adjustment_reason_required",
        ),
        ForeignKeyConstraint(
            ["farm_id", "feed_item_id"],
            ["feed_items.farm_id", "feed_items.id"],
            name="transaction_feed_item_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="transaction_herd_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "recorded_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="transaction_actor_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("feed_transaction"),
    )

    feed_item_id = db.Column(db.String(36), nullable=False)
    transaction_type = db.Column(db.String(16), nullable=False)
    quantity_delta_kg = db.Column(db.Numeric(12, 3), nullable=False)
    unit_code = db.Column(db.String(8), nullable=False, default="kg")
    balance_before_kg = db.Column(db.Numeric(12, 3), nullable=False)
    balance_after_kg = db.Column(db.Numeric(12, 3), nullable=False)
    herd_id = db.Column(db.String(36), nullable=True)
    reason = db.Column(db.String(500), nullable=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    recorded_by_user_id = db.Column(db.String(36), nullable=False)


class VaccinationSchedule(FarmScopedMixin, db.Model):
    __tablename__ = "vaccination_schedules"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_vaccination_schedule"),
        CheckConstraint(
            "status IN ('scheduled', 'cancelled')", name="valid_status"
        ),
        CheckConstraint(
            "(animal_id IS NOT NULL AND herd_id IS NULL) OR "
            "(animal_id IS NULL AND herd_id IS NOT NULL)",
            name="exactly_one_target",
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="schedule_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="schedule_herd_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "created_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="schedule_actor_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("vaccination_schedule"),
    )

    animal_id = db.Column(db.String(36), nullable=True)
    herd_id = db.Column(db.String(36), nullable=True)
    vaccine_name = db.Column(db.String(160), nullable=False)
    due_at = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="scheduled")
    assigned_to_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_by_user_id = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)

    @validates("animal_id", "herd_id")
    def validate_single_target(self, key: str, value: str | None) -> str | None:
        other = self.herd_id if key == "animal_id" else self.animal_id
        if value is not None and other is not None:
            raise ValueError("A vaccination schedule must target an animal or a herd, not both.")
        return value

    @classmethod
    def validate_target(cls, animal_id: str | None, herd_id: str | None) -> None:
        if (animal_id is None) == (herd_id is None):
            raise ValueError("A vaccination schedule must target exactly one animal or herd.")


class HealthRecord(FarmScopedMixin, db.Model):
    __tablename__ = "health_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="health_record_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="health_record_herd_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "recorded_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="health_record_actor_same_farm",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(animal_id IS NOT NULL AND herd_id IS NULL) OR "
            "(animal_id IS NULL AND herd_id IS NOT NULL)",
            name="exactly_one_subject",
        ),
        _farm_scoped_synthetic_fk("health_record"),
    )

    animal_id = db.Column(db.String(36), nullable=True)
    herd_id = db.Column(db.String(36), nullable=True)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False)
    observations = db.Column(db.String(2000), nullable=False)
    action_taken = db.Column(db.String(1000), nullable=True)
    follow_up_at = db.Column(db.DateTime(timezone=True), nullable=True)
    recorded_by_user_id = db.Column(db.String(36), nullable=False)


class VaccinationScheduleEvent(FarmScopedMixin, db.Model):
    __tablename__ = "vaccination_schedule_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["farm_id", "schedule_id"],
            ["vaccination_schedules.farm_id", "vaccination_schedules.id"],
            name="schedule_event_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "actor_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="schedule_event_actor_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("vaccination_schedule_event"),
    )

    schedule_id = db.Column(db.String(36), nullable=False)
    event_type = db.Column(db.String(32), nullable=False)
    before_snapshot = db.Column(db.JSON, nullable=True)
    after_snapshot = db.Column(db.JSON, nullable=True)
    reason = db.Column(db.String(500), nullable=False)
    actor_user_id = db.Column(db.String(36), nullable=False)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)


class VaccinationOutcome(FarmScopedMixin, db.Model):
    __tablename__ = "vaccination_outcomes"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_vaccination_outcome"),
        CheckConstraint(
            "outcome IN ('administered', 'not_performed', 'postponed', 'needs_verification')",
            name="valid_outcome",
        ),
        CheckConstraint(
            "outcome <> 'administered' OR "
            "(occurred_at IS NOT NULL AND performed_by_user_id IS NOT NULL)",
            name="administered_requires_evidence",
        ),
        ForeignKeyConstraint(
            ["farm_id", "schedule_id"],
            ["vaccination_schedules.farm_id", "vaccination_schedules.id"],
            name="outcome_schedule_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "recorded_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="outcome_actor_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="outcome_synthetic_dataset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "supersedes_id"],
            ["vaccination_outcomes.farm_id", "vaccination_outcomes.id"],
            name="superseded_outcome_same_farm",
            ondelete="RESTRICT",
        ),
    )

    schedule_id = db.Column(db.String(36), nullable=False)
    outcome = db.Column(db.String(32), nullable=False)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=True)
    performed_by_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    recorded_by_user_id = db.Column(db.String(36), nullable=False)
    notes = db.Column(db.String(1000), nullable=True)
    supersedes_id = db.Column(db.String(36), nullable=True)


class Alert(FarmScopedMixin, db.Model):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_alert"),
        CheckConstraint(
            "status IN ('new', 'acknowledged', 'in_progress', 'closed')",
            name="valid_status",
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="alert_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="alert_herd_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("alert"),
    )

    alert_type = db.Column(db.String(64), nullable=False)
    severity = db.Column(db.String(16), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(2000), nullable=False)
    animal_id = db.Column(db.String(36), nullable=True)
    herd_id = db.Column(db.String(36), nullable=True)
    status = db.Column(db.String(16), nullable=False, default="new")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)


class AlertEvent(FarmScopedMixin, db.Model):
    __tablename__ = "alert_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["farm_id", "alert_id"],
            ["alerts.farm_id", "alerts.id"],
            name="alert_event_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "actor_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="alert_event_actor_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("alert_event"),
    )

    alert_id = db.Column(db.String(36), nullable=False)
    event_type = db.Column(db.String(32), nullable=False)
    actor_user_id = db.Column(db.String(36), nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    note = db.Column(db.String(1000), nullable=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)


class AuditLog(UUIDMixin, db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'denied', 'failed')", name="valid_outcome"
        ),
        ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="audit_synthetic_dataset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "actor_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="audit_farm_actor_membership",
            ondelete="RESTRICT",
        ),
    )

    farm_id = db.Column(
        db.String(36), ForeignKey("farms.id", ondelete="RESTRICT"), nullable=True
    )
    synthetic_dataset_id = db.Column(db.String(36), nullable=True)
    actor_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.String(36), nullable=True)
    outcome = db.Column(db.String(16), nullable=False)
    request_id = db.Column(db.String(100), nullable=False)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    metadata_redacted = db.Column(db.JSON, nullable=True)


class RetentionPolicy(UUIDMixin, db.Model):
    __tablename__ = "data_retention_policies"
    __table_args__ = (
        CheckConstraint("retention_days > 0", name="positive_retention"),
        CheckConstraint("purge_execution_enabled = 0", name="purge_disabled"),
    )

    data_category = db.Column(db.String(32), nullable=False, unique=True)
    retention_days = db.Column(db.Integer, nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    purge_execution_enabled = db.Column(db.Boolean, nullable=False, default=False)
    configured_by_user_id = db.Column(
        db.String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False)


class PlatformAdmin(UUIDMixin, db.Model):
    __tablename__ = "platform_admins"
    __table_args__ = (
        UniqueConstraint("user_id", name="unique_platform_admin_user"),
    )

    user_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    granted_by_user_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class DiseaseRiskAssessment(FarmScopedMixin, db.Model):
    __tablename__ = "disease_risk_assessments"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_disease_risk_assessment"),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')", name="valid_risk_level"
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="disease_risk_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="disease_risk_herd_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "performed_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="disease_risk_actor_same_farm",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(animal_id IS NOT NULL AND herd_id IS NULL) OR "
            "(animal_id IS NULL AND herd_id IS NOT NULL)",
            name="disease_risk_exactly_one_subject",
        ),
        _farm_scoped_synthetic_fk("disease_risk_assessment"),
    )

    animal_id = db.Column(db.String(36), nullable=True)
    herd_id = db.Column(db.String(36), nullable=True)
    symptoms = db.Column(db.JSON, nullable=False)
    risk_level = db.Column(db.String(16), nullable=False)
    signs_used = db.Column(db.JSON, nullable=False)
    explanation = db.Column(db.String(2000), nullable=False)
    recommendations = db.Column(db.String(2000), nullable=False)
    method = db.Column(db.String(64), nullable=False)
    emergency_warning = db.Column(db.Boolean, nullable=False, default=False)
    performed_by_user_id = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)


class WeightRecord(FarmScopedMixin, db.Model):
    __tablename__ = "weight_records"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_weight_record"),
        CheckConstraint("weight_kg > 0", name="positive_weight_kg"),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="weight_record_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "recorded_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="weight_record_actor_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("weight_record"),
    )

    animal_id = db.Column(db.String(36), nullable=False)
    weight_kg = db.Column(db.Numeric(10, 3), nullable=False)
    measured_at = db.Column(db.DateTime(timezone=True), nullable=False)
    notes = db.Column(db.String(500), nullable=True)
    recorded_by_user_id = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)


class GrowthPrediction(FarmScopedMixin, db.Model):
    __tablename__ = "growth_predictions"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_growth_prediction"),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="growth_prediction_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "performed_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="growth_prediction_actor_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("growth_prediction"),
    )

    animal_id = db.Column(db.String(36), nullable=False)
    method = db.Column(db.String(64), nullable=False)
    history_count = db.Column(db.Integer, nullable=False)
    predicted = db.Column(db.JSON, nullable=False)
    explanation = db.Column(db.String(2000), nullable=False)
    performed_by_user_id = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ai_model = db.Column(db.String(64), nullable=True)
    input_snapshot = db.Column(db.JSON, nullable=True)
    trend = db.Column(db.String(16), nullable=True)
    confidence = db.Column(db.String(16), nullable=True)
    limitations = db.Column(db.Text, nullable=True)


class FeedPlan(FarmScopedMixin, db.Model):
    __tablename__ = "feed_plans"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_feed_plan"),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="feed_plan_animal_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="feed_plan_herd_same_farm",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["farm_id", "created_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="feed_plan_actor_same_farm",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(animal_id IS NOT NULL AND herd_id IS NULL) OR "
            "(animal_id IS NULL AND herd_id IS NOT NULL)",
            name="feed_plan_exactly_one_subject",
        ),
        _farm_scoped_synthetic_fk("feed_plan"),
    )

    animal_id = db.Column(db.String(36), nullable=True)
    herd_id = db.Column(db.String(36), nullable=True)
    name = db.Column(db.String(160), nullable=False)
    period_days = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.String(1000), nullable=True)
    assumptions = db.Column(db.String(1000), nullable=True)
    created_by_user_id = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    source = db.Column(db.String(16), nullable=True)
    status = db.Column(db.String(16), nullable=True)
    ai_model = db.Column(db.String(64), nullable=True)
    input_snapshot = db.Column(db.JSON, nullable=True)
    rationale = db.Column(db.Text, nullable=True)
    limitations = db.Column(db.Text, nullable=True)
    confidence = db.Column(db.String(16), nullable=True)
    feedings_per_day = db.Column(db.Integer, nullable=True)
    schedule_note = db.Column(db.String(500), nullable=True)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    accepted_by_user_id = db.Column(db.String(36), nullable=True)


class FeedPlanItem(FarmScopedMixin, db.Model):
    __tablename__ = "feed_plan_items"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="farm_feed_plan_item"),
        ForeignKeyConstraint(
            ["farm_id", "plan_id"],
            ["feed_plans.farm_id", "feed_plans.id"],
            name="feed_plan_item_plan_same_farm",
            ondelete="RESTRICT",
        ),
        _farm_scoped_synthetic_fk("feed_plan_item"),
    )

    plan_id = db.Column(db.String(36), nullable=False)
    feed_name = db.Column(db.String(160), nullable=False)
    unit = db.Column(db.String(32), nullable=False)
    price_per_unit = db.Column(db.Numeric(12, 3), nullable=False)
    amount_per_day = db.Column(db.Numeric(12, 3), nullable=False)
    nutrients = db.Column(db.JSON, nullable=True)
    notes = db.Column(db.String(500), nullable=True)