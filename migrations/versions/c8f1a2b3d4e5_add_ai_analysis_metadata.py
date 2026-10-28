"""Add metadata for AI-generated growth and feed results

Revision ID: c8f1a2b3d4e5
Revises: b7f2c9a41d3e
Create Date: 2026-10-28 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c8f1a2b3d4e5"
down_revision = "b7f2c9a41d3e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "growth_predictions",
        sa.Column("ai_model", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "growth_predictions",
        sa.Column("input_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "growth_predictions",
        sa.Column("trend", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "growth_predictions",
        sa.Column("confidence", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "growth_predictions",
        sa.Column("limitations", sa.Text(), nullable=True),
    )

    op.add_column(
        "feed_plans",
        sa.Column("source", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("ai_model", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("input_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("rationale", sa.Text(), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("limitations", sa.Text(), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("confidence", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("feedings_per_day", sa.Integer(), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("schedule_note", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "feed_plans",
        sa.Column("accepted_by_user_id", sa.String(length=36), nullable=True),
    )


def downgrade():
    op.drop_column("feed_plans", "accepted_by_user_id")
    op.drop_column("feed_plans", "accepted_at")
    op.drop_column("feed_plans", "schedule_note")
    op.drop_column("feed_plans", "feedings_per_day")
    op.drop_column("feed_plans", "confidence")
    op.drop_column("feed_plans", "limitations")
    op.drop_column("feed_plans", "rationale")
    op.drop_column("feed_plans", "input_snapshot")
    op.drop_column("feed_plans", "ai_model")
    op.drop_column("feed_plans", "status")
    op.drop_column("feed_plans", "source")

    op.drop_column("growth_predictions", "limitations")
    op.drop_column("growth_predictions", "confidence")
    op.drop_column("growth_predictions", "trend")
    op.drop_column("growth_predictions", "input_snapshot")
    op.drop_column("growth_predictions", "ai_model")
