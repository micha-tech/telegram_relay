import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from relay.models import ProcessedMessage, RelayConfiguration, TelegramAccount, TelegramEntity

@pytest.mark.django_db
def test_processed_message_idempotency_constraint():
    user = get_user_model().objects.create_user("u@example.com", password="password")
    account = TelegramAccount.objects.create(user=user, phone_number="+100000000")
    source = TelegramEntity.objects.create(user=user, account=account, kind="source", peer_id=-1001, entity_type="channel")
    destination = TelegramEntity.objects.create(user=user, account=account, kind="destination", peer_id=-1002, entity_type="channel")
    relay = RelayConfiguration.objects.create(user=user, account=account, source=source, destination=destination, name="test")
    values = dict(account=account, relay=relay, source_chat_id=-1001, source_message_id=12, status="processing")
    ProcessedMessage.objects.create(**values)
    with pytest.raises(IntegrityError), transaction.atomic(): ProcessedMessage.objects.create(**values)

