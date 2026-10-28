"""Add or reconcile platform administrators

Revision ID: b7f2c9a41d3e
Revises: 661550e6b932
Create Date: 2026-10-28 06:30:00.000000

The live database already contains a legacy ``platform_admins`` table whose
primary key is ``user_id`` and which has no ``id`` column. This revision keeps
every existing row, adds stable UUID ``id`` values, and moves the primary key
to ``id`` so the table matches ``PlatformAdmin`` (which uses ``UUIDMixin``).
"""
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b7f2c9a41d3e"
down_revision = "661550e6b932"
branch_labels = None
depends_on = None


_TABLE = "platform_admins"
_TEMP_TABLE = "platform_admins_repair"
_UNIQUE = "unique_platform_admin_user"
_FK_USER = "fk_platform_admins_user_id_users"
_FK_GRANTED_BY = "fk_platform_admins_granted_by_user_id_users"


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(inspector):
    return {column["name"]: column for column in inspector.get_columns(_TABLE)}


def _primary_key(inspector):
    constraint = inspector.get_pk_constraint(_TABLE)
    return list(constraint.get("constrained_columns") or [])


def _unique_column_sets(inspector):
    column_sets = []
    for constraint in inspector.get_unique_constraints(_TABLE):
        column_sets.append(tuple(constraint.get("column_names") or ()))
    for index in inspector.get_indexes(_TABLE):
        if index.get("unique"):
            column_sets.append(tuple(index.get("column_names") or ()))
    return column_sets


def _has_foreign_key(inspector, constrained_columns, referred_table, referred_columns):
    for foreign_key in inspector.get_foreign_keys(_TABLE):
        if (
            list(foreign_key.get("constrained_columns") or []) == constrained_columns
            and foreign_key.get("referred_table") == referred_table
            and list(foreign_key.get("referred_columns") or []) == referred_columns
        ):
            return True
    return False


def _create_current_table(table_name):
    op.create_table(
        table_name,
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(_FK_USER),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name=op.f(_FK_GRANTED_BY),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_admins")),
        sa.UniqueConstraint("user_id", name=_UNIQUE),
    )


