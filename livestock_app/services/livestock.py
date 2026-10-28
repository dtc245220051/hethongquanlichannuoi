from collections import defaultdict
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, scoped_session

from livestock_app.models import Animal, Barn, Herd, HerdEvent, Species
from livestock_app.services import (
    NotFoundError,
    ValidationError,
    atomic,
    record_audit,
    require_farm_member,
    require_farm_operator,
    utc_now,
)

_EventType = Literal[
    "acquisition",
    "birth",
    "split",
    "merge",
    "movement",
    "tracking_conversion",
    "loss",
    "sale",
]


def _validate_reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValidationError("A reason of 1 to 500 characters is required.")
    return reason.strip()


def _validate_positive_heads(heads: int) -> None:
    if isinstance(heads, bool) or not isinstance(heads, int) or heads <= 0:
        raise ValidationError("Head count must be a positive integer.")


class LivestockService:
    def __init__(self, session: Session | scoped_session):
        self.session = session() if isinstance(session, scoped_session) else session

    def create_herd(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        name: str,
        species_code: str,
        heads: int,
        reason: str,
        event_type: Literal["acquisition", "birth"],
        barn_id: str | None = None,
    ) -> str:
        _validate_positive_heads(heads)
        reason = _validate_reason(reason)
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("Herd name is required.")
        if event_type not in {"acquisition", "birth"}:
            raise ValidationError("Initial herd event must be acquisition or birth.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            species = self.session.scalar(
                select(Species).where(
                    Species.code == species_code, Species.active.is_(True)
                )
            )
            if species is None:
                raise NotFoundError("Active species was not found.")
            if barn_id is not None:
                self._lock_barn(farm_id, barn_id)
                self._ensure_capacity(farm_id, barn_id, heads)

            herd = Herd(
                farm_id=farm_id,
                name=name.strip(),
                species_code=species_code,
                barn_id=barn_id,
                unidentified_count=heads,
                status="active",
            )
            self.session.add(herd)
            self.session.flush()
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                target_herd_id=herd.id,
                heads=heads,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action=f"livestock.herd.{event_type}",
                entity_type="herd",
                entity_id=herd.id,
            )
            herd_id = herd.id
        return herd_id

    def create_animal(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        species_code: str,
        reason: str,
        event_type: Literal["acquisition", "birth"],
        identifier: str | None = None,
        display_name: str | None = None,
        herd_id: str | None = None,
        barn_id: str | None = None,
    ) -> str:
        reason = _validate_reason(reason)
        if event_type not in {"acquisition", "birth"}:
            raise ValidationError("Animal creation event must be acquisition or birth.")
        if identifier is not None and not identifier.strip():
            raise ValidationError("Animal identifier cannot be blank.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            species = self.session.scalar(
                select(Species).where(
                    Species.code == species_code, Species.active.is_(True)
                )
            )
            if species is None:
                raise NotFoundError("Active species was not found.")

            target_herd = None
            if herd_id is not None:
                target_herd = self._get_herd(farm_id, herd_id, lock=True)
                if target_herd.status != "active":
                    raise ValidationError("Cannot add an animal to an inactive herd.")
                if target_herd.species_code != species_code:
                    raise ValidationError("Animal species must match the herd.")
                if barn_id is not None and barn_id != target_herd.barn_id:
                    raise ValidationError("Animal and herd must have the same location.")
                barn_id = target_herd.barn_id
                if barn_id is not None:
                    self._lock_barn(farm_id, barn_id)
                    self._ensure_capacity(farm_id, barn_id, 1)
            elif barn_id is not None:
                self._lock_barn(farm_id, barn_id)
                self._ensure_capacity(farm_id, barn_id, 1)
            animal = Animal(
                farm_id=farm_id,
                species_code=species_code,
                identifier=identifier.strip() if identifier else None,
                display_name=display_name.strip() if display_name else None,
                herd_id=herd_id,
                barn_id=barn_id,
                status="active",
            )
            self.session.add(animal)
            self.session.flush()
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                target_herd_id=herd_id,
                animal_id=animal.id,
                heads=1,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action=f"livestock.animal.{event_type}",
                entity_type="animal",
                entity_id=animal.id,
            )
            animal_id = animal.id
        return animal_id

    def update_herd(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        herd_id: str,
        name: str,
    ) -> None:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            raise ValidationError("Herd name must contain 1 to 160 characters.")
        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            herd = self._get_herd(farm_id, herd_id, lock=True)
            if herd.status != "active":
                raise ValidationError("Only an active herd can be updated.")
            herd.name = name.strip()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="livestock.herd.updated",
                entity_type="herd",
                entity_id=herd.id,
            )

    def update_animal(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str,
        identifier: str | None,
        display_name: str | None,
    ) -> None:
        if identifier is not None and (
            not identifier.strip() or len(identifier.strip()) > 100
        ):
            raise ValidationError("Animal identifier must contain 1 to 100 characters.")
        if display_name is not None and (
            not display_name.strip() or len(display_name.strip()) > 160
        ):
            raise ValidationError("Display name must contain 1 to 160 characters.")
        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            animal = self.session.scalar(
                select(Animal)
                .where(Animal.farm_id == farm_id, Animal.id == animal_id)
                .with_for_update()
            )
            if animal is None:
                raise NotFoundError("Animal was not found in this farm.")
            if animal.status != "active":
                raise ValidationError("Only an active animal can be updated.")
            animal.identifier = identifier.strip() if identifier else None
            animal.display_name = display_name.strip() if display_name else None
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="livestock.animal.updated",
                entity_type="animal",
                entity_id=animal.id,
            )

    def identify_unidentified_animal(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        herd_id: str,
        identifier: str,
        reason: str,
        display_name: str | None = None,
    ) -> str:
        reason = _validate_reason(reason)
        if not identifier.strip():
            raise ValidationError("Animal identifier is required.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            herd = self._get_herd(farm_id, herd_id, lock=True)
            if herd.status != "active" or herd.unidentified_count < 1:
                raise ValidationError("The active herd has no unidentified animal to convert.")
            herd.unidentified_count -= 1
            animal = Animal(
                farm_id=farm_id,
                species_code=herd.species_code,
                herd_id=herd.id,
                barn_id=herd.barn_id,
                identifier=identifier.strip(),
                display_name=display_name.strip() if display_name else None,
                status="active",
            )
            self.session.add(animal)
            self.session.flush()
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type="tracking_conversion",
                source_herd_id=herd.id,
                target_herd_id=herd.id,
                animal_id=animal.id,
                heads=1,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="livestock.herd.identify_animal",
                entity_type="animal",
                entity_id=animal.id,
            )
            animal_id = animal.id
        return animal_id

    def adjust_unidentified_count(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        herd_id: str,
        delta: int,
        event_type: Literal["acquisition", "birth", "loss", "sale"],
        reason: str,
    ) -> int:
        if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
            raise ValidationError("Count delta must be a nonzero integer.")
        reason = _validate_reason(reason)
        if delta == 0:
            raise ValidationError("Count delta cannot be zero.")
        if (delta > 0 and event_type not in {"acquisition", "birth"}) or (
            delta < 0 and event_type not in {"loss", "sale"}
        ):
            raise ValidationError("Event type must match the direction of the count change.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            herd = self._get_herd(farm_id, herd_id, lock=True)
            if herd.status != "active":
                raise ValidationError("Cannot change the count of an inactive herd.")
            new_count = herd.unidentified_count + delta
            if new_count < 0:
                raise ValidationError("Count change would make unidentified heads negative.")
            if delta > 0 and herd.barn_id:
                self._lock_barn(farm_id, herd.barn_id)
                self._ensure_capacity(farm_id, herd.barn_id, delta)

            herd.unidentified_count = new_count
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                target_herd_id=herd_id if delta > 0 else None,
                source_herd_id=herd_id if delta < 0 else None,
                heads=abs(delta),
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action=f"livestock.herd.{event_type}",
                entity_type="herd",
                entity_id=herd_id,
            )
            result = herd.unidentified_count
        return result

    def split_herd(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        source_herd_id: str,
        target_name: str,
        heads: int,
        reason: str,
        target_barn_id: str | None = None,
    ) -> str:
        _validate_positive_heads(heads)
        reason = _validate_reason(reason)
        if not target_name.strip():
            raise ValidationError("Target herd name is required.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            source = self._get_herd(farm_id, source_herd_id, lock=True)
            if source.status != "active" or source.unidentified_count < heads:
                raise ValidationError(
                    "Split size exceeds the source herd's unidentified head count."
                )
            if target_barn_id is None:
                target_barn_id = source.barn_id
            if target_barn_id is not None:
                self._lock_barn(farm_id, target_barn_id)
                if target_barn_id != source.barn_id:
                    self._ensure_capacity(farm_id, target_barn_id, heads)
            source.unidentified_count -= heads
            target = Herd(
                farm_id=farm_id,
                name=target_name.strip(),
                species_code=source.species_code,
                barn_id=target_barn_id,
                unidentified_count=heads,
                status="active",
            )
            self.session.add(target)
            self.session.flush()
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type="split",
                source_herd_id=source.id,
                target_herd_id=target.id,
                heads=heads,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="livestock.herd.split",
                entity_type="herd",
                entity_id=target.id,
            )
            target_id = target.id
        return target_id

    def merge_herds(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        source_herd_id: str,
        target_herd_id: str,
        reason: str,
    ) -> int:
        reason = _validate_reason(reason)
        if source_herd_id == target_herd_id:
            raise ValidationError("A herd cannot be merged into itself.")

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            preliminary = self.session.scalars(
                select(Herd)
                .where(
                    Herd.farm_id == farm_id,
                    Herd.id.in_([source_herd_id, target_herd_id]),
                )
                .order_by(Herd.id)
            ).all()
            if len(preliminary) != 2:
                raise NotFoundError("Source or target herd was not found.")
            source_hint = next(h for h in preliminary if h.id == source_herd_id)
            target_hint = next(h for h in preliminary if h.id == target_herd_id)
            barn_ids = {
                barn_id
                for barn_id in (source_hint.barn_id, target_hint.barn_id)
                if barn_id is not None
            }
            self._lock_barns(farm_id, barn_ids)
            locked_herds = self.session.scalars(
                select(Herd)
                .where(Herd.farm_id == farm_id, Herd.id.in_(sorted([source_herd_id, target_herd_id])))
                .order_by(Herd.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            source = next(h for h in locked_herds if h.id == source_herd_id)
            target = next(h for h in locked_herds if h.id == target_herd_id)
            if source.status != "active" or target.status != "active":
                raise ValidationError("Only active herds can be merged.")
            if source.species_code != target.species_code:
                raise ValidationError("Herds of different species cannot be merged.")
            if source.barn_id != target.barn_id and target.barn_id is not None:
                self._ensure_capacity(farm_id, target.barn_id, self._herd_heads(source))

            heads = self._herd_heads(source)
            if heads <= 0:
                raise ValidationError("An empty herd cannot be merged.")
            self.session.query(Animal).filter_by(
                farm_id=farm_id, herd_id=source.id, status="active"
            ).update({Animal.herd_id: target.id, Animal.barn_id: target.barn_id})
            target.unidentified_count += source.unidentified_count
            source.unidentified_count = 0
            source.status = "closed"
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type="merge",
                source_herd_id=source.id,
                target_herd_id=target.id,
                heads=heads,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="livestock.herd.merge",
                entity_type="herd",
                entity_id=target.id,
            )
        return heads

    def move_herd(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        herd_id: str,
        target_barn_id: str | None,
        reason: str,
    ) -> int:
        reason = _validate_reason(reason)
        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            herd = self._get_herd(farm_id, herd_id, lock=True)
            if herd.status != "active":
                raise ValidationError("Only active herds can be moved.")
            heads = self._herd_heads(herd)
            if heads <= 0:
                raise ValidationError("An empty herd cannot be moved.")
            if target_barn_id is not None:
                self._lock_barn(farm_id, target_barn_id)
                if target_barn_id != herd.barn_id:
                    self._ensure_capacity(farm_id, target_barn_id, heads)
            herd.barn_id = target_barn_id
            self.session.query(Animal).filter_by(
                farm_id=farm_id, herd_id=herd.id, status="active"
            ).update({Animal.barn_id: target_barn_id})
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type="movement",
                source_herd_id=herd.id,
                target_herd_id=herd.id,
                heads=heads,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="livestock.herd.movement",
                entity_type="herd",
                entity_id=herd.id,
            )
        return heads

    def move_animal(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str,
        target_barn_id: str | None,
        reason: str,
    ) -> None:
        reason = _validate_reason(reason)
        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            animal_hint = self.session.scalar(
                select(Animal).where(
                    Animal.farm_id == farm_id, Animal.id == animal_id
                )
            )
            if animal_hint is None:
                raise NotFoundError("Animal was not found in this farm.")
            source_herd_hint = (
                self.session.scalar(
                    select(Herd).where(
                        Herd.farm_id == farm_id, Herd.id == animal_hint.herd_id
                    )
                )
                if animal_hint.herd_id
                else None
            )
            if animal_hint.herd_id and source_herd_hint is None:
                raise NotFoundError("Animal's herd was not found in this farm.")
            source_barn_id = (
                source_herd_hint.barn_id
                if source_herd_hint is not None
                else animal_hint.barn_id
            )
            barn_ids = {
                barn_id
                for barn_id in (source_barn_id, target_barn_id)
                if barn_id is not None
            }
            self._lock_barns(farm_id, barn_ids)

            source_herd = (
                self._get_herd(farm_id, animal_hint.herd_id, lock=True)
                if animal_hint.herd_id
                else None
            )
            animal = self.session.scalar(
                select(Animal)
                .where(Animal.farm_id == farm_id, Animal.id == animal_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if animal is None or animal.status != "active":
                raise ValidationError("Only an active animal can be moved.")
            if animal.herd_id != animal_hint.herd_id:
                raise ValidationError("Animal tracking changed concurrently; retry the operation.")
            if source_herd is not None and source_herd.barn_id != source_barn_id:
                raise ValidationError("Herd location changed concurrently; retry the operation.")
            if source_herd is not None and source_herd.status != "active":
                raise ValidationError("Cannot move an animal from an inactive herd.")
            if target_barn_id != source_barn_id and target_barn_id is not None:
                self._ensure_capacity(farm_id, target_barn_id, 1)

            animal.herd_id = None
            animal.barn_id = target_barn_id
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type="movement",
                source_herd_id=source_herd.id if source_herd else None,
                animal_id=animal.id,
                heads=1,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="livestock.animal.movement",
                entity_type="animal",
                entity_id=animal.id,
            )

    def change_animal_status(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        animal_id: str,
        status: Literal["deceased", "sold"],
        reason: str,
    ) -> None:
        reason = _validate_reason(reason)
        if status not in {"deceased", "sold"}:
            raise ValidationError("Animal status can only change to deceased or sold.")
        event_type = "loss" if status == "deceased" else "sale"

        with atomic(self.session):
            require_farm_operator(self.session, farm_id, actor_user_id)
            animal = self.session.scalar(
                select(Animal)
                .where(Animal.farm_id == farm_id, Animal.id == animal_id)
                .with_for_update()
            )
            if animal is None:
                raise NotFoundError("Animal was not found in this farm.")
            if animal.status != "active":
                raise ValidationError("Only active animals can be marked sold or deceased.")
            source_herd_id = animal.herd_id
            if source_herd_id is not None:
                herd = self._get_herd(farm_id, source_herd_id, lock=True)
                if herd.status != "active":
                    raise ValidationError("The animal is attached to an inactive herd.")
            animal.status = status
            self._add_event(
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                source_herd_id=source_herd_id,
                animal_id=animal.id,
                heads=1,
                reason=reason,
            )
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action=f"livestock.animal.{event_type}",
                entity_type="animal",
                entity_id=animal.id,
            )

    def farm_head_count(self, *, farm_id: str, actor_user_id: str) -> int:
        require_farm_member(self.session, farm_id, actor_user_id)
        herds = self.session.scalars(
            select(Herd).where(Herd.farm_id == farm_id, Herd.status == "active")
        ).all()
        herd_ids = [herd.id for herd in herds]
        animals_by_herd: dict[str, int] = defaultdict(int)
        standalone_count = 0
        for animal in self.session.scalars(
            select(Animal).where(
                Animal.farm_id == farm_id,
                Animal.status == "active",
                or_(Animal.herd_id.is_(None), Animal.herd_id.in_(herd_ids))
                if herd_ids
                else Animal.herd_id.is_(None),
            )
        ):
            if animal.herd_id is None:
                standalone_count += 1
            else:
                animals_by_herd[animal.herd_id] += 1
        return standalone_count + sum(
            herd.unidentified_count + animals_by_herd[herd.id] for herd in herds
        )

    def _get_herd(self, farm_id: str, herd_id: str, *, lock: bool) -> Herd:
        query = select(Herd).where(Herd.farm_id == farm_id, Herd.id == herd_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        herd = self.session.scalar(query)
        if herd is None:
            raise NotFoundError("Herd was not found in this farm.")
        return herd

    def _herd_heads(self, herd: Herd) -> int:
        if herd.status != "active":
            return 0
        animal_ids = self.session.scalars(
            select(Animal.id)
            .where(
                Animal.farm_id == herd.farm_id,
                Animal.herd_id == herd.id,
                Animal.status == "active",
            )
            .with_for_update()
        ).all()
        return herd.unidentified_count + len(animal_ids)

    def _lock_barn(self, farm_id: str, barn_id: str) -> Barn:
        return self._lock_barns(farm_id, {barn_id})[barn_id]

    def _lock_barns(self, farm_id: str, barn_ids: set[str]) -> dict[str, Barn]:
        if not barn_ids:
            return {}
        barns = self.session.scalars(
            select(Barn)
            .where(
                Barn.farm_id == farm_id,
                Barn.id.in_(sorted(barn_ids)),
                Barn.status == "active",
            )
            .order_by(Barn.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if len(barns) != len(barn_ids):
            raise NotFoundError("An active barn was not found in this farm.")
        return {barn.id: barn for barn in barns}

    def _ensure_capacity(self, farm_id: str, barn_id: str, additional_heads: int) -> None:
        barn = self._lock_barn(farm_id, barn_id)
        active_herds = self.session.scalars(
            select(Herd)
            .where(
                Herd.farm_id == farm_id,
                Herd.barn_id == barn_id,
                Herd.status == "active",
            )
            .order_by(Herd.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        herd_ids = [herd.id for herd in active_herds]
        animal_query = select(Animal).where(
            Animal.farm_id == farm_id,
            Animal.status == "active",
            or_(
                Animal.herd_id.in_(herd_ids),
                Animal.herd_id.is_(None) & (Animal.barn_id == barn_id),
            )
            if herd_ids
            else Animal.herd_id.is_(None) & (Animal.barn_id == barn_id),
        ).with_for_update().execution_options(populate_existing=True)
        active_animals = self.session.scalars(animal_query).all()
        animal_counts: dict[str, int] = defaultdict(int)
        standalone_count = 0
        for animal in active_animals:
            if animal.herd_id is None:
                standalone_count += 1
            else:
                animal_counts[animal.herd_id] += 1
        occupancy = standalone_count + sum(
            herd.unidentified_count + animal_counts[herd.id] for herd in active_herds
        )
        if occupancy + additional_heads > barn.capacity_heads:
            raise ValidationError("Operation would exceed the barn's configured capacity.")

    def _add_event(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        event_type: _EventType,
        heads: int,
        reason: str,
        source_herd_id: str | None = None,
        target_herd_id: str | None = None,
        animal_id: str | None = None,
    ) -> None:
        self.session.add(
            HerdEvent(
                farm_id=farm_id,
                event_type=event_type,
                source_herd_id=source_herd_id,
                target_herd_id=target_herd_id,
                animal_id=animal_id,
                head_count=heads,
                occurred_at=utc_now(),
                reason=reason,
                recorded_by_user_id=actor_user_id,
            )
        )
