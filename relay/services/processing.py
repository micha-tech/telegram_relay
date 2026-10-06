import re
from dataclasses import dataclass

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

@dataclass(frozen=True)
class TelegramMessage:
    chat_id: int
    message_id: int
    text: str

@dataclass(frozen=True)
class ProcessingResult:
    accepted: bool
    text: str
    reason: str | None = None

class MessageProcessor:
    """Pure, deterministic pipeline; safe to test without Telegram."""
    def process(self, message: TelegramMessage, rules: dict) -> ProcessingResult:
        text = message.text or ""
        for phrase in rules.get("blocked_phrases", []):
            if phrase.casefold() in text.casefold(): return ProcessingResult(False, "", "blocked_phrase")
        if rules.get("remove_urls"): text = URL_RE.sub("", text)
        source_name = rules.get("source_name")
        if rules.get("remove_source_name") and source_name:
            text = re.sub(re.escape(source_name), "", text, flags=re.IGNORECASE)
        for replacement in rules.get("replacements", []):
            old, new = replacement.get("from", ""), replacement.get("to", "")
            if old: text = text.replace(old, new)
        if rules.get("prefix"): text = f"{rules['prefix']}\n{text}"
        if rules.get("suffix"): text = f"{text}\n{rules['suffix']}"
        text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text and rules.get("drop_empty", True): return ProcessingResult(False, "", "empty")
        return ProcessingResult(True, text)
