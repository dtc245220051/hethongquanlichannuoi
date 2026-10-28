from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, scoped_session

from livestock_app.models import (
    Alert,
    AlertEvent,
    Animal,
    Barn,
    Herd,
    HealthRecord,
    VaccinationOutcome,
    VaccinationSchedule,
    VaccinationScheduleEvent,
)
from livestock_app.services import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
    atomic,
    record_audit,
    require_farm_manager,
    require_farm_operator,
    utc_now,
)


def _session(session: Session | scoped_session) -> Session:
    return session() if isinstance(session, scoped_session) else session


def _required_text(value: str, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValidationError(f"{label} must contain 1 to {limit} characters.")
    return value.strip()


def _require_manager(session: Session, farm_id: str, actor_user_id: str) -> None:
    require_farm_manager(session, farm_id, actor_user_id)


class BarnService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def occupancy(self, *, farm_id: str, barn_id: str) -> int:
        herds = self.session.scalars(
            select(Herd).where(
                Herd.farm_id == farm_id,
                Herd.barn_id == barn_id,
                Herd.status == "active",
            )
        ).all()
        herd_heads = sum(herd.head_count for herd in herds)
        standalone_animals = self.session.scalar(
            select(func.count(Animal.id)).where(
                Animal.farm_id == farm_id,
                Animal.barn_id == barn_id,
                Animal.status == "active",
                Animal.herd_id.is_(None),
            )
        )
        return herd_heads + int(standalone_animals or 0)

    def create(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        name: str,
        capacity_heads: int,
        location_note: str | None,
    ) -> str:
        name = _required_text(name, "Barn name", 160)
        if isinstance(capacity_heads, bool) or not isinstance(capacity_heads, int) or capacity_heads < 1:
            raise ValidationError("Capacity must be a positive whole number.")
        if location_note is not None and len(location_note) > 500:
            raise ValidationError("Location note is too long.")
        with atomic(self.session):
            _require_manager(self.session, farm_id, actor_user_id)
            barn = Barn(
                farm_id=farm_id,
                name=name,
                capacity_heads=capacity_heads,
                location_note=location_note.strip() if location_note else None,
                status="active",
            )
            self.session.add(barn)
            self.session.flush()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="barn.created",
                entity_type="barn",
                entity_id=barn.id,
            )
            return barn.id

    def update(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        barn_id: str,
        name: str,
        capacity_heads: int,
        location_note: str | None,
    ) -> None:
        name = _required_text(name, "Barn name", 160)
        if isinstance(capacity_heads, bool) or not isinstance(capacity_heads, int) or capacity_heads < 1:
            raise ValidationError("Capacity must be a positive whole number.")
        if location_note is not None and len(location_note) > 500:
            raise ValidationError("Location note is too long.")
        with atomic(self.session):
            _require_manager(self.session, farm_id, actor_user_id)
            barn = self.session.scalar(
                select(Barn)
                .where(Barn.farm_id == farm_id, Barn.id == barn_id)
                .with_for_update()
            )
            if barn is None:
                raise NotFoundError("Barn was not found in this farm.")
            if capacity_heads < self.occupancy(farm_id=farm_id, barn_id=barn_id):
                raise ValidationError("Capacity cannot be lower than current occupancy.")
            barn.name = name
            barn.capacity_heads = capacity_heads
            barn.location_note = location_note.strip() if location_note else None
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="barn.updated",
                entity_type="barn",
                entity_id=barn.id,
            )


