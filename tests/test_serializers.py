import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory

from relay.models import TelegramAccount, TelegramEntity
from relay.serializers import RelaySerializer


@pytest.mark.django_db
def test_relay_rejects_unrecognized_rule_keys():
    user = get_user_model().objects.create_user("u@example.com", password="password")
    account = TelegramAccount.objects.create(user=user, phone_number="+15551234567")
    source = TelegramEntity.objects.create(
        user=user, account=account, kind="source", peer_id=-1001, entity_type="channel"
    )
    destination = TelegramEntity.objects.create(
        user=user,
        account=account,
        kind="destination",
        peer_id=-1002,
        entity_type="channel",
    )
    request = APIRequestFactory().post("/")
    request.user = user
    serializer = RelaySerializer(
        data={
            "account": str(account.id),
            "source": str(source.id),
            "destination": str(destination.id),
            "name": "relay",
            "enabled": True,
            "rules": {"unknown_behavior": True},
        },
        context={"request": request},
    )
    assert not serializer.is_valid()
    assert "rules" in serializer.errors
