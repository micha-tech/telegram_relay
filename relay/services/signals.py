"""Keyword matching and idempotent capture of Telegram messages as signals."""

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from relay.models import Signal, SignalSource

DEFAULT_MIN_LENGTH = 0
MAX_TEXT_LENGTH = 8000


@dataclass(frozen=True)
class SignalRules:
    """Normalised keyword rules for a watched chat."""

    keywords: tuple[str, ...] = ()
    blocked_phrases: tuple[str, ...] = ()
    min_length: int = DEFAULT_MIN_LENGTH
    case_sensitive: bool = False

    @classmethod
    def from_dict(cls, rules) -> "SignalRules":
        raw = rules or {}
        keywords = _clean_list(raw.get("keywords"))
        blocked_phrases = _clean_list(raw.get("blocked_phrases"))
        try:
            min_length = int(raw.get("min_length") or DEFAULT_MIN_LENGTH)
        except (TypeError, ValueError):
            min_length = DEFAULT_MIN_LENGTH
        return cls(
            keywords=keywords,
            blocked_phrases=blocked_phrases,
            min_length=max(0, min_length),
            case_sensitive=bool(raw.get("case_sensitive", False)),
        )


def _clean_list(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def match_keywords(text: str, rules) -> tuple[str, ...]:
    """Return the keywords present in text; an empty tuple means "not a signal"."""
    parsed = SignalRules.from_dict(rules)
    if not parsed.keywords:
        return ()
    body = text or ""
    stripped = body.strip()
    if not stripped:
        return ()
    if parsed.min_length and len(stripped) < parsed.min_length:
        return ()
    haystack = body if parsed.case_sensitive else body.casefold()
    for phrase in parsed.blocked_phrases:
        needle = phrase if parsed.case_sensitive else phrase.casefold()
        if needle in haystack:
            return ()
    return tuple(
        keyword
        for keyword in parsed.keywords
        if (keyword if parsed.case_sensitive else keyword.casefold()) in haystack
    )


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
                matched_terms=[],
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
    terms=None,
) -> Signal | None:
    """Match a message against a source's rules and store it when it matches."""
    matched = tuple(terms) if terms is not None else match_keywords(text, source.rules)
    if not matched:
        return None
    body = (text or "")[:MAX_TEXT_LENGTH]
    try:
        with transaction.atomic():
            signal = Signal.objects.create(
                account=account,
                source=source,
                chat_id=chat_id,
                message_id=message_id,
                text=body,
                author=(author or "")[:255],
                author_id=author_id,
                matched_terms=list(matched),
                sent_at=sent_at or timezone.now(),
            )
    except IntegrityError:
        return None
    SignalSource.objects.filter(pk=source.pk).update(last_signal_at=signal.sent_at)
    return signal
