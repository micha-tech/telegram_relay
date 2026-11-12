import pytest

from relay.models import Signal, SignalSource, TelegramEntity
from relay.services.signals import capture

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_redis")]


@pytest.fixture
def entity(account):
    return TelegramEntity.objects.create(
        user=account.user,
        account=account,
        kind=TelegramEntity.Kind.SOURCE,
        peer_id=-1001234567890,
        entity_type="channel",
        username="signals",
        title="Signals Group",
    )


@pytest.fixture
def source(account, entity):
    return SignalSource.objects.create(
        account=account, entity=entity, name="Signals Group", enabled=True
    )


def test_capture_stores_the_signal(account, source):
    signal = capture(
        account,
        source,
        chat_id=source.entity.peer_id,
        message_id=42,
        text="Over 2.5 goals here",
        author="Tips",
    )
    assert signal is not None
    assert signal.text == "Over 2.5 goals here"
    assert signal.source_id == source.pk
    assert Signal.objects.count() == 1


def test_capture_is_idempotent(account, source):
    first = capture(
        account, source, chat_id=source.entity.peer_id, message_id=42, text="Over 2.5 goals"
    )
    second = capture(
        account, source, chat_id=source.entity.peer_id, message_id=42, text="Over 2.5 goals"
    )
    assert first is not None
    assert second is None
    assert Signal.objects.count() == 1


def test_capture_skips_blank_messages(account, source):
    assert (
        capture(account, source, chat_id=source.entity.peer_id, message_id=1, text="   ")
        is None
    )
    assert Signal.objects.count() == 0


def test_signal_list_is_available_to_every_authenticated_user(
    account, source, regular_user, client, telegram_env
):
    capture(account, source, chat_id=source.entity.peer_id, message_id=7, text="Over 2.5 goals")
    client.force_authenticate(regular_user)
    response = client.get("/api/v1/signals/")
    assert response.status_code == 200
    payload = response.data["data"]
    assert payload["count"] == 1
    row = payload["results"][0]
    assert row["text"] == "Over 2.5 goals"
    assert row["source_name"] == "Signals Group"


def test_signal_list_requires_authentication(client, telegram_env):
    assert client.get("/api/v1/signals/").status_code == 401


def test_signal_list_filters_by_text(account, source, user, client, telegram_env):
    capture(account, source, chat_id=source.entity.peer_id, message_id=1, text="Over 2.5 goals")
    capture(account, source, chat_id=source.entity.peer_id, message_id=2, text="Banker tip")
    client.force_authenticate(user)
    response = client.get("/api/v1/signals/?q=banker")
    assert response.status_code == 200
    assert response.data["data"]["count"] == 1
    assert response.data["data"]["results"][0]["text"] == "Banker tip"


def test_signal_list_filters_by_source(account, source, user, client, telegram_env):
    capture(account, source, chat_id=source.entity.peer_id, message_id=1, text="Over 2.5 goals")
    client.force_authenticate(user)
    response = client.get(f"/api/v1/signals/?source={source.pk}")
    assert response.status_code == 200
    assert response.data["data"]["count"] == 1
    response = client.get("/api/v1/signals/?source=00000000-0000-0000-0000-000000000000")
    assert response.data["data"]["count"] == 0


def test_signal_stats_summarise_the_feed(account, source, user, client, telegram_env):
    capture(account, source, chat_id=source.entity.peer_id, message_id=1, text="Over 2.5 goals")
    client.force_authenticate(user)
    response = client.get("/api/v1/signals/stats/")
    assert response.status_code == 200
    data = response.data["data"]
    assert data["total"] == 1
    assert data["last_24h"] == 1
    assert data["by_source"][0]["count"] == 1


def test_signal_sources_require_super_admin(entity, regular_user, client, telegram_env):
    client.force_authenticate(regular_user)
    response = client.get("/api/v1/signal-sources/")
    assert response.status_code == 403
    response = client.post(
        "/api/v1/signal-sources/",
        {
            "account": str(entity.account_id),
            "entity": str(entity.pk),
        },
        format="json",
    )
    assert response.status_code == 403


def test_super_admin_creates_a_signal_source(entity, user, client, telegram_env):
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/signal-sources/",
        {
            "account": str(entity.account_id),
            "entity": str(entity.pk),
            "name": "Signals Group",
        },
        format="json",
    )
    assert response.status_code == 201
    data = response.data["data"]
    assert data["chat_title"] == "Signals Group"
    assert data["name"] == "Signals Group"
    assert data["account_label"] == "Ada"


def test_signal_source_rejects_chat_from_another_account(account, user, client, telegram_env):
    other_entity = TelegramEntity.objects.create(
        user=user,
        account=account,
        kind=TelegramEntity.Kind.DESTINATION,
        peer_id=-1009999,
        entity_type="channel",
    )
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/signal-sources/",
        {
            "account": str(account.pk),
            "entity": str(other_entity.pk),
        },
        format="json",
    )
    assert response.status_code == 400


def test_super_admin_updates_and_deletes_a_source(source, user, client, telegram_env):
    client.force_authenticate(user)
    response = client.patch(
        f"/api/v1/signal-sources/{source.pk}/",
        {"name": "Updated Group", "enabled": False},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["data"]["name"] == "Updated Group"
    assert response.data["data"]["enabled"] is False
    response = client.delete(f"/api/v1/signal-sources/{source.pk}/")
    assert response.status_code == 200
    assert SignalSource.objects.count() == 0


def test_scrape_request_is_queued_for_the_worker(source, user, client, monkeypatch, telegram_env):
    queued = []
    monkeypatch.setattr(
        "relay.views.enqueue_scrape",
        lambda account_id, source_id: queued.append((str(account_id), str(source_id))),
    )
    client.force_authenticate(user)
    response = client.post(f"/api/v1/signal-sources/{source.pk}/scrape/")
    assert response.status_code == 202
    assert response.data["data"]["queued"] is True
    assert queued == [(str(source.account_id), str(source.pk))]


@pytest.mark.django_db(transaction=True)
def test_worker_captures_messages(account, source, telegram_env):
    import asyncio

    from relay.services.worker import TelegramRelayWorker

    class FakeMessage:
        raw_text = "Over 2.5 goals"
        post_author = "Tips"
        sender_id = 555
        date = None

    class FakeEvent:
        id = 99
        chat_id = source.entity.peer_id
        raw_text = "Over 2.5 goals"
        sender_id = 555
        date = None
        message = FakeMessage()

    worker = TelegramRelayWorker.__new__(TelegramRelayWorker)

    async def scenario():
        await worker._capture_signals(account, source.entity.peer_id, FakeEvent())

    asyncio.run(scenario())
    assert Signal.objects.count() == 1
    assert Signal.objects.get().text == "Over 2.5 goals"
    assert Signal.objects.get().author == "Tips"


@pytest.mark.django_db(transaction=True)
def test_worker_ignores_messages_from_unwatched_chats(account, entity, telegram_env):
    import asyncio

    from relay.services.worker import TelegramRelayWorker

    class FakeEvent:
        id = 1
        chat_id = -1009999999
        raw_text = "Over 2.5 goals"
        sender_id = 1
        date = None
        message = None

    worker = TelegramRelayWorker.__new__(TelegramRelayWorker)

    async def scenario():
        await worker._capture_signals(account, entity.peer_id, FakeEvent())

    asyncio.run(scenario())
    assert Signal.objects.count() == 0
