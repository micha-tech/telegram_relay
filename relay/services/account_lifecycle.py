import logging
import os

from asgiref.sync import async_to_sync
from django.utils import timezone
from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.sessions import StringSession

from relay.models import AccountConnectionEvent, AuditLog, TelegramAccount

from .redis import RedisService
from .session_store import TelegramSessionStore

logger = logging.getLogger(__name__)


class AccountLifecycleError(Exception):
    pass


class AccountLifecycleService:
    """User-triggered disconnect/reconnect with safe credential handling."""

    def __init__(self, store=None):
        self.store = store or TelegramSessionStore()

    def disconnect(self, account: TelegramAccount, request_id=""):
        session = self.store.load_session(account)
        if session:
            async_to_sync(self._revoke)(session)
        self.store.delete_session(account)
        account.status = TelegramAccount.Status.DISCONNECTED
        account.last_error = ""
        account.save(update_fields=["status", "last_error", "updated_at"])
        AccountConnectionEvent.objects.create(
            account=account,
            kind=AccountConnectionEvent.Kind.DISCONNECTED,
            detail="Session deleted by owner",
            request_id=request_id[:64],
        )
        AuditLog.objects.create(
            user=account.user,
            action="telegram.account.disconnected",
            object_type="telegram_account",
            object_id=account.pk,
            request_id=request_id,
        )
        return account

    async def _revoke(self, session: str):
        """Best-effort remote logout so the auth key stops working in Telegram."""
        client = TelegramClient(
            StringSession(session),
            int(os.environ["TELEGRAM_API_ID"]),
            os.environ["TELEGRAM_API_HASH"],
        )
        try:
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()
        except (OSError, RPCError, ValueError) as exc:
            logger.warning(
                "telegram_logout_failed", extra={"error": exc.__class__.__name__}
            )
        finally:
            await client.disconnect()

    def reconnect(self, account: TelegramAccount, request_id=""):
        if not self.store.session_exists(account):
            raise AccountLifecycleError("No stored session, connect the account again")
        if account.status == TelegramAccount.Status.INVALID:
            raise AccountLifecycleError(
                "The stored session was revoked by Telegram, connect the account again"
            )
        account.status = TelegramAccount.Status.CONNECTED
        account.last_error = ""
        account.save(update_fields=["status", "last_error", "updated_at"])
        AuditLog.objects.create(
            user=account.user,
            action="telegram.account.reconnect_requested",
            object_type="telegram_account",
            object_id=account.pk,
            request_id=request_id,
        )
        return account

    def health(self, account: TelegramAccount):
        redis = RedisService()
        try:
            worker_active = bool(redis.client.exists(f"relay:account-owner:{account.pk}"))
        except Exception:
            worker_active = None
        return {
            "status": account.status,
            "session_present": self.store.session_exists(account),
            "worker_active": worker_active,
            "last_connected_at": account.last_connected_at,
            "last_activity_at": account.last_activity_at,
            "last_error": account.last_error,
            "checked_at": timezone.now(),
        }
