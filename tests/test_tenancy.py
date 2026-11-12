from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from relay.models import (
    Invite,
    Membership,
    Signal,
    SignalSource,
    TelegramAccount,
    TelegramEntity,
    Tenant,
)
from relay.services.signals import capture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_redis")]

REGISTER = "/api/v1/auth/register"


@pytest.fixture
def source(account):
    entity = TelegramEntity.objects.create(
        user=account.user,
        account=account,
        kind=TelegramEntity.Kind.SOURCE,
        peer_id=-1001234567890,
        entity_type="channel",
        username="signals",
        title="Signals Group",
    )
    return SignalSource.objects.create(
        account=account, entity=entity, name="Signals Group", enabled=True
    )


def register(client, email, password="a-strong-password-123", **extra):
    return client.post(REGISTER, {"email": email, "password": password, **extra}, format="json")


def test_register_creates_a_workspace_with_the_user_as_owner(client):
    response = register(client, "founder@example.com", tenant_name="Acme Signals")

    assert response.status_code == 201
    user = get_user_model().objects.get(email="founder@example.com")
    membership = Membership.objects.select_related("tenant").get(user=user)
    assert membership.role == Membership.Role.OWNER
    assert membership.tenant.name == "Acme Signals"
    assert membership.tenant.slug == "acme-signals"

    payload = response.data["data"]["user"]
    assert payload["role"] == "owner"
    assert payload["tenant"]["name"] == "Acme Signals"
    assert payload["is_superuser"] is False


def test_register_without_a_name_uses_a_personal_workspace(client):
    response = register(client, "solo@example.com")

    assert response.status_code == 201
    tenant = Tenant.objects.get(memberships__user__email="solo@example.com")
    assert tenant.name == "solo@example.com's workspace"
    assert tenant.slug
    assert " " not in tenant.slug


def test_register_rejects_duplicate_email(client):
    register(client, "taken@example.com")
    response = register(client, "taken@example.com")
    assert response.status_code == 400


def test_me_reports_the_workspace_and_role(client, user):
    client.force_authenticate(user)
    response = client.get("/api/v1/auth/me")
    payload = response.data["data"]
    assert payload["role"] == "owner"
    assert payload["tenant"]["id"] == str(user.membership.tenant_id)


def test_owner_can_invite_and_someone_can_join(client, user):
    client.force_authenticate(user)
    created = client.post(
        "/api/v1/tenant/invites/",
        {"role": "member", "email": "friend@example.com"},
        format="json",
    )
    assert created.status_code == 201
    code = created.data["data"]["code"]

    preview = client.get(f"/api/v1/auth/invites/{code}")
    assert preview.status_code == 200
    assert preview.data["data"]["tenant_name"] == "Test Workspace"

    joined = register(
        client, "friend@example.com", password="another-password-123", invite_code=code
    )
    assert joined.status_code == 201
    membership = Membership.objects.get(user__email="friend@example.com")
    assert membership.tenant_id == user.membership.tenant_id
    assert membership.role == Membership.Role.MEMBER

    invite = Invite.objects.get(code=code)
    assert invite.used_at is not None
    assert invite.used_by.email == "friend@example.com"

    reuse = register(client, "third@example.com", password="another-password-123", invite_code=code)
    assert reuse.status_code == 400


def test_invite_preview_rejects_used_and_expired_codes(client, user):
    client.force_authenticate(user)
    code = client.post("/api/v1/tenant/invites/", {"role": "member"}, format="json").data["data"][
        "code"
    ]
    Invite.objects.filter(code=code).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert client.get(f"/api/v1/auth/invites/{code}").status_code == 400
    assert client.get("/api/v1/auth/invites/does-not-exist").status_code == 400


def test_invite_can_be_locked_to_an_email(client, user):
    client.force_authenticate(user)
    code = client.post(
        "/api/v1/tenant/invites/",
        {"role": "member", "email": "expected@example.com"},
        format="json",
    ).data["data"]["code"]
    response = register(
        client, "other@example.com", password="another-password-123", invite_code=code
    )
    assert response.status_code == 400


