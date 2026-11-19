"""Text transformations applied when a captured signal is forwarded.

The stored :class:`~relay.models.Signal` always keeps the raw message. Templates
only affect the copy sent to a destination chat, so the workspace feed remains a
faithful, searchable record. A template is plain text with simple
``{{ placeholder }}`` tokens (``{{ text }}``, ``{{ source }}``, ``{{ author }}``
…) plus a small set of ordered cleanup steps.

Bodies are rendered with literal placeholder substitution rather than a full
template language. Admins routinely write ordinary prose (apostrophes, quotes,
braces) in their layouts, and treating that prose as code produced confusing
parse errors — for example ``{{ you're welcome }}``. Only exact, known
placeholders are replaced; everything else is kept exactly as written.
"""

import re

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
MAX_BODY_LENGTH = 4000
MAX_TEMPLATE_LENGTH = 4000
MAX_TEXT_LENGTH = 2000
MAX_PATTERNS = 20
MAX_REPLACEMENTS = 50
MAX_STRIP_HEADER_LINES = 50


class TemplateConfigError(ValueError):
    """Raised when an admin-supplied template configuration is invalid."""


def validate_body(body: str) -> str:
    """Validate a template body's size; all of its text is treated as literal."""
    if len(body or "") > MAX_TEMPLATE_LENGTH:
        raise TemplateConfigError(
            f"Template body must be at most {MAX_TEMPLATE_LENGTH} characters."
        )
    return body or ""


def render_body(body: str, context: dict) -> str:
    """Replace known placeholders, leaving all other text untouched."""
    return PLACEHOLDER_RE.sub(
        lambda match: str(context.get(match.group(1), match.group(0))), body
    )


def validate_patterns(patterns) -> list[str]:
    if not isinstance(patterns, list):
        raise TemplateConfigError("Remove patterns must be a list.")
    if len(patterns) > MAX_PATTERNS:
        raise TemplateConfigError(f"At most {MAX_PATTERNS} remove patterns are allowed.")
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or len(pattern) > 500:
            raise TemplateConfigError("Each remove pattern must be a non-empty string.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise TemplateConfigError(f"Invalid regular expression: {pattern}") from exc
    return list(patterns)


def validate_replacements(replacements) -> list[dict[str, str]]:
    if not isinstance(replacements, list):
        raise TemplateConfigError("Replacements must be a list.")
    if len(replacements) > MAX_REPLACEMENTS:
        raise TemplateConfigError(f"At most {MAX_REPLACEMENTS} replacements are allowed.")
    cleaned = []
    for item in replacements:
        if not isinstance(item, dict):
            raise TemplateConfigError("Each replacement needs 'from' and 'to' strings.")
        old = item.get("from")
        new = item.get("to")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise TemplateConfigError("Each replacement needs 'from' and 'to' strings.")
        cleaned.append({"from": old, "to": new})
    return cleaned


def _value(config, key, default=None):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def format_signal(
    config,
    *,
    text: str,
    source: str = "",
    account: str = "",
    author: str = "",
    sent_at=None,
    message_id=None,
    peer_id=None,
) -> str | None:
    """Return the message to forward, or ``None`` when it should be skipped.

    ``config`` may be a :class:`SignalTemplate` instance or a validated dict
    (used by the preview endpoint). Order of operations is deterministic:

    1. drop the first N lines (source headers),
    2. remove regex matches,
    3. remove URLs,
    4. apply literal replacements,
    5. render the optional template body,
    6. prepend the header and append the footer.
    """
    body = text or ""
    strip = _value(config, "strip_header_lines", 0) or 0
    if isinstance(strip, int) and not isinstance(strip, bool) and strip > 0:
        body = "\n".join(body.splitlines()[strip:])
    for pattern in _value(config, "remove_patterns", []) or []:
        try:
            body = re.sub(pattern, "", body)
        except re.error:
            # Invalid patterns are rejected when the template is saved; keep the
            # original text if an old row somehow contains one.
            continue
    if _value(config, "remove_urls", False):
        body = URL_RE.sub("", body)
    for item in _value(config, "replacements", []) or []:
        body = body.replace(item["from"], item["to"])
    body = body.strip()
    if not body and _value(config, "drop_empty", True):
        return None

    when = (
        sent_at.isoformat()
        if hasattr(sent_at, "isoformat")
        else (str(sent_at) if sent_at else "")
    )
    context = {
        "text": body,
        "source": source or "",
        "account": account or "",
        "author": author or "",
        "sent_at": when,
        "date": when,
        "message_id": message_id if message_id is not None else "",
        "peer_id": peer_id if peer_id is not None else "",
    }
    template_body = _value(config, "body", "") or ""
    rendered = render_body(template_body, context) if template_body else body

    header = _value(config, "header", "") or ""
    footer = _value(config, "footer", "") or ""
    parts = [part for part in (header, rendered, footer) if part]
    result = "\n".join(parts).strip()
    if not result:
        return None
    return result[:MAX_BODY_LENGTH]