class VaccinationService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def create_schedule(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        vaccine_name: str,
        due_at: datetime,
        animal_id: str | None = None,
        herd_id: str | None = None,
    ) -> str:
        vaccine_name = _required_text(vaccine_name, "Vaccine name", 160)
        if (animal_id is None) == (herd_id is None):
            raise ValidationError("Select exactly one animal or herd.")
        with atomic(self.session):
            _require_manager(self.session, farm_id, actor_user_id)
            self._validate_target(farm_id, animal_id, herd_id)
            now = utc_now()
            schedule = VaccinationSchedule(
                farm_id=farm_id,
                vaccine_name=vaccine_name,
                due_at=due_at,
                animal_id=animal_id,
                herd_id=herd_id,
                status="scheduled",
                created_by_user_id=actor_user_id,
                created_at=now,
            )
            self.session.add(schedule)
            self.session.flush()
            self._schedule_event(
                farm_id, schedule, actor_user_id, "created", None, self._snapshot(schedule)
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="vaccination.schedule.created",
                entity_type="vaccination_schedule",
                entity_id=schedule.id,
            )
            return schedule.id

    def update_schedule(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        schedule_id: str,
        vaccine_name: str,
        due_at: datetime,
        status: Literal["scheduled", "cancelled"],
    ) -> None:
        vaccine_name = _required_text(vaccine_name, "Vaccine name", 160)
        if status not in {"scheduled", "cancelled"}:
            raise ValidationError("Unsupported schedule status.")
        with atomic(self.session):
            _require_manager(self.session, farm_id, actor_user_id)
            schedule = self.session.scalar(
                select(VaccinationSchedule)
                .where(
                    VaccinationSchedule.farm_id == farm_id,
                    VaccinationSchedule.id == schedule_id,
                )
                .with_for_update()
            )
            if schedule is None:
                raise NotFoundError("Vaccination schedule was not found in this farm.")
            before = self._snapshot(schedule)
            schedule.vaccine_name = vaccine_name
            schedule.due_at = due_at
            schedule.status = status
            self._schedule_event(
                farm_id, schedule, actor_user_id, "updated", before, self._snapshot(schedule)
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="vaccination.schedule.updated",
                entity_type="vaccination_schedule",
                entity_id=schedule.id,
            )

    def record_outcome(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        schedule_id: str,
        outcome: str,
        occurred_at: datetime | None,
        notes: str | None,
    ) -> str:
        allowed = {"administered", "not_performed", "postponed", "needs_verification"}
        if outcome not in allowed:
            raise ValidationError("Unsupported vaccination outcome.")
        if outcome == "administered" and occurred_at is None:
            raise ValidationError("An administered outcome requires the actual time.")
        if notes is not None and len(notes) > 1000:
            raise ValidationError("Notes are too long.")
        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            schedule = self.session.scalar(
                select(VaccinationSchedule).where(
                    VaccinationSchedule.farm_id == farm_id,
                    VaccinationSchedule.id == schedule_id,
                )
            )
            if schedule is None or schedule.status != "scheduled":
                raise NotFoundError("Active vaccination schedule was not found in this farm.")
            if self.session.scalar(
                select(VaccinationOutcome.id).where(
                    VaccinationOutcome.farm_id == farm_id,
                    VaccinationOutcome.schedule_id == schedule_id,
                )
            ) is not None:
                raise ValidationError(
                    "This schedule already has a result; correction workflow is not enabled."
                )
            record = VaccinationOutcome(
                farm_id=farm_id,
                schedule_id=schedule_id,
                outcome=outcome,
                occurred_at=occurred_at,
                performed_by_user_id=actor_user_id if outcome == "administered" else None,
                recorded_by_user_id=actor_user_id,
                notes=notes.strip() if notes else None,
            )
            self.session.add(record)
            self.session.flush()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action=f"vaccination.outcome.{outcome}",
                entity_type="vaccination_outcome",
                entity_id=record.id,
                occurred_at=utc_now(),
            )
            return record.id

    def _validate_target(
        self, farm_id: str, animal_id: str | None, herd_id: str | None
    ) -> None:
        if animal_id is not None and self.session.scalar(
            select(Animal.id).where(Animal.farm_id == farm_id, Animal.id == animal_id)
        ) is None:
            raise NotFoundError("Animal was not found in this farm.")
        if herd_id is not None and self.session.scalar(
            select(Herd.id).where(Herd.farm_id == farm_id, Herd.id == herd_id)
        ) is None:
            raise NotFoundError("Herd was not found in this farm.")

    @staticmethod
    def _snapshot(schedule: VaccinationSchedule) -> dict[str, str]:
        return {
            "vaccine_name": schedule.vaccine_name,
            "due_at": schedule.due_at.isoformat(),
            "status": schedule.status,
        }

    def _schedule_event(
        self,
        farm_id: str,
        schedule: VaccinationSchedule,
        actor_user_id: str,
        event_type: str,
        before: dict[str, str] | None,
        after: dict[str, str],
    ) -> None:
        self.session.add(
            VaccinationScheduleEvent(
                farm_id=farm_id,
                schedule_id=schedule.id,
                event_type=event_type,
                before_snapshot=before,
                after_snapshot=after,
                reason="Recorded through farm management workflow",
                actor_user_id=actor_user_id,
                occurred_at=utc_now(),
            )
        )


