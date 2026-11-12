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
def tenant(db):
    from relay.models import Tenant

    return Tenant.objects.create(name="Test Workspace", slug="test-workspace")


def make_member(email, tenant, role):
    from django.contrib.auth import get_user_model

    from relay.models import Membership

    user = get_user_model().objects.create_user(
        username=email,
        email=email,
        password="password-12345",
        is_superuser=role == Membership.Role.OWNER,
        is_staff=role == Membership.Role.OWNER,
    )
    Membership.objects.create(user=user, tenant=tenant, role=role)
    return user


@pytest.fixture
def user(tenant):
    from relay.models import Membership

    return make_member("person@example.com", tenant, Membership.Role.OWNER)


@pytest.fixture
def other_user(db):
    from relay.models import Membership, Tenant

    other_tenant = Tenant.objects.create(name="Other Workspace", slug="other-workspace")
    return make_member("other@example.com", other_tenant, Membership.Role.OWNER)


@pytest.fixture
def regular_user(tenant):
    from relay.models import Membership

    return make_member("regular@example.com", tenant, Membership.Role.MEMBER)


@pytest.fixture
def account(user, tenant):
    from relay.models import TelegramAccount

    return TelegramAccount.objects.create(
        user=user,
        tenant=tenant,
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
