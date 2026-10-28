from sqlalchemy import func, select

from livestock_app import db, demo_seed
from livestock_app.demo_seed import (
    DEMO_DATABASE_ATTESTATION,
    DEMO_DATASET_MARKER,
    DEMO_OWNER_EMAIL,
    DemoSeedError,
    seed_synthetic_demo,
)
from livestock_app.models import (
    Alert,
    Animal,
    AuditLog,
    Barn,
    Farm,
    FeedInventoryBalance,
    FeedItem,
    FeedTransaction,
    Herd,
    HealthRecord,
    Species,
    SyntheticDataset,
    User,
    VaccinationOutcome,
    VaccinationSchedule,
)


_SEEDED_MODELS = (
    Barn,
    Herd,
    Animal,
    FeedItem,
    FeedInventoryBalance,
    FeedTransaction,
    VaccinationSchedule,
    VaccinationOutcome,
    HealthRecord,
    Alert,
    AuditLog,
)


def _seed(app, *, owner_password="synthetic-demo-password"):
    return seed_synthetic_demo(
        db.session,
        app_env="test",
        testing=True,
        database_url="sqlite+pysqlite:///:memory:",
        owner_password=owner_password,
    )


def test_seed_creates_only_marked_synthetic_rows_and_is_idempotent(app):
    with app.app_context():
        db.create_all()
        created = _seed(app)
        assert created.created is True
        assert created.dataset_marker == DEMO_DATASET_MARKER

        dataset = db.session.scalar(
            select(SyntheticDataset).where(
                SyntheticDataset.marker == DEMO_DATASET_MARKER
            )
        )
        assert dataset is not None
        for model in _SEEDED_MODELS:
            rows = db.session.scalars(
                select(model).where(
                    model.farm_id == dataset.farm_id,
                    model.synthetic_dataset_id == dataset.id,
                )
            ).all()
            assert rows
            assert all(row.synthetic_dataset_id == dataset.id for row in rows)

        counts_before = {
            model.__tablename__: db.session.scalar(
                select(func.count(model.id)).where(
                    model.farm_id == dataset.farm_id,
                    model.synthetic_dataset_id == dataset.id,
                )
            )
            for model in _SEEDED_MODELS
        }
        no_op = _seed(app, owner_password=None)
        counts_after = {
            model.__tablename__: db.session.scalar(
                select(func.count(model.id)).where(
                    model.farm_id == dataset.farm_id,
                    model.synthetic_dataset_id == dataset.id,
                )
            )
            for model in _SEEDED_MODELS
        }
        assert no_op.created is False
        assert counts_after == counts_before
        assert db.session.scalar(
            select(func.count(User.id)).where(User.email == DEMO_OWNER_EMAIL)
        ) == 1
        db.drop_all()


def test_seed_refuses_existing_business_data_without_mutation(app):
    with app.app_context():
        db.create_all()
        existing_farm = Farm(name="Existing synthetic test farm")
        db.session.add(existing_farm)
        db.session.commit()

        try:
            _seed(app)
        except DemoSeedError as exc:
            assert "đã có dữ liệu nghiệp vụ" in str(exc)
        else:
            raise AssertionError("Seed must refuse a database with existing farm data.")

        assert db.session.scalar(select(Farm.name)) == "Existing synthetic test farm"
        assert db.session.scalar(
            select(func.count(SyntheticDataset.id)).where(
                SyntheticDataset.marker == DEMO_DATASET_MARKER
            )
        ) == 0
        assert db.session.scalar(
            select(func.count(User.id)).where(User.email == DEMO_OWNER_EMAIL)
        ) == 0
        db.drop_all()


