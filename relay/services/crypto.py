import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken

class SecretBox:
    def __init__(self, key: str | None = None):
        raw = key or os.environ.get("TELEGRAM_SESSION_ENCRYPTION_KEY", "")
        if not raw:
            if os.environ.get("DEBUG", "false").lower() != "true":
                raise RuntimeError("TELEGRAM_SESSION_ENCRYPTION_KEY is required")
            raw = "development-only-key"
        try: decoded = base64.urlsafe_b64decode(raw.encode())
        except Exception: decoded = b""
        fernet_key = raw.encode() if len(decoded) == 32 else base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        self._fernet = Fernet(fernet_key)
    def encrypt(self, value: str) -> bytes: return self._fernet.encrypt(value.encode())
    def decrypt(self, value: bytes) -> str:
        try: return self._fernet.decrypt(bytes(value)).decode()
        except InvalidToken as exc: raise RuntimeError("Encrypted credential cannot be decrypted") from exc