def test_owner_creates_a_member_who_can_log_in(client, user):
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/tenant/members/",
        {"email": "staff@example.com", "password": "a-strong-password-123", "role": "member"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["data"]["role"] == "member"

    login = client.post(
        "/api/v1/auth/login",
        {"email": "staff@example.com", "password": "a-strong-password-123"},
        format="json",
    )
    assert login.status_code == 200
    assert login.data["data"]["user"]["tenant"]["id"] == str(user.membership.tenant_id)


def test_admins_cannot_create_admins(client, user):
    client.force_authenticate(user)
    staff = client.post(
        "/api/v1/tenant/members/",
        {"email": "admin@example.com", "password": "a-strong-password-123", "role": "admin"},
        format="json",
    )
    assert staff.status_code == 201
    admin = get_user_model().objects.get(email="admin@example.com")

    client.force_authenticate(admin)
    forbidden = client.post(
        "/api/v1/tenant/members/",
        {"email": "another@example.com", "password": "a-strong-password-123", "role": "admin"},
        format="json",
    )
    assert forbidden.status_code == 400
    allowed = client.post(
        "/api/v1/tenant/members/",
        {"email": "another@example.com", "password": "a-strong-password-123", "role": "member"},
        format="json",
    )
    assert allowed.status_code == 201


def test_plain_members_cannot_manage_the_workspace(client, regular_user):
    client.force_authenticate(regular_user)
    assert client.get("/api/v1/tenant/members/").status_code == 403
    assert client.get("/api/v1/tenant/invites/").status_code == 403
    assert client.post("/api/v1/tenant/invites/", {}, format="json").status_code == 403


def test_owner_cannot_remove_or_demote_themselves(client, user):
    client.force_authenticate(user)
    assert client.delete(f"/api/v1/tenant/members/{user.id}/").status_code == 400
    assert (
        client.patch(
            f"/api/v1/tenant/members/{user.id}/", {"role": "member"}, format="json"
        ).status_code
        == 400
    )


def test_owner_can_rename_the_workspace_but_members_cannot(client, user, regular_user):
    client.force_authenticate(regular_user)
    assert (
        client.patch("/api/v1/tenant/rename/", {"name": "Nope"}, format="json").status_code == 403
    )

    client.force_authenticate(user)
    response = client.patch("/api/v1/tenant/rename/", {"name": "Renamed"}, format="json")
    assert response.status_code == 200
    assert response.data["data"]["name"] == "Renamed"


def test_signals_are_isolated_between_workspaces(
    account, source, tenant, regular_user, client, telegram_env
):
    capture(account, source, chat_id=source.entity.peer_id, message_id=1, text="tenant A tip")

    other = Tenant.objects.create(name="Other", slug="other")
    outsider = get_user_model().objects.create_user(
        username="outsider@example.com", email="outsider@example.com", password="x"
    )
    Membership.objects.create(user=outsider, tenant=other, role=Membership.Role.MEMBER)

    client.force_authenticate(regular_user)
    assert client.get("/api/v1/signals/").data["data"]["count"] == 1

    client.force_authenticate(outsider)
    response = client.get("/api/v1/signals/")
    assert response.status_code == 200
    assert response.data["data"]["count"] == 0
    assert Signal.objects.count() == 1


def test_workspace_stats_only_count_their_sources(account, source, user, client, telegram_env):
    other = Tenant.objects.create(name="Other", slug="other")
    other_user = get_user_model().objects.create_user(
        username="o@example.com", email="o@example.com", password="x"
    )
    Membership.objects.create(user=other_user, tenant=other, role=Membership.Role.OWNER)
    other_account = TelegramAccount.objects.create(
        user=other_user, tenant=other, phone_number="+15550001111"
    )
    entity = TelegramEntity.objects.create(
        user=other_user,
        account=other_account,
        kind="source",
        peer_id=-100777,
        entity_type="channel",
    )
    SignalSource.objects.create(account=other_account, entity=entity, name="Other feed")

    client.force_authenticate(user)
    data = client.get("/api/v1/signals/stats/").data["data"]
    assert data["total"] == 0
    assert data["active_sources"] == 1


def test_removing_a_member_revokes_access_and_can_be_undone(client, user):
    client.force_authenticate(user)
    created = client.post(
        "/api/v1/tenant/members/",
        {"email": "temp@example.com", "password": "a-strong-password-123", "role": "member"},
        format="json",
    )
    member_id = created.data["data"]["id"]

    removed = client.delete(f"/api/v1/tenant/members/{member_id}/")
    assert removed.status_code == 200
    temp = get_user_model().objects.get(email="temp@example.com")
    assert temp.is_active is False
    assert not Membership.objects.filter(user=temp).exists()

    logged_out = client.post(
        "/api/v1/auth/login",
        {"email": "temp@example.com", "password": "a-strong-password-123"},
        format="json",
    )
    assert logged_out.status_code == 400

    readded = client.post(
        "/api/v1/tenant/members/",
        {"email": "temp@example.com", "password": "another-password-123", "role": "member"},
        format="json",
    )
    assert readded.status_code == 201
    assert readded.data["data"]["is_active"] is True


def test_tenant_endpoints_do_not_leak_other_workspaces(client, user, other_user):
    client.force_authenticate(user)
    members = client.get("/api/v1/tenant/members/").data["data"]
    assert [row["email"] for row in members] == ["person@example.com"]
    invites = client.get("/api/v1/tenant/invites/").data["data"]
    assert invites == []