def test_seed_refuses_non_test_mode_and_unverified_demo_target_without_connection(app):
    with app.app_context():
        db.create_all()
        try:
            seed_synthetic_demo(
                db.session,
                app_env="production",
                testing=False,
                database_url="mysql+pymysql://user:password@host/livestock_demo_a",
                agent_mode="mock",
                owner_password="synthetic-demo-password",
            )
        except DemoSeedError as exc:
            assert "APP_ENV=demo" in str(exc)
        else:
            raise AssertionError("Production must never enter demo seed flow.")

        try:
            seed_synthetic_demo(
                db.session,
                app_env="demo",
                testing=False,
                database_url="mysql+pymysql://user:password@host/livestock_demo_a",
                configured_database_name="livestock_demo_a",
                confirmed_database_name="livestock_demo_a",
                expected_server_uuid="expected-server",
                observed_attestation="",
                agent_mode="mock",
                owner_password="synthetic-demo-password",
            )
        except DemoSeedError as exc:
            assert "xác nhận database riêng" in str(exc)
        else:
            raise AssertionError("Unverified database must be refused.")

        try:
            seed_synthetic_demo(
                db.session,
                app_env="demo",
                testing=False,
                database_url="mysql+pymysql://user:password@host/livestock_demo_a",
                configured_database_name="livestock_demo_b",
                confirmed_database_name="livestock_demo_a",
                expected_server_uuid="expected-server",
                observed_attestation=DEMO_DATABASE_ATTESTATION,
                agent_mode="mock",
                owner_password="synthetic-demo-password",
            )
        except DemoSeedError as exc:
            assert "không khớp" in str(exc)
        else:
            raise AssertionError("Mismatched configured DB name must be refused.")

        assert db.session.scalar(select(func.count(Farm.id))) == 0
        assert db.session.scalar(select(func.count(Species.id))) == 0
        db.drop_all()


def test_seed_requires_explicit_test_database_identity(app):
    with app.app_context():
        db.create_all()
        try:
            seed_synthetic_demo(
                db.session,
                app_env="test",
                testing=False,
                database_url="sqlite+pysqlite:///:memory:",
                owner_password="synthetic-demo-password",
            )
        except DemoSeedError as exc:
            assert "TESTING=True" in str(exc)
        else:
            raise AssertionError("Test seed must require isolated test mode.")
        db.drop_all()


def test_seed_refuses_missing_password_without_creating_records(app):
    with app.app_context():
        db.create_all()
        try:
            _seed(app, owner_password=None)
        except DemoSeedError as exc:
            assert "ít nhất 12 ký tự" in str(exc)
        else:
            raise AssertionError("Seed must require an operator-provided password.")
        assert db.session.scalar(select(func.count(Farm.id))) == 0
        assert db.session.scalar(select(func.count(User.id))) == 0
        db.drop_all()


def test_seed_refuses_mismatched_mysql_server_uuid_without_business_queries():
    class IdentitySession:
        def execute(self, _statement):
            return self

        def one(self):
            return "livestock_demo_l5", "unexpected-server"

        def scalar(self, *_args, **_kwargs):
            raise AssertionError("Business query ran before UUID verification.")

    try:
        seed_synthetic_demo(
            IdentitySession(),
            app_env="demo",
            testing=False,
            database_url="mysql+pymysql://demo:synthetic@db/livestock_demo_l5",
            configured_database_name="livestock_demo_l5",
            confirmed_database_name="livestock_demo_l5",
            expected_server_uuid="expected-server",
            observed_attestation=DEMO_DATABASE_ATTESTATION,
            owner_password="synthetic-demo-password",
            agent_mode="mock",
        )
    except DemoSeedError as exc:
        assert "không khớp" in str(exc)
    else:
        raise AssertionError("Seed must refuse an unrecognized MySQL server.")


def test_seed_rolls_back_everything_when_dataset_validation_fails(app, monkeypatch):
    with app.app_context():
        db.create_all()
        monkeypatch.setattr(demo_seed, "_counts_for_dataset", lambda *_args: {})
        try:
            _seed(app)
        except DemoSeedError as exc:
            assert "transaction bị hủy" in str(exc)
        else:
            raise AssertionError("Invalid generated dataset must roll back.")

        assert db.session.scalar(select(func.count(Farm.id))) == 0
        assert db.session.scalar(select(func.count(User.id))) == 0
        assert db.session.scalar(select(func.count(SyntheticDataset.id))) == 0
        assert db.session.scalar(select(func.count(Species.id))) == 0
        db.drop_all()


