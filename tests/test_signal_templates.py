import asyncio
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from relay.models import (
    AuditLog,
    Signal,
    SignalSource,
    SignalTemplate,
    TelegramEntity,
)
from relay.services.signal_formatting import format_signal

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_redis")]

TEMPLATES = "/api/v1/signal-templates/"
SOURCES = "/api/v1/signal-sources/"


def config(**overrides):
    return {
        "name": "Clean tips",
        "body": "",
        "header": "",
        "footer": "",
        "strip_header_lines": 0,
        "remove_urls": False,
        "remove_patterns": [],
        "replacements": [],
        "drop_empty": True,
        **overrides,
    }


def source_entity(account, peer_id=-1001234567890):
    return TelegramEntity.objects.create(
        user=account.user,
        account=account,
        kind=TelegramEntity.Kind.SOURCE,
        peer_id=peer_id,
        entity_type="channel",
        title="Tips",
    )


def destination_entity(account, peer_id=-1009876543210):
    return TelegramEntity.objects.create(
        user=account.user,
        account=account,
        kind=TelegramEntity.Kind.DESTINATION,
        peer_id=peer_id,
        entity_type="channel",
        title="Desk",
    )


def test_admin_creates_a_template_in_their_workspace(client, user):
    client.force_authenticate(user)
    response = client.post(TEMPLATES, config(), format="json")

    assert response.status_code == 201
    data = response.data["data"]
    assert data["name"] == "Clean tips"
    assert str(data["tenant"]) == str(user.membership.tenant_id)
    assert data["drop_empty"] is True
    assert SignalTemplate.objects.count() == 1
    assert AuditLog.objects.filter(action="signal_template.created").count() == 1


def test_members_cannot_manage_templates(client, regular_user):
    client.force_authenticate(regular_user)

    assert client.get(TEMPLATES).status_code == 403
    assert client.post(TEMPLATES, config(), format="json").status_code == 403


def test_templates_are_isolated_between_workspaces(client, user, other_user):
    template = SignalTemplate.objects.create(tenant=user.membership.tenant, name="Private")
    client.force_authenticate(other_user)

    assert client.get(TEMPLATES).data["data"]["results"] == []
    assert client.get(f"{TEMPLATES}{template.id}/").status_code == 404


def test_admin_can_update_and_delete_a_template(client, user):
    template = SignalTemplate.objects.create(tenant=user.membership.tenant, name="Old")
    client.force_authenticate(user)

    updated = client.patch(
        f"{TEMPLATES}{template.id}/", {"name": "New", "header": "ALERT"}, format="json"
    )
    assert updated.status_code == 200
    assert updated.data["data"]["name"] == "New"
    assert updated.data["data"]["header"] == "ALERT"

    deleted = client.delete(f"{TEMPLATES}{template.id}/")
    assert deleted.status_code == 200
    assert SignalTemplate.objects.count() == 0


