from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.utils import timezone
from telethon.errors import (
    FloodWaitError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from relay.models import (
    AccountConnectionEvent,
    AuthenticationChallenge,
    TelegramAccount,
    TelegramSession,
)
from relay.services.crypto import SecretBox
from relay.services.telegram_auth import TelegramAuthService, cleanup_expired_challenges

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_redis")]


class FakeSession:
    def save(self):
        return "encrypted-session-string"


class FakeClient:
    def __init__(self, sign_in_side_effect=None, flood_send_code=None):
        self.session = FakeSession()
        self.sign_in = AsyncMock(side_effect=sign_in_side_effect)
        self.send_code_request = AsyncMock(
            side_effect=flood_send_code
            or (lambda phone: SimpleNamespace(phone_code_hash="secret-hash"))
        )
        self.get_me = AsyncMock(
            return_value=SimpleNamespace(
                id=999, first_name="Ada", last_name="Lovelace", username="ada"
            )
        )
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.log_out = AsyncMock()
        self.is_user_authorized = AsyncMock(return_value=True)


def install_fake_client(monkeypatch, fake):
    async def _client(self, session=""):
        await fake.connect()
        return fake

    monkeypatch.setattr(TelegramAuthService, "_client", _client)
    return fake


def test_start_creates_encrypted_challenge(user, client, monkeypatch, telegram_env):
    fake = install_fake_client(monkeypatch, FakeClient())
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    assert response.status_code == 202
    challenge_id = response.data["data"]["challenge_id"]
    challenge = AuthenticationChallenge.objects.get(pk=challenge_id)
    assert challenge.state == AuthenticationChallenge.State.CODE_SENT
    assert bytes(challenge.encrypted_session) != b"encrypted-session-string"
    assert TelegramSession.objects.filter(account=challenge.account).exists() is False
    fake.send_code_request.assert_awaited_once_with("+15551234567")
    assert AccountConnectionEvent.objects.filter(
        account=challenge.account, kind=AccountConnectionEvent.Kind.CODE_SENT
    ).exists()


def test_verify_code_completes_and_persists_session(user, client, monkeypatch, telegram_env):
    install_fake_client(monkeypatch, FakeClient())
    client.force_authenticate(user)
    start = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    challenge_id = start.data["data"]["challenge_id"]
    response = client.post(
        "/api/v1/telegram/auth/verify",
        {"challenge_id": challenge_id, "code": "12345"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["data"]["status"] == AuthenticationChallenge.State.COMPLETE
    account = TelegramAccount.objects.get(user=user)
    assert account.status == TelegramAccount.Status.CONNECTED
    assert account.telegram_user_id == 999
    assert account.display_name == "Ada Lovelace"
    assert account.username == "ada"
    session = TelegramSession.objects.get(account=account)
    assert bytes(session.encrypted_session) != b"encrypted-session-string"
    assert SecretBox("test-encryption-key").decrypt(session.encrypted_session) == (
        "encrypted-session-string"
    )
    assert session.key_version == 1


def test_verify_requires_password_step(user, client, monkeypatch, telegram_env):
    install_fake_client(
        monkeypatch, FakeClient(sign_in_side_effect=SessionPasswordNeededError(None))
    )
    client.force_authenticate(user)
    start = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    challenge_id = start.data["data"]["challenge_id"]
    response = client.post(
        "/api/v1/telegram/auth/verify",
        {"challenge_id": challenge_id, "code": "12345"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["data"]["status"] == AuthenticationChallenge.State.PASSWORD_REQUIRED
    assert not TelegramAccount.objects.get(user=user).status == TelegramAccount.Status.CONNECTED


def test_verify_2fa_completes(user, client, monkeypatch, telegram_env):
    fake = install_fake_client(
        monkeypatch, FakeClient(sign_in_side_effect=SessionPasswordNeededError(None))
    )
    client.force_authenticate(user)
    start = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    challenge_id = start.data["data"]["challenge_id"]
    client.post(
        "/api/v1/telegram/auth/verify",
        {"challenge_id": challenge_id, "code": "12345"},
        format="json",
    )
    fake.sign_in = AsyncMock(return_value=True)
    response = client.post(
        "/api/v1/telegram/auth/2fa",
        {"challenge_id": challenge_id, "password": "super-secret"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["data"]["status"] == AuthenticationChallenge.State.COMPLETE
    assert TelegramAccount.objects.get(user=user).status == TelegramAccount.Status.CONNECTED


def test_invalid_code_increments_attempts(user, client, monkeypatch, telegram_env):
    install_fake_client(monkeypatch, FakeClient(sign_in_side_effect=PhoneCodeInvalidError(None)))
    client.force_authenticate(user)
    start = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    challenge_id = start.data["data"]["challenge_id"]
    response = client.post(
        "/api/v1/telegram/auth/verify",
        {"challenge_id": challenge_id, "code": "00000"},
        format="json",
    )
    assert response.status_code == 400
    challenge = AuthenticationChallenge.objects.get(pk=challenge_id)
    assert challenge.attempts == 1


def test_expired_challenge_is_rejected(user, client, monkeypatch, telegram_env):
    install_fake_client(monkeypatch, FakeClient())
    client.force_authenticate(user)
    start = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    challenge_id = start.data["data"]["challenge_id"]
    AuthenticationChallenge.objects.filter(pk=challenge_id).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    response = client.post(
        "/api/v1/telegram/auth/verify",
        {"challenge_id": challenge_id, "code": "12345"},
        format="json",
    )
    assert response.status_code == 400


def test_flood_wait_is_sanitized(user, client, monkeypatch, telegram_env):
    install_fake_client(
        monkeypatch, FakeClient(flood_send_code=FloodWaitError(None, capture=30))
    )
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    assert response.status_code == 400
    assert "30" in str(response.data)
    assert "FloodWait" not in str(response.data)


def test_other_user_cannot_use_challenge(user, other_user, client, monkeypatch, telegram_env):
    install_fake_client(monkeypatch, FakeClient())
    client.force_authenticate(user)
    start = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    challenge_id = start.data["data"]["challenge_id"]
    client.force_authenticate(other_user)
    response = client.post(
        "/api/v1/telegram/auth/verify",
        {"challenge_id": challenge_id, "code": "12345"},
        format="json",
    )
    assert response.status_code == 400
    assert AuthenticationChallenge.objects.get(pk=challenge_id).state != (
        AuthenticationChallenge.State.COMPLETE
    )


def test_duplicate_telegram_identity_merges_to_existing_account(
    user, client, monkeypatch, telegram_env
):
    existing = TelegramAccount.objects.create(
        user=user,
        phone_number="+15550000000",
        telegram_user_id=999,
        status=TelegramAccount.Status.DISCONNECTED,
        username="ada",
    )
    install_fake_client(monkeypatch, FakeClient())
    client.force_authenticate(user)
    start = client.post(
        "/api/v1/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    challenge_id = start.data["data"]["challenge_id"]
    response = client.post(
        "/api/v1/telegram/auth/verify",
        {"challenge_id": challenge_id, "code": "12345"},
        format="json",
    )
    assert response.status_code == 200
    assert TelegramAccount.objects.filter(user=user).count() == 1
    existing.refresh_from_db()
    assert existing.status == TelegramAccount.Status.CONNECTED
    assert existing.phone_number == "+15551234567"
    assert TelegramSession.objects.filter(account=existing).exists()


def test_multiple_accounts_per_user(user, client, monkeypatch, telegram_env):
    async def _client(self, session=""):
        fake = FakeClient()
        signed = {}

        async def sign_in(phone=None, code=None, phone_code_hash=None, password=None):
            signed["phone"] = phone
            return True

        async def get_me():
            index = 0 if signed["phone"] == "+15551234567" else 1
            return SimpleNamespace(
                id=100 + index, first_name=f"User{index}", last_name="", username=f"user{index}"
            )

        fake.sign_in = AsyncMock(side_effect=sign_in)
        fake.get_me = AsyncMock(side_effect=get_me)
        await fake.connect()
        return fake

    monkeypatch.setattr(TelegramAuthService, "_client", _client)
    client.force_authenticate(user)
    for phone in ["+15551234567", "+15557654321"]:
        start = client.post("/api/v1/telegram/auth/start", {"phone_number": phone}, format="json")
        assert start.status_code == 202
        response = client.post(
            "/api/v1/telegram/auth/verify",
            {"challenge_id": start.data["data"]["challenge_id"], "code": "12345"},
            format="json",
        )
        assert response.status_code == 200
    accounts = TelegramAccount.objects.filter(user=user)
    assert accounts.count() == 2
    assert {a.telegram_user_id for a in accounts} == {100, 101}
    assert all(TelegramSession.objects.filter(account=a).exists() for a in accounts)


def test_cleanup_expires_and_removes_old_challenges(user, telegram_env):
    fresh = AuthenticationChallenge.objects.create(
        user=user,
        account=TelegramAccount.objects.create(user=user, phone_number="+15551112222"),
        encrypted_session=b"x",
        encrypted_phone_code_hash=b"y",
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    old = AuthenticationChallenge.objects.create(
        user=user,
        account=TelegramAccount.objects.create(user=user, phone_number="+15553334444"),
        encrypted_session=b"x",
        encrypted_phone_code_hash=b"y",
        state=AuthenticationChallenge.State.FAILED,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    AuthenticationChallenge.objects.filter(pk=old.pk).update(
        updated_at=timezone.now() - timedelta(days=8)
    )
    cleanup_expired_challenges()
    fresh.refresh_from_db()
    assert fresh.state == AuthenticationChallenge.State.EXPIRED
    assert not AuthenticationChallenge.objects.filter(pk=old.pk).exists()
