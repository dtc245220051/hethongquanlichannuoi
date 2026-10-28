"""Bridge the schema state recorded in the live database

Revision ID: 07780722d2de
Revises: f173c942d5a1
Create Date: 2026-10-28 07:00:00.000000

The live MySQL database stores ``07780722d2de`` in ``alembic_version`` even
though the revision script was never committed. Its schema matches
``f173c942d5a1`` plus a legacy ``platform_admins`` table (``user_id`` primary
key, no ``id`` column). This revision makes that state explicit and
reproducible without touching an existing table or its data.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "07780722d2de"
down_revision = "f173c942d5a1"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("platform_admins"):
        # The live database already has this table. Never recreate or modify it
        # here; b7f2c9a41d3e is responsible for reconciling its shape.
        return

    op.create_table(
        "platform_admins",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name=op.f("fk_platform_admins_granted_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_platform_admins_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade():
    # The legacy table may predate Alembic. Keep it while stepping back to
    # f173c942d5a1; the core-schema downgrade removes it only when the whole
    # schema is being torn down.
    pass