def test_preview_applies_cleanup_then_renders_the_template(client, user):
    client.force_authenticate(user)
    response = client.post(
        f"{TEMPLATES}preview/",
        config(
            strip_header_lines=1,
            remove_urls=True,
            remove_patterns=["SPONSOR"],
            replacements=[{"from": "2.5", "to": "2.75"}],
            body="{{ source }}: {{ text }}",
            header="PRIME EDGE",
            footer="— verified",
        )
        | {
            "text": "SPONSOR\nOver 2.5 goals https://example.com",
            "source": "Tips",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["data"]["skipped"] is False
    assert response.data["data"]["formatted"] == (
        "PRIME EDGE\nTips: Over 2.75 goals\n— verified"
    )


def test_preview_skips_empty_results_unless_drop_empty_is_disabled(client, user):
    client.force_authenticate(user)
    payload = config(remove_patterns=["SPONSOR"], header="ALERT") | {"text": "SPONSOR"}

    skipped = client.post(f"{TEMPLATES}preview/", payload, format="json")
    assert skipped.data["data"] == {"formatted": "", "skipped": True}

    kept = client.post(
        f"{TEMPLATES}preview/", payload | {"drop_empty": False}, format="json"
    )
    assert kept.data["data"] == {"formatted": "ALERT", "skipped": False}


def test_body_keeps_apostrophes_and_ordinary_prose_literal(client, user):
    client.force_authenticate(user)
    response = client.post(
        f"{TEMPLATES}preview/",
        config(body="{{ source }}: {{ text }}\nyou're welcome to football")
        | {"text": "Over 2.5 goals", "source": "Tips"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["data"]["formatted"] == (
        "Tips: Over 2.5 goals\nyou're welcome to football"
    )


def test_admin_can_save_a_template_containing_apostrophes(client, user):
    client.force_authenticate(user)
    response = client.post(
        TEMPLATES,
        config(
            name="Sign-off",
            body="{{ text }}\nyou're welcome to football",
        ),
        format="json",
    )

    assert response.status_code == 201
    assert response.data["data"]["body"] == "{{ text }}\nyou're welcome to football"


def test_unknown_placeholders_are_left_untouched(client, user):
    client.force_authenticate(user)
    response = client.post(
        f"{TEMPLATES}preview/",
        config(body="{{ you're welcome to football }} {{ missing }}")
        | {"text": "Over 2.5 goals"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["data"]["formatted"] == (
        "{{ you're welcome to football }} {{ missing }}"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("remove_patterns", ["("]),
        ("body", "x" * 4001),
        ("replacements", [{"from": ""}]),
    ],
)
def test_preview_rejects_invalid_configurations(client, user, field, value):
    client.force_authenticate(user)
    response = client.post(
        f"{TEMPLATES}preview/", config(**{field: value}) | {"text": "hello"}, format="json"
    )

    assert response.status_code == 400
    assert field in response.data["error"]["details"]


def test_source_can_use_a_template_from_its_workspace(client, user, account):
    template = SignalTemplate.objects.create(tenant=account.tenant, name="Approved")
    entity = source_entity(account)
    client.force_authenticate(user)
    response = client.post(
        SOURCES,
        {
            "account": str(account.id),
            "entity": str(entity.id),
            "template": str(template.id),
            "name": "Tips",
        },
        format="json",
    )

    assert response.status_code == 201
    assert str(response.data["data"]["template"]) == str(template.id)
    assert response.data["data"]["template_name"] == "Approved"


def test_source_rejects_a_template_from_another_workspace(client, user, account, other_user):
    template = SignalTemplate.objects.create(tenant=other_user.membership.tenant, name="Foreign")
    entity = source_entity(account)
    client.force_authenticate(user)
    response = client.post(
        SOURCES,
        {
            "account": str(account.id),
            "entity": str(entity.id),
            "template": str(template.id),
        },
        format="json",
    )

    assert response.status_code == 400
    assert "template" in response.data["error"]["details"]


@pytest.mark.django_db(transaction=True)
def test_live_forward_uses_template_but_the_feed_keeps_raw_text(
    client, user, account, telegram_env, monkeypatch
):
    template = SignalTemplate.objects.create(
        tenant=account.tenant,
        name="Desk format",
        strip_header_lines=1,
        remove_urls=True,
        replacements=[{"from": "2.5", "to": "2.75"}],
        body="{{ source }}: {{ text }}",
        header="PRIME EDGE",
    )
    entity = source_entity(account)
    source = SignalSource.objects.create(
        account=account,
        entity=entity,
        name="Tips",
        forward_enabled=True,
        forward_to=destination_entity(account),
        template=template,
    )
    calls = []

    async def fake_forward(client_, source_, text, **kwargs):
        calls.append((client_, source_, text))

    monkeypatch.setattr("relay.services.signals.forward_signal", fake_forward)

    class FakeMessage:
        post_author = "Analyst"
        sender_id = 555
        date = datetime(2026, 1, 2, tzinfo=dt_timezone.utc)

    class FakeEvent:
        id = 42
        chat_id = entity.peer_id
        raw_text = "SPONSOR\nOver 2.5 goals https://example.com"
        sender_id = 555
        date = FakeMessage.date
        message = FakeMessage()

    from relay.services.worker import TelegramRelayWorker

    worker = TelegramRelayWorker.__new__(TelegramRelayWorker)
    asyncio.run(worker._capture_signals(account, object(), entity.peer_id, FakeEvent()))

    assert len(calls) == 1
    assert calls[0][1].id == source.id
    assert calls[0][2] == "PRIME EDGE\nTips: Over 2.75 goals"
    stored = Signal.objects.get()
    assert stored.text == FakeEvent.raw_text
    assert stored.author == "Analyst"


def test_format_signal_is_pure_without_a_template():
    result = format_signal(
        None, text="  Plain signal  ", source="Tips", author="Analyst"
    )
    assert result == "Plain signal"
