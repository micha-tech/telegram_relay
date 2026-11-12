"""Idempotent capture of Telegram messages as signals."""

from django.db import IntegrityError, transaction
from django.utils import timezone
from telethon.errors import FloodWaitError, RPCError

from relay.models import Signal, SignalSource

MAX_TEXT_LENGTH = 8000


def _resolve_peer(client, entity):
    from .worker import TelegramRelayWorker

    return TelegramRelayWorker()._input_peer(entity)


async def forward_signal(
    client,
    source: SignalSource,
    text: str,
    *,
    original_chat_id=None,
    original_message_id=None,
):
    """Forward a captured signal to the configured destination channel/chat."""
    if not source.forward_enabled or not source.forward_to_id:
        return None
    destination = source.forward_to
    if destination is None:
        return None
    body = (text or "").strip()
    if not body:
        return None
    try:
        peer = _resolve_peer(client, destination)
        return await client.send_message(peer, body, link_preview=False)
    except (FloodWaitError, RPCError, RuntimeError, ValueError):
        return None
    except Exception:
        return None


def record_signal(
    account,
    source: SignalSource,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    author: str = "",
    author_id: int | None = None,
    sent_at=None,
) -> Signal | None:
    """Store a signal once; returns None when the message was already captured."""
    body = (text or "")[:MAX_TEXT_LENGTH]
    try:
        with transaction.atomic():
            return Signal.objects.create(
                account=account,
                source=source,
                chat_id=chat_id,
                message_id=message_id,
                text=body,
                author=(author or "")[:255],
                author_id=author_id,
                sent_at=sent_at or timezone.now(),
            )
    except IntegrityError:
        return None


def capture(
    account,
    source: SignalSource,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    author: str = "",
    author_id: int | None = None,
    sent_at=None,
) -> Signal | None:
    """Capture a message from a watched chat as a signal."""
    body = (text or "")
    if not body.strip():
        return None
    body_truncated = body[:MAX_TEXT_LENGTH]
    try:
        with transaction.atomic():
            signal = Signal.objects.create(
                account=account,
                source=source,
                chat_id=chat_id,
                message_id=message_id,
                text=body_truncated,
                author=(author or "")[:255],
                author_id=author_id,
                sent_at=sent_at or timezone.now(),
            )
    except IntegrityError:
        return None
    SignalSource.objects.filter(pk=source.pk).update(last_signal_at=signal.sent_at)
    return signal
