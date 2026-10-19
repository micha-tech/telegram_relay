import os
from contextlib import contextmanager

import pytest

os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "test-api-hash")


class FakeRedisService:
    def __init__(self, client=None):
        self.client = client or self

    @contextmanager
    def lock(self, name, timeout=120):
        yield

    def health(self):
        return True

    def exists(self, key):
        return False


@pytest.fixture
def no_redis(monkeypatch):
    monkeypatch.setattr("relay.services.telegram_auth.RedisService", FakeRedisService)
    monkeypatch.setattr("relay.services.account_lifecycle.RedisService", FakeRedisService)
    return FakeRedisService


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-api-hash")
    monkeypatch.setenv("TELEGRAM_SESSION_ENCRYPTION_KEY", "test-encryption-key")


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="person@example.com", email="person@example.com", password="password-12345"
    )


@pytest.fixture
def other_user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="other@example.com", email="other@example.com", password="password-12345"
    )


@pytest.fixture
def account(user):
    from relay.models import TelegramAccount

    return TelegramAccount.objects.create(
        user=user,
        phone_number="+15551234567",
        telegram_user_id=999,
        status=TelegramAccount.Status.CONNECTED,
        display_name="Ada",
        username="ada",
    )


@pytest.fixture
def connected_account(account, telegram_env):
    from relay.models import TelegramSession
    from relay.services.crypto import SecretBox

    TelegramSession.objects.create(
        account=account,
        encrypted_session=SecretBox("test-encryption-key").encrypt("session-string"),
        key_version=1,
    )
    return account


@pytest.fixture
def client():
    from rest_framework.test import APIClient

    return APIClient()
