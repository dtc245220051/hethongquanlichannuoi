import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session, scoped_session
from werkzeug.security import generate_password_hash

from livestock_app.models import Farm, FarmMembership, User
from livestock_app.services import (
    AuthorizationError,
    ServiceError,
    ValidationError,
    atomic,
    record_audit,
    require_farm_member,
)


class DuplicateMemberError(ServiceError):
    pass


def _session(session: Session | scoped_session) -> Session:
    return session() if isinstance(session, scoped_session) else session


class FarmMemberService:
    def __init__(self, session: Session | scoped_session):
        self.session = _session(session)

    def create_member(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        role: str,
        email: str,
        display_name: str,
        password: str,
    ) -> str:
        if not isinstance(role, str) or role not in {
            "care_staff",
            "manager",
            "veterinarian",
        }:
            raise ValidationError("Vai trò thành viên không hợp lệ.")
        normalized_email = email.strip().lower() if isinstance(email, str) else ""
        if (
            len(normalized_email) > 254
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized_email)
        ):
            raise ValidationError("Vui lòng nhập email hợp lệ.")
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or len(display_name.strip()) > 160
        ):
            raise ValidationError("Tên nhân viên phải từ 1 đến 160 ký tự.")
        if not isinstance(password, str) or len(password) < 12:
            raise ValidationError("Mật khẩu phải có ít nhất 12 ký tự.")

        with atomic(self.session):
            actor_membership = require_farm_member(
                self.session, farm_id, actor_user_id
            )
            if actor_membership.role != "owner":
                raise AuthorizationError(
                    "Chỉ chủ trang trại mới được quản lý thành viên."
                )
            farm = self.session.scalar(
                select(Farm).where(Farm.id == farm_id, Farm.status == "active")
            )
            if farm is None:
                raise AuthorizationError("Trang trại không hoạt động.")

            existing_user = self.session.scalar(
                select(User).where(func.lower(User.email) == normalized_email)
            )
            if existing_user is not None:
                if existing_user.status != "active":
                    raise DuplicateMemberError(
                        "Email đã thuộc tài khoản bị vô hiệu hóa; không thể cấp lại."
                    )
                raise DuplicateMemberError("Email đã được sử dụng.")

            user = User(
                email=normalized_email,
                password_hash=generate_password_hash(password),
                display_name=display_name.strip(),
                status="active",
            )
            self.session.add(user)
            self.session.flush()
            membership = FarmMembership(
                farm_id=farm_id,
                user_id=user.id,
                role=role,
                created_by_user_id=actor_user_id,
            )
            self.session.add(membership)
            self.session.flush()
            record_audit(
                self.session,
                farm_id=farm_id,
                actor_user_id=actor_user_id,
                action="farm.member.created",
                entity_type="user",
                entity_id=user.id,
            )
            return user.id

    def create_care_staff(
        self,
        *,
        farm_id: str,
        actor_user_id: str,
        email: str,
        display_name: str,
        password: str,
    ) -> str:
        return self.create_member(
            farm_id=farm_id,
            actor_user_id=actor_user_id,
            role="care_staff",
            email=email,
            display_name=display_name,
            password=password,
        )
