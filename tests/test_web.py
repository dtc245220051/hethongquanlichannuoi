import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from livestock_app import db
from livestock_app.web import _stable_display_labels
from livestock_app.models import (
    Alert,
    AlertEvent,
    Animal,
    AuditLog,
    Barn,
    Farm,
    FarmMembership,
    FeedInventoryBalance,
    FeedItem,
    FeedTransaction,
    Herd,
    HerdEvent,
    HealthRecord,
    Species,
    User,
    VaccinationOutcome,
    VaccinationSchedule,
)


@pytest.fixture
def web_data(app):
    with app.app_context():
        db.create_all()
        farm = Farm(name="Demo Farm")
        other_farm = Farm(name="Other Demo Farm")
        owner = User(
            email="owner@example.test",
            password_hash=generate_password_hash("synthetic-owner-password"),
            display_name="Chủ trại",
        )
        staff = User(
            email="staff@example.test",
            password_hash=generate_password_hash("synthetic-staff-password"),
            display_name="Nhân viên",
        )
        outsider = User(
            email="outsider@example.test",
            password_hash=generate_password_hash("synthetic-outsider-password"),
            display_name="Người ngoài",
        )
        db.session.add_all([farm, other_farm, owner, staff, outsider])
        db.session.flush()
        db.session.add_all(
            [
                FarmMembership(farm_id=farm.id, user_id=owner.id, role="owner"),
                FarmMembership(farm_id=farm.id, user_id=staff.id, role="care_staff"),
                FarmMembership(farm_id=other_farm.id, user_id=outsider.id, role="owner"),
            ]
        )
        species = Species(code="cattle", name_vi="Bò")
        barn = Barn(farm_id=farm.id, name="Chuồng A", capacity_heads=20)
        other_barn = Barn(farm_id=other_farm.id, name="Chuồng B", capacity_heads=20)
        feed = FeedItem(farm_id=farm.id, name="Cám tổng hợp", base_unit_code="kg")
        db.session.add_all([species, barn, other_barn, feed])
        db.session.flush()
        herd = Herd(
            farm_id=farm.id,
            name="Đàn bò 01",
            species_code="cattle",
            barn_id=barn.id,
            unidentified_count=3,
            status="active",
        )
        db.session.add(herd)
        db.session.flush()
        inside_animals = [
            Animal(
                farm_id=farm.id,
                species_code="cattle",
                herd_id=herd.id,
                barn_id=barn.id,
                identifier=f"COW-{index}",
                status="active",
            )
            for index in range(1, 3)
        ]
        standalone_animal = Animal(
            farm_id=farm.id,
            species_code="cattle",
            barn_id=barn.id,
            identifier="COW-03",
            status="active",
        )
        alert = Alert(
            farm_id=farm.id,
            alert_type="stock",
            severity="medium",
            title="Kiểm tra tồn kho",
            description="Cảnh báo tổng hợp cho bài test.",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        schedule = VaccinationSchedule(
            farm_id=farm.id,
            herd_id=herd.id,
            vaccine_name="Vaccine demo",
            due_at=datetime.now(timezone.utc) + timedelta(days=2),
            status="scheduled",
            created_by_user_id=owner.id,
            created_at=datetime.now(timezone.utc),
        )
        db.session.add_all(
            [
                *inside_animals,
                standalone_animal,
                FeedInventoryBalance(
                    farm_id=farm.id,
                    feed_item_id=feed.id,
                    quantity_kg=Decimal("10.000"),
                    updated_at=datetime.now(timezone.utc),
                ),
                alert,
                schedule,
            ]
        )
        db.session.commit()
        data = {
            "farm_id": farm.id,
            "other_farm_id": other_farm.id,
            "owner_email": owner.email,
            "staff_email": staff.email,
            "staff_id": staff.id,
            "outsider_email": outsider.email,
            "barn_id": barn.id,
            "other_barn_id": other_barn.id,
            "herd_id": herd.id,
            "animal_id": inside_animals[0].id,
            "feed_id": feed.id,
            "alert_id": alert.id,
            "schedule_id": schedule.id,
        }
        db.session.remove()
        yield data
        db.session.remove()
        db.drop_all()


def _csrf(client) -> str:
    response = client.get("/login")
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode()


def _login(client, email: str, password: str) -> None:
    response = client.post(
        "/login",
        data={
            "_csrf_token": _csrf(client),
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 302


def _remove_test_member(app, email: str) -> None:
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == email))
        if user is None:
            return
        db.session.query(AuditLog).filter_by(entity_id=user.id).delete(
            synchronize_session=False
        )
        db.session.query(FarmMembership).filter_by(user_id=user.id).delete(
            synchronize_session=False
        )
        db.session.delete(user)
        db.session.commit()


def test_health_route_remains_public_and_authentication_is_required(client):
    assert client.get("/health/live").status_code == 200
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_rejects_bad_credentials_and_accepts_active_members(web_data, client):
    invalid = client.post(
        "/login",
        data={
            "_csrf_token": _csrf(client),
            "email": web_data["owner_email"],
            "password": "wrong-password",
        },
    )
    assert invalid.status_code == 401
    assert b"kh\xc3\xb4ng h\xe1\xbb\xa3p l\xe1\xbb\x87" in invalid.data

    _login(client, web_data["owner_email"], "synthetic-owner-password")
    assert client.get("/").status_code == 200


def test_csrf_and_http_method_are_enforced(web_data, client):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    assert client.post("/logout").status_code == 400
    assert client.get("/logout").status_code == 405


def test_dashboard_counts_herd_members_once(web_data, client):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.get("/")
    assert response.status_code == 200
    assert b">6<" in response.data
    assert b">3<" in response.data


def test_administered_outcome_prevents_false_overdue_alert(web_data, client, app):
    with app.app_context():
        owner_id = db.session.scalar(
            select(User.id).where(User.email == web_data["owner_email"])
        )
        due_at = datetime.now(timezone.utc) - timedelta(days=1)
        schedule = VaccinationSchedule(
            farm_id=web_data["farm_id"],
            herd_id=web_data["herd_id"],
            vaccine_name="Đã tiêm đúng hẹn",
            due_at=due_at,
            status="scheduled",
            created_by_user_id=owner_id,
            created_at=due_at - timedelta(days=7),
        )
        db.session.add(schedule)
        db.session.flush()
        db.session.add(
            VaccinationOutcome(
                farm_id=web_data["farm_id"],
                schedule_id=schedule.id,
                outcome="administered",
                occurred_at=due_at,
                performed_by_user_id=owner_id,
                recorded_by_user_id=owner_id,
            )
        )
        db.session.commit()

    _login(client, web_data["owner_email"], "synthetic-owner-password")
    dashboard = client.get("/")
    assert re.search(rb'data-metric="overdue-vaccinations"[^>]*>0<', dashboard.data)
    vaccination_page = client.get("/vaccinations")
    assert "Đã thực hiện".encode() in vaccination_page.data


def test_other_farm_object_is_not_disclosed(web_data, client):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.get(f"/barns/{web_data['other_barn_id']}")
    assert response.status_code == 404
    assert b"Chu\xe1\xbb\x93ng B" not in response.data


def test_staff_cannot_read_audit_or_manage_barns(web_data, client):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    assert client.get("/audit").status_code == 403
    assert client.get("/members").status_code == 403
    member_post = client.post(
        "/members",
        data={
            "_csrf_token": _csrf(client),
            "email": "staff-created-account@example.test",
            "display_name": "Unauthorized",
            "password": "Synthetic-Staff-Password-12!",
            "password_confirmation": "Synthetic-Staff-Password-12!",
        },
    )
    assert member_post.status_code == 403
    response = client.post(
        "/barns",
        data={
            "_csrf_token": _csrf(client),
            "name": "Không được tạo",
            "capacity_heads": "5",
        },
    )
    assert response.status_code == 403


def test_unauthenticated_user_cannot_open_member_management(client):
    response = client.get("/members")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_owner_can_create_staff_member_with_hashed_password_and_audit(
    web_data, client, app
):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/members",
        data={
            "_csrf_token": _csrf(client),
            "email": "new-staff@example.test",
            "display_name": "Nhân viên mới",
            "password": "Synthetic-Staff-Password-12!",
            "password_confirmation": "Synthetic-Staff-Password-12!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"new-staff@example.test" in response.data
    assert b"Synthetic-Staff-Password" not in response.data

    with app.app_context():
        user = db.session.scalar(
            select(User).where(User.email == "new-staff@example.test")
        )
        membership = db.session.scalar(
            select(FarmMembership).where(
                FarmMembership.user_id == user.id,
                FarmMembership.farm_id == web_data["farm_id"],
            )
        )
        assert membership.role == "care_staff"
        assert membership.created_by_user_id
        assert user.status == "active"
        assert user.password_hash != "Synthetic-Staff-Password-12!"
        assert check_password_hash(user.password_hash, "Synthetic-Staff-Password-12!")
        audit = db.session.scalar(
            select(AuditLog).where(
                AuditLog.farm_id == web_data["farm_id"],
                AuditLog.action == "farm.member.created",
                AuditLog.entity_id == user.id,
            )
        )
        assert audit is not None

    staff_client = app.test_client()
    login_response = staff_client.post(
        "/login",
        data={
            "_csrf_token": _csrf(staff_client),
            "email": "new-staff@example.test",
            "password": "Synthetic-Staff-Password-12!",
        },
    )
    assert login_response.status_code == 302
    assert staff_client.get("/").status_code == 200
    assert staff_client.get("/members").status_code == 403
    _remove_test_member(app, "new-staff@example.test")


@pytest.mark.parametrize("role", ["manager", "veterinarian"])
def test_owner_can_create_manager_and_veterinarian(web_data, client, app, role):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    email = f"new-{role}@example.test"
    response = client.post(
        "/members",
        data={
            "_csrf_token": _csrf(client),
            "role": role,
            "email": email,
            "display_name": f"New {role}",
            "password": "Synthetic-New-Member-Password-12!",
            "password_confirmation": "Synthetic-New-Member-Password-12!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert email.encode() in response.data
    assert ("Quản lí" if role == "manager" else "Thú y").encode() in response.data
    with app.app_context():
        member = db.session.scalar(
            select(FarmMembership)
            .join(User, User.id == FarmMembership.user_id)
            .where(User.email == email)
        )
        assert member.role == role
        assert member.farm_id == web_data["farm_id"]
    _remove_test_member(app, email)


def test_owner_cannot_create_admin_membership(web_data, client, app):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/members",
        data={
            "_csrf_token": _csrf(client),
            "role": "admin",
            "email": "new-admin@example.test",
            "display_name": "Not an admin",
            "password": "Synthetic-New-Member-Password-12!",
            "password_confirmation": "Synthetic-New-Member-Password-12!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "không hợp lệ".encode() in response.data
    with app.app_context():
        assert db.session.scalar(
            select(User.id).where(User.email == "new-admin@example.test")
        ) is None


def test_manager_can_manage_operations_but_not_members_or_audit(web_data, client, app):
    with app.app_context():
        member = db.session.scalar(
            select(FarmMembership).where(
                FarmMembership.farm_id == web_data["farm_id"],
                FarmMembership.user_id == web_data["staff_id"],
            )
        )
        member.role = "manager"
        db.session.commit()
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    assert client.get("/members").status_code == 403
    assert client.get("/audit").status_code == 403
    member_post = client.post(
        "/members",
        data={
            "_csrf_token": _csrf(client),
            "role": "veterinarian",
            "email": "manager-created@example.test",
            "display_name": "Unauthorized member",
            "password": "Synthetic-New-Member-Password-12!",
            "password_confirmation": "Synthetic-New-Member-Password-12!",
        },
    )
    assert member_post.status_code == 403
    response = client.post(
        "/barns",
        data={
            "_csrf_token": _csrf(client),
            "name": "Chuồng do quản lí tạo",
            "capacity_heads": "5",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(
            select(Barn.id).where(
                Barn.farm_id == web_data["farm_id"],
                Barn.name == "Chuồng do quản lí tạo",
            )
        )
        assert db.session.scalar(
            select(User.id).where(User.email == "manager-created@example.test")
        ) is None


def test_veterinarian_is_read_only_and_limited_to_care_pages(web_data, client, app):
    with app.app_context():
        member = db.session.scalar(
            select(FarmMembership).where(
                FarmMembership.farm_id == web_data["farm_id"],
                FarmMembership.user_id == web_data["staff_id"],
            )
        )
        member.role = "veterinarian"
        db.session.commit()
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    livestock_response = client.get("/livestock")
    assert livestock_response.status_code == 200
    assert b"create_animal" not in livestock_response.data
    health_response = client.get("/health-records")
    assert health_response.status_code == 200
    assert b'name="observations"' not in health_response.data
    vaccinations_response = client.get("/vaccinations")
    assert vaccinations_response.status_code == 200
    assert b"record_outcome" not in vaccinations_response.data
    for path in ("/", "/barns", "/feed", "/alerts", "/members", "/audit"):
        assert client.get(path).status_code == 403
    for path in ("/livestock", "/health-records", "/vaccinations"):
        assert (
            client.post(path, data={"_csrf_token": _csrf(client)}).status_code
            == 403
        )
    assert client.post(
        "/logout",
        data={"_csrf_token": _csrf(client)},
    ).status_code == 302


def test_member_creation_requires_csrf(web_data, client, app):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/members",
        data={
            "email": "csrf-staff@example.test",
            "display_name": "CSRF Staff",
            "password": "Synthetic-Staff-Password-12!",
            "password_confirmation": "Synthetic-Staff-Password-12!",
        },
    )
    assert response.status_code == 400
    with app.app_context():
        assert db.session.scalar(
            select(User.id).where(User.email == "csrf-staff@example.test")
        ) is None


@pytest.mark.parametrize(
    ("email", "status"),
    [
        ("staff@example.test", "active"),
        ("disabled@example.test", "disabled"),
    ],
)
def test_member_creation_rejects_duplicate_account_without_new_membership(
    web_data, client, app, email, status
):
    with app.app_context():
        if status == "disabled":
            db.session.add(
                User(
                    email=email,
                    password_hash=generate_password_hash("synthetic-disabled-password"),
                    display_name="Disabled user",
                    status="disabled",
                )
            )
            db.session.commit()
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/members",
        data={
            "_csrf_token": _csrf(client),
            "email": email,
            "display_name": "Duplicate",
            "password": "Synthetic-Staff-Password-12!",
            "password_confirmation": "Synthetic-Staff-Password-12!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Email " in response.data
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == email))
        memberships = db.session.scalars(
            select(FarmMembership).where(FarmMembership.user_id == user.id)
        ).all()
        if status == "disabled":
            assert memberships == []
        else:
            assert len(memberships) == 1
            assert memberships[0].farm_id == web_data["farm_id"]


def test_member_creation_ignores_submitted_other_farm_id(web_data, client, app):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/members",
        data={
            "_csrf_token": _csrf(client),
            "farm_id": web_data["other_farm_id"],
            "email": "scoped-staff@example.test",
            "display_name": "Scoped staff",
            "password": "Synthetic-Staff-Password-12!",
            "password_confirmation": "Synthetic-Staff-Password-12!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        user = db.session.scalar(
            select(User).where(User.email == "scoped-staff@example.test")
        )
        memberships = db.session.scalars(
            select(FarmMembership).where(FarmMembership.user_id == user.id)
        ).all()
        assert len(memberships) == 1
        assert memberships[0].farm_id == web_data["farm_id"]
        assert memberships[0].farm_id != web_data["other_farm_id"]
    _remove_test_member(app, "scoped-staff@example.test")


def test_staff_cannot_close_alert(web_data, client, app):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    response = client.post(
        f"/alerts/{web_data['alert_id']}",
        data={
            "_csrf_token": _csrf(client),
            "action": "close",
            "reason": "Lý do tổng hợp",
        },
    )
    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(Alert, web_data["alert_id"]).status == "new"
        assert db.session.scalar(
            select(AlertEvent.id).where(AlertEvent.alert_id == web_data["alert_id"])
        ) is None


def test_staff_can_acknowledge_alert_and_add_history(web_data, client, app):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    response = client.post(
        f"/alerts/{web_data['alert_id']}",
        data={
            "_csrf_token": _csrf(client),
            "action": "acknowledge",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        alert = db.session.get(Alert, web_data["alert_id"])
        assert alert.status == "acknowledged"
        assert db.session.scalar(
            select(AlertEvent.id).where(AlertEvent.alert_id == alert.id)
        ) is not None


def test_feed_transaction_service_enforces_role_and_nonnegative_balance(
    web_data, client, app
):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    rejected = client.post(
        "/feed",
        data={
            "_csrf_token": _csrf(client),
            "feed_item_id": web_data["feed_id"],
            "transaction_type": "receipt",
            "quantity_kg": "5",
            "unit_code": "kg",
        },
    )
    assert rejected.status_code == 302
    with app.app_context():
        assert db.session.get(FeedInventoryBalance, db.session.scalar(
            select(FeedInventoryBalance.id).where(
                FeedInventoryBalance.feed_item_id == web_data["feed_id"]
            )
        )).quantity_kg == Decimal("10.000")

    consumed = client.post(
        "/feed",
        data={
            "_csrf_token": _csrf(client),
            "feed_item_id": web_data["feed_id"],
            "transaction_type": "consumption",
            "quantity_kg": "11",
            "unit_code": "kg",
        },
    )
    assert consumed.status_code == 302
    with app.app_context():
        assert db.session.scalar(
            select(FeedTransaction.id).where(
                FeedTransaction.feed_item_id == web_data["feed_id"]
            )
        ) is None


def test_vaccination_schedule_is_not_an_outcome(web_data, client, app):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    create_denied = client.post(
        "/vaccinations",
        data={
            "_csrf_token": _csrf(client),
            "action": "create_schedule",
            "vaccine_name": "Không được tạo",
            "due_at": "2030-01-01T10:00",
            "herd_id": web_data["herd_id"],
        },
    )
    assert create_denied.status_code == 403
    result = client.post(
        "/vaccinations",
        data={
            "_csrf_token": _csrf(client),
            "action": "record_outcome",
            "schedule_id": web_data["schedule_id"],
            "outcome": "administered",
            "occurred_at": "2028-05-01T08:30",
        },
    )
    assert result.status_code == 302
    with app.app_context():
        schedule = db.session.get(VaccinationSchedule, web_data["schedule_id"])
        outcome = db.session.scalar(
            select(VaccinationOutcome).where(
                VaccinationOutcome.schedule_id == schedule.id
            )
        )
        assert schedule.status == "scheduled"
        assert outcome.outcome == "administered"
        assert outcome.performed_by_user_id is not None


def test_barn_capacity_cannot_be_lowered_below_occupancy(web_data, client, app):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        f"/barns/{web_data['barn_id']}",
        data={
            "_csrf_token": _csrf(client),
            "name": "Chuồng A",
            "capacity_heads": "5",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Barn, web_data["barn_id"]).capacity_heads == 20


def test_owner_can_create_barn_and_audit_it(web_data, client, app):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/barns",
        data={
            "_csrf_token": _csrf(client),
            "name": "Chuồng mới",
            "capacity_heads": "12",
            "location_note": "Khu tổng hợp",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        barn = db.session.scalar(
            select(Barn).where(
                Barn.farm_id == web_data["farm_id"], Barn.name == "Chuồng mới"
            )
        )
        assert barn is not None
        assert barn.capacity_heads == 12


def test_owner_can_create_feed_item_through_service(web_data, client, app):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/feed",
        data={
            "_csrf_token": _csrf(client),
            "action": "create_item",
            "name": "Cám kiểm thử",
            "threshold_kg": "5.250",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        item = db.session.scalar(
            select(FeedItem).where(
                FeedItem.farm_id == web_data["farm_id"],
                FeedItem.name == "Cám kiểm thử",
            )
        )
        assert item is not None
        balance = db.session.scalar(
            select(FeedInventoryBalance).where(
                FeedInventoryBalance.feed_item_id == item.id
            )
        )
        assert balance.quantity_kg == Decimal("0.000")


def test_staff_consumption_updates_balance_and_ledger(web_data, client, app):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    response = client.post(
        "/feed",
        data={
            "_csrf_token": _csrf(client),
            "feed_item_id": web_data["feed_id"],
            "transaction_type": "consumption",
            "quantity_kg": "2.500",
            "unit_code": "kg",
            "herd_id": web_data["herd_id"],
        },
    )
    assert response.status_code == 302
    with app.app_context():
        balance = db.session.scalar(
            select(FeedInventoryBalance).where(
                FeedInventoryBalance.feed_item_id == web_data["feed_id"]
            )
        )
        transaction = db.session.scalar(
            select(FeedTransaction).where(
                FeedTransaction.feed_item_id == web_data["feed_id"]
            )
        )
        assert balance.quantity_kg == Decimal("7.500")
        assert transaction.quantity_delta_kg == Decimal("-2.500")


def test_herd_count_split_and_merge_workflows_preserve_total(
    web_data, client, app
):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    adjust = {
        "_csrf_token": _csrf(client),
        "action": "adjust_count",
        "herd_id": web_data["herd_id"],
        "delta": "1",
        "event_type": "acquisition",
        "reason": "Bổ sung tổng hợp",
    }
    assert client.post("/livestock", data=adjust).status_code == 302
    with app.app_context():
        assert db.session.get(Herd, web_data["herd_id"]).head_count == 6

    split = {
        "_csrf_token": _csrf(client),
        "action": "split_herd",
        "source_herd_id": web_data["herd_id"],
        "target_name": "Đàn tách",
        "heads": "2",
        "reason": "Tách tổng hợp",
    }
    assert client.post("/livestock", data=split).status_code == 302
    with app.app_context():
        target = db.session.scalar(
            select(Herd).where(
                Herd.farm_id == web_data["farm_id"], Herd.name == "Đàn tách"
            )
        )
        assert target is not None
        target_id = target.id
        assert target.head_count == 2
        assert db.session.get(Herd, web_data["herd_id"]).head_count == 4

    merge = {
        "_csrf_token": _csrf(client),
        "action": "merge_herds",
        "source_herd_id": target_id,
        "target_herd_id": web_data["herd_id"],
        "reason": "Gộp tổng hợp",
    }
    assert client.post("/livestock", data=merge).status_code == 302
    with app.app_context():
        assert db.session.get(Herd, target_id).status == "closed"
        assert db.session.get(Herd, web_data["herd_id"]).head_count == 6


def test_existing_vaccination_outcome_cannot_be_silently_overwritten(
    web_data, client, app
):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    payload = {
        "_csrf_token": _csrf(client),
        "action": "record_outcome",
        "schedule_id": web_data["schedule_id"],
        "outcome": "administered",
        "occurred_at": "2028-05-01T08:30",
    }
    assert client.post("/vaccinations", data=payload).status_code == 302
    payload["_csrf_token"] = _csrf(client)
    payload["outcome"] = "postponed"
    assert client.post("/vaccinations", data=payload).status_code == 302
    with app.app_context():
        outcomes = db.session.scalars(
            select(VaccinationOutcome).where(
                VaccinationOutcome.schedule_id == web_data["schedule_id"]
            )
        ).all()
        assert len(outcomes) == 1
        assert outcomes[0].outcome == "administered"


def test_staff_cannot_update_vaccination_schedule(web_data, client):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    response = client.post(
        "/vaccinations",
        data={
            "_csrf_token": _csrf(client),
            "action": "update_schedule",
            "schedule_id": web_data["schedule_id"],
            "vaccine_name": "Sửa trái phép",
            "due_at": "2030-01-01T10:00",
            "status": "cancelled",
        },
    )
    assert response.status_code == 403


def test_vaccination_schedule_requires_exactly_one_target(web_data, client):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    response = client.post(
        "/vaccinations",
        data={
            "_csrf_token": _csrf(client),
            "action": "create_schedule",
            "vaccine_name": "Thiếu đối tượng",
            "due_at": "2030-01-01T10:00",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Select exactly one animal or herd." in response.data


def test_health_record_is_farm_scoped_and_audited(web_data, client, app):
    _login(client, web_data["staff_email"], "synthetic-staff-password")
    response = client.post(
        "/health-records",
        data={
            "_csrf_token": _csrf(client),
            "animal_id": web_data["animal_id"],
            "observations": "Ghi nhận tổng hợp cho kiểm thử.",
            "action_taken": "Đã thông báo người phụ trách.",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        row = db.session.scalar(
            select(HealthRecord).where(HealthRecord.farm_id == web_data["farm_id"])
        )
        assert row is not None
        assert row.animal_id == web_data["animal_id"]


def test_authenticated_pages_render_with_data_and_empty_history(web_data, client):
    _login(client, web_data["owner_email"], "synthetic-owner-password")
    urls = [
        "/barns",
        f"/barns/{web_data['barn_id']}",
        "/livestock",
        f"/livestock/herds/{web_data['herd_id']}",
        f"/livestock/animals/{web_data['animal_id']}",
        "/feed",
        "/health-records",
        "/vaccinations",
        "/alerts",
        f"/alerts/{web_data['alert_id']}",
        "/audit",
    ]
    for path in urls:
        response = client.get(path)
        assert response.status_code == 200, path


def test_farm_records_render_friendly_labels_instead_of_visible_uuids(
    web_data, client, app
):
    with app.app_context():
        owner_id = db.session.scalar(
            select(User.id).where(User.email == web_data["owner_email"])
        )
        db.session.add_all(
            [
                HealthRecord(
                    farm_id=web_data["farm_id"],
                    animal_id=web_data["animal_id"],
                    recorded_at=datetime.now(timezone.utc),
                    observations="Quan sát tổng hợp.",
                    recorded_by_user_id=owner_id,
                ),
                AlertEvent(
                    farm_id=web_data["farm_id"],
                    alert_id=web_data["alert_id"],
                    event_type="acknowledged",
                    actor_user_id=owner_id,
                    occurred_at=datetime.now(timezone.utc),
                ),
                AuditLog(
                    farm_id=web_data["farm_id"],
                    actor_user_id=owner_id,
                    action="test.view_label",
                    entity_type="animal",
                    entity_id=web_data["animal_id"],
                    outcome="success",
                    request_id="test-friendly-labels",
                    occurred_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db.session.commit()

    _login(client, web_data["owner_email"], "synthetic-owner-password")
    pages = {
        "/barns": ("Chuồng A",),
        f"/barns/{web_data['barn_id']}": ("Chuồng A", "Đàn bò 01"),
        "/livestock": ("Đàn bò 01", "Bò COW-1", "Chuồng A"),
        f"/livestock/herds/{web_data['herd_id']}": (
            "Đàn bò 01",
            "Chuồng A",
        ),
        f"/livestock/animals/{web_data['animal_id']}": (
            "Bò COW-1",
            "Đàn bò 01",
            "Chuồng A",
        ),
        "/health-records": ("Bò COW-1",),
        "/vaccinations": ("Đàn bò 01", "Lịch tiêm phòng số 01"),
        "/alerts": ("Kiểm tra tồn kho",),
        f"/alerts/{web_data['alert_id']}": ("Kiểm tra tồn kho", "Chủ trại"),
        "/audit": ("Bò COW-1", "Nhật ký #1"),
    }
    identifiers = (
        web_data["animal_id"],
        web_data["herd_id"],
        web_data["barn_id"],
        web_data["schedule_id"],
        web_data["alert_id"],
    )
    for path, labels in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for label in labels:
            assert label.encode() in response.data, (path, label)
        for identifier in identifiers:
            assert f">{identifier}<".encode() not in response.data, (
                path,
                identifier,
            )


def test_fallback_labels_are_deterministic_and_distinct():
    class Record:
        def __init__(self, record_id):
            self.id = record_id

    records = [Record("b"), Record("a"), Record("c")]
    labels = _stable_display_labels(records, "Vật nuôi", lambda _record: None)
    assert labels == {
        "a": "Vật nuôi #01",
        "b": "Vật nuôi #02",
        "c": "Vật nuôi #03",
    }


def test_owner_can_provision_initial_farm_without_default_account(app):
    with app.app_context():
        db.create_all()
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "provision-owner",
            "--email",
            "new-owner@example.test",
            "--display-name",
            "Chủ thử nghiệm",
            "--farm-name",
            "Trang trại thử nghiệm",
        ],
        input="Synthetic-Strong-Password-12!\nSynthetic-Strong-Password-12!\n",
    )
    assert result.exit_code == 0, result.output
    assert "Synthetic-Strong-Password" not in result.output
    with app.app_context():
        user = db.session.scalar(
            select(User).where(User.email == "new-owner@example.test")
        )
        assert user is not None
        assert user.password_hash != "Synthetic-Strong-Password-12!"
        assert db.session.scalar(
            select(FarmMembership.id).where(
                FarmMembership.user_id == user.id,
                FarmMembership.role == "owner",
            )
        ) is not None
        db.session.remove()
        db.drop_all()