def _create_legacy_table(table_name):
    op.create_table(
        table_name,
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name=op.f(_FK_GRANTED_BY),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(_FK_USER),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def _recover_interrupted_sqlite_swap():
    if op.get_bind().dialect.name != "sqlite":
        return
    inspector = _inspector()
    if inspector.has_table(_TABLE):
        return
    if inspector.has_table(_TEMP_TABLE):
        op.rename_table(_TEMP_TABLE, _TABLE)


def _rebuild_sqlite(to_current):
    connection = op.get_bind()
    rows = connection.execute(sa.text(f"SELECT * FROM {_TABLE}")).mappings().all()

    inspector = _inspector()
    if inspector.has_table(_TEMP_TABLE):
        op.drop_table(_TEMP_TABLE)

    if to_current:
        _create_current_table(_TEMP_TABLE)
        for row in rows:
            connection.execute(
                sa.text(
                    f"INSERT INTO {_TEMP_TABLE} "
                    "(id, user_id, granted_by_user_id, created_at) "
                    "VALUES (:id, :user_id, :granted_by_user_id, :created_at)"
                ),
                {
                    "id": row.get("id") or str(uuid4()),
                    "user_id": row["user_id"],
                    "granted_by_user_id": row["granted_by_user_id"],
                    "created_at": row["created_at"],
                },
            )
    else:
        _create_legacy_table(_TEMP_TABLE)
        for row in rows:
            connection.execute(
                sa.text(
                    f"INSERT INTO {_TEMP_TABLE} "
                    "(user_id, granted_by_user_id, created_at) "
                    "VALUES (:user_id, :granted_by_user_id, :created_at)"
                ),
                {
                    "user_id": row["user_id"],
                    "granted_by_user_id": row["granted_by_user_id"],
                    "created_at": row["created_at"],
                },
            )

    copied = connection.execute(
        sa.text(f"SELECT COUNT(*) FROM {_TEMP_TABLE}")
    ).scalar()
    if copied != len(rows):
        raise RuntimeError(
            "Refusing to replace platform_admins because row counts differ."
        )

    op.drop_table(_TABLE)
    op.rename_table(_TEMP_TABLE, _TABLE)


def _upgrade_mysql():
    connection = op.get_bind()
    inspector = _inspector()
    columns = _columns(inspector)

    if "id" not in columns:
        op.add_column(
            _TABLE,
            sa.Column("id", sa.String(length=36), nullable=True),
        )
        inspector = _inspector()
        columns = _columns(inspector)

    for (user_id,) in connection.execute(
        sa.text(f"SELECT user_id FROM {_TABLE} WHERE id IS NULL")
    ).fetchall():
        connection.execute(
            sa.text(f"UPDATE {_TABLE} SET id = :id WHERE user_id = :user_id"),
            {"id": str(uuid4()), "user_id": user_id},
        )

    if columns["id"]["nullable"]:
        op.alter_column(
            _TABLE,
            "id",
            existing_type=sa.String(length=36),
            nullable=False,
        )

    inspector = _inspector()
    if ("user_id",) not in _unique_column_sets(inspector):
        op.create_unique_constraint(_UNIQUE, _TABLE, ["user_id"])

    inspector = _inspector()
    primary_key = _primary_key(inspector)
    if primary_key != ["id"]:
        if primary_key:
            op.execute(
                f"ALTER TABLE {_TABLE} DROP PRIMARY KEY, ADD PRIMARY KEY (id)"
            )
        else:
            op.execute(f"ALTER TABLE {_TABLE} ADD PRIMARY KEY (id)")

    inspector = _inspector()
    if not _has_foreign_key(inspector, ["user_id"], "users", ["id"]):
        op.create_foreign_key(
            _FK_USER,
            _TABLE,
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if not _has_foreign_key(
        inspector, ["granted_by_user_id"], "users", ["id"]
    ):
        op.create_foreign_key(
            _FK_GRANTED_BY,
            _TABLE,
            "users",
            ["granted_by_user_id"],
            ["id"],
        )


def _downgrade_mysql():
    inspector = _inspector()
    columns = _columns(inspector)
    if "id" not in columns:
        return

    primary_key = _primary_key(inspector)
    if primary_key != ["user_id"]:
        if ("user_id",) not in _unique_column_sets(inspector):
            op.create_unique_constraint(_UNIQUE, _TABLE, ["user_id"])
        if primary_key:
            op.execute(
                f"ALTER TABLE {_TABLE} DROP PRIMARY KEY, "
                "ADD PRIMARY KEY (user_id)"
            )
        else:
            op.execute(f"ALTER TABLE {_TABLE} ADD PRIMARY KEY (user_id)")

    inspector = _inspector()
    unique_names = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints(_TABLE)
    }
    if _UNIQUE in unique_names:
        op.drop_constraint(_UNIQUE, _TABLE, type_="unique")

    inspector = _inspector()
    if "id" in _columns(inspector):
        op.drop_column(_TABLE, "id")


def upgrade():
    _recover_interrupted_sqlite_swap()

    inspector = _inspector()
    if not inspector.has_table(_TABLE):
        _create_current_table(_TABLE)
        return

    dialect = op.get_bind().dialect.name
    if dialect == "mysql":
        _upgrade_mysql()
    elif dialect == "sqlite":
        columns = _columns(inspector)
        needs_rebuild = (
            "id" not in columns
            or _primary_key(inspector) != ["id"]
            or columns["id"]["nullable"]
            or ("user_id",) not in _unique_column_sets(inspector)
            or op.get_bind().execute(
                sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE id IS NULL")
            ).scalar()
        )
        if needs_rebuild:
            _rebuild_sqlite(to_current=True)
    else:
        raise RuntimeError(
            f"Platform administrator migration is not implemented for {dialect}."
        )


def downgrade():
    _recover_interrupted_sqlite_swap()

    inspector = _inspector()
    if not inspector.has_table(_TABLE):
        return

    dialect = op.get_bind().dialect.name
    if dialect == "mysql":
        _downgrade_mysql()
    elif dialect == "sqlite":
        if "id" in _columns(inspector):
            _rebuild_sqlite(to_current=False)
    else:
        raise RuntimeError(
            f"Platform administrator migration is not implemented for {dialect}."
        )
