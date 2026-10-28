"""Add AI features UC013 UC014 UC015

Revision ID: 661550e6b932
Revises: 07780722d2de
Create Date: 2026-10-28 06:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "661550e6b932"
down_revision = "07780722d2de"
branch_labels = None
depends_on = None


_NEW_TABLES = (
    "disease_risk_assessments",
    "weight_records",
    "growth_predictions",
    "feed_plans",
    "feed_plan_items",
)


def _create_table_if_missing(table_name, *columns, **kwargs) -> None:
    if not sa.inspect(op.get_bind()).has_table(table_name):
        op.create_table(table_name, *columns, **kwargs)


def _create_no_hard_delete_triggers() -> None:
    dialect = op.get_bind().dialect.name
    for table_name in _NEW_TABLES:
        trigger_name = f"block_delete_{table_name}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        if dialect == "sqlite":
            op.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE DELETE ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, 'Hard delete is disabled'); END"
            )
        elif dialect == "mysql":
            op.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE DELETE ON {table_name} "
                "FOR EACH ROW SIGNAL SQLSTATE '45000' "
                "SET MESSAGE_TEXT = 'Hard delete is disabled'"
            )
        else:
            raise RuntimeError(
                f"Hard-delete protections are not implemented for {dialect}."
            )


def _drop_no_hard_delete_triggers() -> None:
    for table_name in _NEW_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS block_delete_{table_name}")


def upgrade():
    _create_table_if_missing(
        "disease_risk_assessments",
        sa.Column("animal_id", sa.String(length=36), nullable=True),
        sa.Column("herd_id", sa.String(length=36), nullable=True),
        sa.Column("symptoms", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("signs_used", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.String(length=2000), nullable=False),
        sa.Column("recommendations", sa.String(length=2000), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("emergency_warning", sa.Boolean(), nullable=False),
        sa.Column("performed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("synthetic_dataset_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name=op.f("ck_disease_risk_assessments_valid_risk_level"),
        ),
        sa.CheckConstraint(
            "(animal_id IS NOT NULL AND herd_id IS NULL) OR "
            "(animal_id IS NULL AND herd_id IS NOT NULL)",
            name=op.f(
                "ck_disease_risk_assessments_disease_risk_exactly_one_subject"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name=op.f("fk_disease_risk_assessments_farm_id_farms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="disease_risk_animal_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="disease_risk_herd_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "performed_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="disease_risk_actor_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="disease_risk_assessment_synthetic_dataset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disease_risk_assessments")),
        sa.UniqueConstraint("farm_id", "id", name="farm_disease_risk_assessment"),
    )

    _create_table_if_missing(
        "weight_records",
        sa.Column("animal_id", sa.String(length=36), nullable=False),
        sa.Column("weight_kg", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("recorded_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("synthetic_dataset_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "weight_kg > 0", name=op.f("ck_weight_records_positive_weight_kg")
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name=op.f("fk_weight_records_farm_id_farms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="weight_record_animal_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "recorded_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="weight_record_actor_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="weight_record_synthetic_dataset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weight_records")),
        sa.UniqueConstraint("farm_id", "id", name="farm_weight_record"),
    )

    _create_table_if_missing(
        "growth_predictions",
        sa.Column("animal_id", sa.String(length=36), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("history_count", sa.Integer(), nullable=False),
        sa.Column("predicted", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.String(length=2000), nullable=False),
        sa.Column("performed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("synthetic_dataset_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name=op.f("fk_growth_predictions_farm_id_farms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="growth_prediction_animal_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "performed_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="growth_prediction_actor_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="growth_prediction_synthetic_dataset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_growth_predictions")),
        sa.UniqueConstraint("farm_id", "id", name="farm_growth_prediction"),
    )

    _create_table_if_missing(
        "feed_plans",
        sa.Column("animal_id", sa.String(length=36), nullable=True),
        sa.Column("herd_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("assumptions", sa.String(length=1000), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("synthetic_dataset_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "(animal_id IS NOT NULL AND herd_id IS NULL) OR "
            "(animal_id IS NULL AND herd_id IS NOT NULL)",
            name=op.f("ck_feed_plans_feed_plan_exactly_one_subject"),
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name=op.f("fk_feed_plans_farm_id_farms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="feed_plan_animal_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "herd_id"],
            ["herds.farm_id", "herds.id"],
            name="feed_plan_herd_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "created_by_user_id"],
            ["user_farm_roles.farm_id", "user_farm_roles.user_id"],
            name="feed_plan_actor_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="feed_plan_synthetic_dataset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_plans")),
        sa.UniqueConstraint("farm_id", "id", name="farm_feed_plan"),
    )

    _create_table_if_missing(
        "feed_plan_items",
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("feed_name", sa.String(length=160), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("price_per_unit", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("amount_per_day", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("nutrients", sa.JSON(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("synthetic_dataset_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name=op.f("fk_feed_plan_items_farm_id_farms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "plan_id"],
            ["feed_plans.farm_id", "feed_plans.id"],
            name="feed_plan_item_plan_same_farm",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "synthetic_dataset_id"],
            ["synthetic_datasets.farm_id", "synthetic_datasets.id"],
            name="feed_plan_item_synthetic_dataset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feed_plan_items")),
        sa.UniqueConstraint("farm_id", "id", name="farm_feed_plan_item"),
    )

    _create_no_hard_delete_triggers()


def downgrade():
    _drop_no_hard_delete_triggers()
    for table_name in (
        "feed_plan_items",
        "feed_plans",
        "growth_predictions",
        "weight_records",
        "disease_risk_assessments",
    ):
        if sa.inspect(op.get_bind()).has_table(table_name):
            op.drop_table(table_name)
