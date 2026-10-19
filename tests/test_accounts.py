from unittest.mock import AsyncMock

import pytest

from relay.models import AccountConnectionEvent, TelegramAccount, TelegramSession
from relay.services.account_lifecycle import AccountLifecycleError, AccountLifecycleService

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_redis")]


def test_disconnect_deletes_session_and_marks_disconnected(
    connected_account, monkeypatch, telegram_env
):
    revoke = AsyncMock()
    monkeypatch.setattr(AccountLifecycleService, "_revoke", revoke)
    AccountLifecycleService().disconnect(connected_account)
    connected_account.refresh_from_db()
    assert connected_account.status == TelegramAccount.Status.DISCONNECTED
    assert not TelegramSession.objects.filter(account=connected_account).exists()
    revoke.assert_awaited_once()


def test_reconnect_requires_session(account, telegram_env):
    with pytest.raises(AccountLifecycleError):
        AccountLifecycleService().reconnect(account)


def test_reconnect_rejects_invalid_session(connected_account, telegram_env):
    connected_account.status = TelegramAccount.Status.INVALID
    connected_account.save()
    with pytest.raises(AccountLifecycleError):
        AccountLifecycleService().reconnect(connected_account)


def test_reconnect_restores_connected_status(connected_account, telegram_env):
    connected_account.status = TelegramAccount.Status.ERROR
    connected_account.save()
    result = AccountLifecycleService().reconnect(connected_account)
    assert result.status == TelegramAccount.Status.CONNECTED


def test_account_endpoints_enforce_ownership(connected_account, other_user, client, telegram_env):
    client.force_authenticate(other_user)
    response = client.get(f"/api/v1/telegram/accounts/{connected_account.pk}/")
    assert response.status_code == 404
    response = client.post(f"/api/v1/telegram/accounts/{connected_account.pk}/disconnect/")
    assert response.status_code == 404
    response = client.post(f"/api/v1/telegram/accounts/{connected_account.pk}/reconnect/")
    assert response.status_code == 404


def test_account_list_masks_phone_and_hides_session(connected_account, client, telegram_env):
    client.force_authenticate(connected_account.user)
    response = client.get("/api/v1/telegram/accounts/")
    payload = response.data["data"]["results"][0]
    assert payload["phone_masked"] == "+••••••••4567"
    assert "phone_number" not in payload
    assert "encrypted_session" not in payload
    text = str(response.data)
    assert "+15551234567" not in text
    assert "session-string" not in text


def test_disconnect_endpoint_removes_credentials(
    connected_account, client, monkeypatch, telegram_env
):
    monkeypatch.setattr(AccountLifecycleService, "_revoke", AsyncMock())
    client.force_authenticate(connected_account.user)
    response = client.post(f"/api/v1/telegram/accounts/{connected_account.pk}/disconnect/")
    assert response.status_code == 200
    assert response.data["data"]["status"] == TelegramAccount.Status.DISCONNECTED
    assert not TelegramSession.objects.filter(account=connected_account).exists()


def test_reconnect_endpoint(connected_account, client, telegram_env):
    connected_account.status = TelegramAccount.Status.DISCONNECTED
    connected_account.save()
    client.force_authenticate(connected_account.user)
    response = client.post(f"/api/v1/telegram/accounts/{connected_account.pk}/reconnect/")
    assert response.status_code == 200
    connected_account.refresh_from_db()
    assert connected_account.status == TelegramAccount.Status.CONNECTED


def test_health_endpoint_reports_session_and_worker_state(connected_account, client, telegram_env):
    client.force_authenticate(connected_account.user)
    response = client.get(f"/api/v1/telegram/accounts/{connected_account.pk}/health/")
    assert response.status_code == 200
    data = response.data["data"]
    assert data["session_present"] is True
    assert data["status"] == TelegramAccount.Status.CONNECTED
    assert set(data) >= {"status", "session_present", "worker_active", "last_connected_at"}


def test_telegram_identity_unique_per_owner(user, telegram_env):
    from django.db import IntegrityError, transaction

    TelegramAccount.objects.create(user=user, phone_number="+15551234567", telegram_user_id=42)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TelegramAccount.objects.create(
                user=user, phone_number="+15557654321", telegram_user_id=42
            )


def test_api_v1_and_legacy_prefix_both_work(client, user, telegram_env):
    client.force_authenticate(user)
    for prefix in ("/api/v1", "/api"):
        response = client.get(f"{prefix}/auth/me")
        assert response.status_code == 200
        assert response.data["data"]["email"] == user.email


def test_account_detail_safe_fields(connected_account, client, telegram_env):
    client.force_authenticate(connected_account.user)
    response = client.get(f"/api/v1/telegram/accounts/{connected_account.pk}/")
    payload = response.data["data"]
    assert payload["telegram_user_id"] == 999
    assert payload["display_name"] == "Ada"
    assert "encrypted_session" not in payload


def test_events_endpoint_lists_recent_events(connected_account, client, telegram_env):
    AccountConnectionEvent.objects.create(
        account=connected_account, kind=AccountConnectionEvent.Kind.CONNECTED
    )
    client.force_authenticate(connected_account.user)
    response = client.get(f"/api/v1/telegram/accounts/{connected_account.pk}/events/")
    assert response.status_code == 200
    assert response.data["data"][0]["kind"] == "connected"
