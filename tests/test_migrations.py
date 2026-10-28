import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import URL


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _migration_command(
    database_url: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "SECRET_KEY": "migration-test-only",
            "DATABASE_URL": database_url,
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "flask",
            "--app",
            "livestock_app:create_app",
            "db",
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_initial_migration_up_and_down_on_isolated_sqlite(tmp_path):
    database_path = tmp_path / "migration-test.db"
    database_url = URL.create("sqlite", database=str(database_path)).render_as_string()

    upgrade_result = _migration_command(database_url, "upgrade")
    assert upgrade_result.returncode == 0, upgrade_result.stderr

    check_result = _migration_command(database_url, "check")
    assert check_result.returncode == 0, check_result.stderr

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "farms" in tables
        assert "vaccination_schedules" in tables
        assert "vaccination_outcomes" in tables
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "block_delete_farms" in trigger_names
        assert "block_delete_audit_logs" in trigger_names
        assert "block_delete_feed_transactions" in trigger_names

        connection.execute(
            "INSERT INTO farms (id, name, timezone, status) "
            "VALUES (?, ?, ?, ?)",
            ("11111111-1111-4111-8111-111111111111", "Synthetic", "UTC", "active"),
        )
        connection.commit()
        try:
            connection.execute(
                "DELETE FROM farms WHERE id = ?",
                ("11111111-1111-4111-8111-111111111111",),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
        else:
            raise AssertionError("Migration must prevent hard deletes.")
        assert connection.execute("SELECT COUNT(*) FROM farms").fetchone()[0] == 1
    finally:
        connection.close()

    downgrade_result = _migration_command(database_url, "downgrade", "base")
    assert downgrade_result.returncode == 0, downgrade_result.stderr

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "farms" not in tables
        assert "vaccination_schedules" not in tables
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert not any(name.startswith("block_delete_") for name in triggers)
    finally:
        connection.close()


def test_role_migration_accepts_manager_and_veterinarian_and_preserves_history(
    tmp_path,
):
    database_path = tmp_path / "role-migration-test.db"
    database_url = URL.create("sqlite", database=str(database_path)).render_as_string()
    upgrade_result = _migration_command(
        database_url,
        "upgrade",
        "a69e06d278df",
    )
    assert upgrade_result.returncode == 0, upgrade_result.stderr

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "INSERT INTO farms (id, name, timezone, status) VALUES (?, ?, ?, ?)",
            ("farm-1", "Synthetic", "UTC", "active"),
        )
        for user_id, email in (
            ("user-owner", "owner@example.test"),
            ("user-staff", "staff@example.test"),
            ("user-manager", "manager@example.test"),
            ("user-vet", "vet@example.test"),
        ):
            connection.execute(
                "INSERT INTO users (id, email, password_hash, display_name, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, email, "test-hash", email, "active"),
            )
        connection.execute(
            "INSERT INTO user_farm_roles "
            "(id, farm_id, user_id, role, created_by_user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("membership-owner", "farm-1", "user-owner", "owner", None),
        )
        connection.execute(
            "INSERT INTO user_farm_roles "
            "(id, farm_id, user_id, role, created_by_user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("membership-staff", "farm-1", "user-staff", "care_staff", "user-owner"),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_result = _migration_command(database_url, "upgrade")
    assert upgrade_result.returncode == 0, upgrade_result.stderr

    connection = sqlite3.connect(database_path)
    try:
        for membership_id, user_id, role in (
            ("membership-manager", "user-manager", "manager"),
            ("membership-vet", "user-vet", "veterinarian"),
        ):
            connection.execute(
                "INSERT INTO user_farm_roles "
                "(id, farm_id, user_id, role, created_by_user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (membership_id, "farm-1", user_id, role, "user-owner"),
            )
        connection.commit()
        assert connection.execute(
            "SELECT role FROM user_farm_roles ORDER BY role"
        ).fetchall() == [
            ("care_staff",),
            ("manager",),
            ("owner",),
            ("veterinarian",),
        ]
        trigger = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='block_delete_user_farm_roles'"
        ).fetchone()
        assert trigger is not None
        try:
            connection.execute(
                "INSERT INTO user_farm_roles "
                "(id, farm_id, user_id, role, created_by_user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("membership-invalid", "farm-1", "user-vet", "admin", "user-owner"),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
        else:
            raise AssertionError("The role check must reject unsupported roles.")
    finally:
        connection.close()


def test_legacy_platform_admins_is_reconciled_without_losing_rows(tmp_path):
    database_path = tmp_path / "legacy-platform-admins-test.db"
    database_url = URL.create("sqlite", database=str(database_path)).render_as_string()

    upgrade_result = _migration_command(database_url, "upgrade", "f173c942d5a1")
    assert upgrade_result.returncode == 0, upgrade_result.stderr

    admin_user_id = "597d92ca-a03a-4fc3-8edc-32b15452dfea"
    created_at = "2026-10-27 22:48:46"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO users (id, email, password_hash, display_name, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (admin_user_id, "admin@example.test", "test-hash", "Admin", "active"),
        )
        connection.execute(
            "CREATE TABLE platform_admins ("
            "user_id VARCHAR(36) NOT NULL, "
            "granted_by_user_id VARCHAR(36), "
            "created_at DATETIME NOT NULL, "
            "PRIMARY KEY (user_id), "
            "CONSTRAINT fk_platform_admins_user_id_users "
            "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE, "
            "CONSTRAINT fk_platform_admins_granted_by_user_id_users "
            "FOREIGN KEY (granted_by_user_id) REFERENCES users (id))"
        )
        connection.execute(
            "INSERT INTO platform_admins (user_id, granted_by_user_id, created_at) "
            "VALUES (?, ?, ?)",
            (admin_user_id, None, created_at),
        )
        connection.execute(
            "UPDATE alembic_version SET version_num = ?",
            ("07780722d2de",),
        )
        connection.commit()
    finally:
        connection.close()

    upgrade_result = _migration_command(database_url, "upgrade")
    assert upgrade_result.returncode == 0, upgrade_result.stderr

    check_result = _migration_command(database_url, "check")
    assert check_result.returncode == 0, check_result.stderr

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table_name in (
            "disease_risk_assessments",
            "weight_records",
            "growth_predictions",
            "feed_plans",
            "feed_plan_items",
        ):
            assert table_name in tables

        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(platform_admins)")
        }
        assert set(columns) == {"id", "user_id", "granted_by_user_id", "created_at"}
        assert [row[1] for row in columns.values() if row[5]] == ["id"]

        unique_column_sets = set()
        for index in connection.execute("PRAGMA index_list(platform_admins)"):
            if index[2]:
                unique_column_sets.add(
                    tuple(
                        row[2]
                        for row in connection.execute(
                            f"PRAGMA index_info({index[1]})"
                        )
                    )
                )
        assert ("user_id",) in unique_column_sets

        row = connection.execute(
            "SELECT id, user_id, granted_by_user_id, created_at "
            "FROM platform_admins"
        ).fetchone()
        assert row is not None
        assert row[0]
        assert row[1] == admin_user_id
        assert row[2] is None
        assert str(row[3]).startswith("2026-10-27")

        try:
            connection.execute(
                "INSERT INTO platform_admins "
                "(id, user_id, granted_by_user_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("duplicate-id", admin_user_id, None, created_at),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
        else:
            raise AssertionError("user_id must remain unique.")
    finally:
        connection.close()

    downgrade_result = _migration_command(database_url, "downgrade", "07780722d2de")
    assert downgrade_result.returncode == 0, downgrade_result.stderr

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "disease_risk_assessments" not in tables
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(platform_admins)")
        }
        assert columns == {"user_id", "granted_by_user_id", "created_at"}
        assert connection.execute(
            "SELECT user_id FROM platform_admins"
        ).fetchall() == [(admin_user_id,)]
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("07780722d2de",)
    finally:
        connection.close()

    upgrade_result = _migration_command(database_url, "upgrade")
    assert upgrade_result.returncode == 0, upgrade_result.stderr
    check_result = _migration_command(database_url, "check")
    assert check_result.returncode == 0, check_result.stderr
