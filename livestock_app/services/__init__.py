from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from livestock_app.models import AuditLog, FarmMembership, FarmPermissionGrant, User


class ServiceError(Exception):
    """Base class for expected business-service failures."""


class AuthorizationError(ServiceError):
    pass


class NotFoundError(ServiceError):
    pass


class ValidationError(ServiceError):
    pass


class InsufficientStockError(ServiceError):
    pass


@contextmanager
def atomic(session: Session) -> Iterator[None]:
    if session.in_transaction():
        with session.begin_nested():
            yield
    else:
        with session.begin():
            yield


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def require_farm_member(
    session: Session,
    farm_id: str,
    user_id: str,
    *,
    permissions: tuple[str, ...] = (),
) -> FarmMembership:
    member = session.scalar(
        select(FarmMembership)
        .join(User, User.id == FarmMembership.user_id)
        .where(
            FarmMembership.farm_id == farm_id,
            FarmMembership.user_id == user_id,
            User.status == "active",
        )
    )
    if member is None:
        raise AuthorizationError("The user is not an active member of this farm.")
    if member.role in {"owner", "manager"} or not permissions:
        return member

    now = utc_now()
    grants = session.scalars(
        select(FarmPermissionGrant).where(
            FarmPermissionGrant.farm_id == farm_id,
            FarmPermissionGrant.user_id == user_id,
            FarmPermissionGrant.permission_code.in_(permissions),
            FarmPermissionGrant.revoked_at.is_(None),
        )
    )
    if any(_as_utc(grant.granted_at) <= now for grant in grants):
        return member
    raise AuthorizationError("The user lacks the required farm permission.")


def require_farm_operator(
    session: Session,
    farm_id: str,
    user_id: str,
    *,
    permissions: tuple[str, ...] = (),
) -> FarmMembership:
    member = require_farm_member(
        session,
        farm_id,
        user_id,
        permissions=permissions,
    )
    if member.role == "veterinarian":
        raise AuthorizationError("Veterinarian memberships are read-only.")
    return member


def require_farm_manager(
    session: Session,
    farm_id: str,
    user_id: str,
) -> FarmMembership:
    member = require_farm_member(session, farm_id, user_id)
    if member.role not in {"owner", "manager"}:
        raise AuthorizationError("Only a farm owner or manager can perform this action.")
    return member


def record_audit(
    session: Session,
    *,
    farm_id: str,
    actor_user_id: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    occurred_at: datetime | None = None,
) -> None:
    session.add(
        AuditLog(
            farm_id=farm_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            outcome="success",
            request_id=str(uuid4()),
            occurred_at=occurred_at or utc_now(),
        )
    )
