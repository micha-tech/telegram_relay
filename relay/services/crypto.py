import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

KEY_SEPARATOR = ","


def _fernet_key(raw: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(raw.encode())
    except Exception:
        decoded = b""
    if len(decoded) == 32:
        return raw.encode()
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


class SecretBox:
    """Encrypts credentials with the primary key while still decrypting older keys.

    ``TELEGRAM_SESSION_ENCRYPTION_KEY`` is the primary key used for encryption.
    ``TELEGRAM_SESSION_ENCRYPTION_KEYS`` may list additional retired keys
    (comma-separated, oldest first) so rotated credentials stay readable.
    ``key_version`` is the 1-based position of the key that successfully decrypts
    a value; 1 means the current primary key.
    """

    def __init__(self, key: str | None = None):
        if key is not None:
            raw_keys = [key]
        else:
            primary = os.environ.get("TELEGRAM_SESSION_ENCRYPTION_KEY", "")
            previous = [
                value.strip()
                for value in os.environ.get("TELEGRAM_SESSION_ENCRYPTION_KEYS", "").split(
                    KEY_SEPARATOR
                )
                if value.strip()
            ]
            if not primary:
                if os.environ.get("DEBUG", "false").lower() != "true":
                    raise RuntimeError("TELEGRAM_SESSION_ENCRYPTION_KEY is required")
                primary = "development-only-key"
            raw_keys = previous + [primary]
        self._fernets = [Fernet(_fernet_key(raw)) for raw in raw_keys]
        self.key_version = len(self._fernets)

    def encrypt(self, value: str) -> bytes:
        return self._fernets[-1].encrypt(value.encode())

    def decrypt(self, value: bytes) -> str:
        token = bytes(value)
        for fernet in self._fernets:
            try:
                return fernet.decrypt(token).decode()
            except InvalidToken:
                continue
        raise RuntimeError("Encrypted credential cannot be decrypted")

    def decrypt_versioned(self, value: bytes, key_version: int | None = None) -> str:
        if key_version:
            index = key_version - 1
            if 0 <= index < len(self._fernets):
                try:
                    return self._fernets[index].decrypt(bytes(value)).decode()
                except InvalidToken:
                    pass
        return self.decrypt(value)