class HealthRecordService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def record(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        observations: str,
        animal_id: str | None = None,
        herd_id: str | None = None,
        action_taken: str | None = None,
        follow_up_at: datetime | None = None,
    ) -> str:
        observations = _required_text(observations, "Observations", 2000)
        if (animal_id is None) == (herd_id is None):
            raise ValidationError("Select exactly one animal or herd.")
        if action_taken is not None and len(action_taken) > 1000:
            raise ValidationError("Action note is too long.")
        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            if animal_id is not None and self.session.scalar(
                select(Animal.id).where(Animal.farm_id == farm_id, Animal.id == animal_id)
            ) is None:
                raise NotFoundError("Animal was not found in this farm.")
            if herd_id is not None and self.session.scalar(
                select(Herd.id).where(Herd.farm_id == farm_id, Herd.id == herd_id)
            ) is None:
                raise NotFoundError("Herd was not found in this farm.")
            record = HealthRecord(
                farm_id=farm_id,
                animal_id=animal_id,
                herd_id=herd_id,
                recorded_at=utc_now(),
                observations=observations,
                action_taken=action_taken.strip() if action_taken else None,
                follow_up_at=follow_up_at,
                recorded_by_user_id=actor_user_id,
            )
            self.session.add(record)
            self.session.flush()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="health_record.created",
                entity_type="health_record",
                entity_id=record.id,
            )
            return record.id


class AlertService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def record_action(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        alert_id: str,
        action: Literal["acknowledge", "note", "close"],
        note: str | None = None,
        reason: str | None = None,
    ) -> None:
        if action not in {"acknowledge", "note", "close"}:
            raise ValidationError("Unsupported alert action.")
        if note is not None and len(note) > 1000:
            raise ValidationError("Alert note is too long.")
        if action == "note":
            note = _required_text(note or "", "Alert note", 1000)
        if action == "close":
            reason = _required_text(reason or "", "Closing reason", 500)
        with atomic(self.session):
            member = require_farm_operator(self.session, farm_id, actor_user_id)
            if action == "close" and member.role not in {"owner", "manager"}:
                raise AuthorizationError("Only a farm owner or manager can close an alert.")
            alert = self.session.scalar(
                select(Alert)
                .where(Alert.farm_id == farm_id, Alert.id == alert_id)
                .with_for_update()
            )
            if alert is None:
                raise NotFoundError("Alert was not found in this farm.")
            if action == "close" and alert.status == "closed":
                raise ValidationError("Alert is already closed.")
            if action == "acknowledge" and alert.status == "closed":
                raise ValidationError("A closed alert cannot be acknowledged.")
            if action == "acknowledge" and alert.status != "new":
                raise ValidationError("Alert has already been acknowledged.")
            event_type = {
                "acknowledge": "acknowledged",
                "note": "note_added",
                "close": "closed",
            }[action]
            if action == "acknowledge" and alert.status == "new":
                alert.status = "acknowledged"
            if action == "close":
                alert.status = "closed"
            self.session.add(
                AlertEvent(
                    farm_id=farm_id,
                    alert_id=alert_id,
                    event_type=event_type,
                    actor_user_id=actor_user_id,
                    reason=reason,
                    note=note.strip() if note else None,
                    occurred_at=utc_now(),
                )
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action=f"alert.{event_type}",
                entity_type="alert",
                entity_id=alert.id,
            )