def test_seed_cli_checks_operator_attestation_before_querying_database(
    app, monkeypatch
):
    app.config.update(
        APP_ENV="demo",
        AGENT_MODE="mock",
        DEMO_DATABASE_ATTESTATION="",
    )

    def forbidden_action(*_args, **_kwargs):
        raise AssertionError("CLI queried the database before validating its target.")

    monkeypatch.setattr(db.session, "execute", forbidden_action)
    monkeypatch.setattr("click.prompt", forbidden_action)
    result = app.test_cli_runner().invoke(
        args=["seed-demo", "--confirm-database-name", "livestock_demo_test"]
    )
    assert result.exit_code != 0
    assert "Thiếu xác nhận operator" in result.output


def _configure_demo_seed_cli(app, *, expected_uuid="expected-server"):
    app.config.update(
        APP_ENV="demo",
        AGENT_MODE="mock",
        SQLALCHEMY_DATABASE_URI="mysql+pymysql://demo:test@db/livestock_demo_test",
        DEMO_DATABASE_NAME="livestock_demo_test",
        DEMO_DATABASE_SERVER_UUID=expected_uuid,
        DEMO_DATABASE_ATTESTATION=DEMO_DATABASE_ATTESTATION,
    )


def test_seed_cli_refuses_missing_server_uuid_before_password_prompt(app, monkeypatch):
    _configure_demo_seed_cli(app, expected_uuid="")

    def forbidden_action(*_args, **_kwargs):
        raise AssertionError("Prompt or database query ran without a server UUID.")

    monkeypatch.setattr(db.session, "execute", forbidden_action)
    monkeypatch.setattr("click.prompt", forbidden_action)

    result = app.test_cli_runner().invoke(
        args=["seed-demo", "--confirm-database-name", "livestock_demo_test"]
    )

    assert result.exit_code != 0
    assert "Cần cấu hình UUID MySQL" in result.output


def test_seed_cli_refuses_database_name_mismatch_before_password_prompt(
    app, monkeypatch
):
    _configure_demo_seed_cli(app)

    def forbidden_action(*_args, **_kwargs):
        raise AssertionError("Prompt or database query ran for a mismatched name.")

    monkeypatch.setattr(db.session, "execute", forbidden_action)
    monkeypatch.setattr("click.prompt", forbidden_action)

    result = app.test_cli_runner().invoke(
        args=["seed-demo", "--confirm-database-name", "livestock_demo_other"]
    )

    assert result.exit_code != 0
    assert "không khớp" in result.output


def test_seed_cli_refuses_server_uuid_mismatch_before_password_prompt(
    app, monkeypatch
):
    _configure_demo_seed_cli(app)

    class IdentityResult:
        def one(self):
            return "livestock_demo_test", "unexpected-server"

    monkeypatch.setattr(db.session, "execute", lambda *_args, **_kwargs: IdentityResult())

    def forbidden_prompt(*_args, **_kwargs):
        raise AssertionError("Password prompt ran before server UUID verification.")

    monkeypatch.setattr("click.prompt", forbidden_prompt)

    result = app.test_cli_runner().invoke(
        args=["seed-demo", "--confirm-database-name", "livestock_demo_test"]
    )

    assert result.exit_code != 0
    assert "không khớp" in result.output


def test_seed_cli_valid_preflight_reaches_hidden_password_prompt_without_seeding(
    app, monkeypatch
):
    _configure_demo_seed_cli(app)

    class IdentityResult:
        def one(self):
            return "livestock_demo_test", "expected-server"

    class PromptReached(Exception):
        pass

    prompt_calls = []

    def stop_at_prompt(*_args, **_kwargs):
        prompt_calls.append(True)
        raise PromptReached

    def forbidden_seed(*_args, **_kwargs):
        raise AssertionError("Seed must not be called by this CLI ordering test.")

    monkeypatch.setattr(db.session, "execute", lambda *_args, **_kwargs: IdentityResult())
    monkeypatch.setattr("click.prompt", stop_at_prompt)
    monkeypatch.setattr(demo_seed, "seed_synthetic_demo", forbidden_seed)

    result = app.test_cli_runner().invoke(
        args=["seed-demo", "--confirm-database-name", "livestock_demo_test"]
    )

    assert isinstance(result.exception, PromptReached)
    assert prompt_calls == [True]
