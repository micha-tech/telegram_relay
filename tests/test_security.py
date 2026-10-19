import pytest

from relay.services.crypto import SecretBox


def test_secret_box_encrypts_and_round_trips_credentials():
    box = SecretBox("unit-test-encryption-key")
    plaintext = "sensitive-telegram-session"
    ciphertext = box.encrypt(plaintext)
    assert plaintext.encode() not in ciphertext
    assert box.decrypt(ciphertext) == plaintext


def test_secret_box_rejects_wrong_key():
    ciphertext = SecretBox("first-key").encrypt("secret")
    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        SecretBox("second-key").decrypt(ciphertext)


def test_secret_box_key_rotation_decrypts_old_and_new(monkeypatch):
    old_ciphertext = SecretBox("old-encryption-key").encrypt("session")
    monkeypatch.setenv("TELEGRAM_SESSION_ENCRYPTION_KEY", "new-encryption-key")
    monkeypatch.setenv("TELEGRAM_SESSION_ENCRYPTION_KEYS", "old-encryption-key")
    box = SecretBox()
    assert box.key_version == 2
    assert box.decrypt(old_ciphertext) == "session"
    assert box.decrypt_versioned(old_ciphertext, key_version=1) == "session"
    new_ciphertext = box.encrypt("session")
    assert box.decrypt(new_ciphertext) == "session"
    assert box.decrypt_versioned(new_ciphertext, key_version=2) == "session"


def test_secret_box_ignores_stale_key_version(monkeypatch):
    ciphertext = SecretBox("only-key").encrypt("session")
    monkeypatch.setenv("TELEGRAM_SESSION_ENCRYPTION_KEY", "only-key")
    box = SecretBox()
    assert box.decrypt_versioned(ciphertext, key_version=99) == "session"


@pytest.mark.django_db
def test_session_store_round_trip_and_persisted_key_version(account, telegram_env):
    from relay.models import TelegramSession
    from relay.services.session_store import TelegramSessionStore

    store = TelegramSessionStore(SecretBox("test-encryption-key"))
    store.save_session(account, "session-string")
    row = TelegramSession.objects.get(account=account)
    assert row.key_version == 1
    assert b"session-string" not in bytes(row.encrypted_session)
    assert store.load_session(account) == "session-string"


@pytest.mark.django_db
def test_session_store_missing_session_returns_none(account, telegram_env):
    from relay.services.session_store import TelegramSessionStore

    assert TelegramSessionStore(SecretBox("test-encryption-key")).load_session(account) is None


@pytest.mark.django_db
def test_session_deletion(account, telegram_env):
    from relay.services.session_store import TelegramSessionStore

    store = TelegramSessionStore(SecretBox("test-encryption-key"))
    store.save_session(account, "session-string")
    assert store.session_exists(account)
    store.delete_session(account)
    assert not store.session_exists(account)


def test_secret_box_requires_key_outside_development(monkeypatch):
    monkeypatch.delenv("TELEGRAM_SESSION_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("DEBUG", "false")
    with pytest.raises(RuntimeError, match="TELEGRAM_SESSION_ENCRYPTION_KEY"):
        SecretBox()
