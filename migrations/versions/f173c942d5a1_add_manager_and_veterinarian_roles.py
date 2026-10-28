"""Add farm manager and veterinarian membership roles

Revision ID: f173c942d5a1
Revises: a69e06d278df
Create Date: 2026-10-28 04:28:42

"""
from alembic import op
import sqlalchemy as sa


revision = "f173c942d5a1"
down_revision = "a69e06d278df"
branch_labels = None
depends_on = None


_ROLE_CHECK = "role IN ('owner', 'care_staff', 'manager', 'veterinarian')"
_ROLE_CONSTRAINT = "ck_user_farm_roles_valid_role"


def _replace_role_constraint(condition: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        connection = op.get_bind()
        op.execute("PRAGMA foreign_keys=OFF")
        with op.batch_alter_table(
            "user_farm_roles",
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(op.f(_ROLE_CONSTRAINT), type_="check")
            batch_op.create_check_constraint("valid_role", condition)
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS block_delete_user_farm_roles "
            "BEFORE DELETE ON user_farm_roles "
            "BEGIN SELECT RAISE(ABORT, 'Hard delete is disabled'); END"
        )
        op.execute("PRAGMA foreign_keys=ON")
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Role migration left foreign key violations.")
    elif dialect == "mysql":
        op.drop_constraint(
            op.f(_ROLE_CONSTRAINT),
            "user_farm_roles",
            type_="check",
        )
        op.create_check_constraint(
            "valid_role",
            "user_farm_roles",
            condition,
        )
    else:
        raise RuntimeError(f"Role migration is not implemented for {dialect}.")


def upgrade():
    _replace_role_constraint(_ROLE_CHECK)


def downgrade():
    connection = op.get_bind()
    has_new_memberships = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM user_farm_roles "
            "WHERE role IN ('manager', 'veterinarian')"
        )
    )
    if has_new_memberships:
        raise RuntimeError(
            "Cannot downgrade while manager or veterinarian memberships exist."
        )
    _replace_role_constraint("role IN ('owner', 'care_staff')")
