import hashlib
import logging
import os
from datetime import timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.db import IntegrityError, transaction
from django.utils import timezone
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    RPCError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from relay.models import (
    AccountConnectionEvent,
    AuthenticationChallenge,
    TelegramAccount,
    TelegramSession,
)

from .crypto import SecretBox
from .redis import RedisService

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
CHALLENGE_TTL = timedelta(minutes=10)


class TelegramAuthError(Exception):
    pass


def record_event(account, kind, detail="", request_id=""):
    AccountConnectionEvent.objects.create(
        account=account, kind=kind, detail=detail[:255], request_id=request_id[:64]
    )


def cleanup_expired_challenges():
    """Expire stale pending challenges and drop long-dead rows."""
    now = timezone.now()
    AuthenticationChallenge.objects.filter(
        state__in=[
            AuthenticationChallenge.State.CODE_SENT,
            AuthenticationChallenge.State.PASSWORD_REQUIRED,
        ],
        expires_at__lte=now,
    ).update(state=AuthenticationChallenge.State.EXPIRED)
    AuthenticationChallenge.objects.filter(
        state__in=[
            AuthenticationChallenge.State.FAILED,
            AuthenticationChallenge.State.EXPIRED,
            AuthenticationChallenge.State.COMPLETE,
        ],
        updated_at__lte=now - timedelta(days=7),
    ).delete()


class TelegramAuthService:
    def __init__(self, box=None):
        self.box = box or SecretBox()
        self.redis = RedisService()
        self.api_id = int(os.environ["TELEGRAM_API_ID"])
        self.api_hash = os.environ["TELEGRAM_API_HASH"]

    async def _client(self, session=""):
        client = TelegramClient(StringSession(session), self.api_id, self.api_hash)
        await client.connect()
        return client

    def start(self, user, phone_number, request_id=""):
        phone_digest = hashlib.sha256(phone_number.encode()).hexdigest()
        with self.redis.lock(f"telegram-auth-start:{user.pk}:{phone_digest}"):
            return async_to_sync(self._start)(user, phone_number, request_id)

    async def _start(self, user, phone_number, request_id=""):
        account, _ = await TelegramAccount.objects.aget_or_create(
            user=user, phone_number=phone_number
        )
        await AuthenticationChallenge.objects.filter(
            user=user,
            account=account,
            state__in=[
                AuthenticationChallenge.State.CODE_SENT,
                AuthenticationChallenge.State.PASSWORD_REQUIRED,
            ],
        ).aupdate(state=AuthenticationChallenge.State.EXPIRED)
        try:
            client = await self._client()
        except (OSError, RPCError) as exc:
            logger.warning("telegram_unavailable", extra={"error": exc.__class__.__name__})
            raise TelegramAuthError("Telegram is temporarily unavailable, try again") from exc
        try:
            try:
                sent = await client.send_code_request(phone_number)
            except FloodWaitError as exc:
                raise TelegramAuthError(
                    f"Too many requests, try again in {exc.seconds} seconds"
                ) from exc
            except (OSError, RPCError) as exc:
                logger.warning("telegram_unavailable", extra={"error": exc.__class__.__name__})
                raise TelegramAuthError(
                    "Telegram is temporarily unavailable, try again"
                ) from exc
            challenge = await AuthenticationChallenge.objects.acreate(
                user=user,
                account=account,
                encrypted_session=self.box.encrypt(client.session.save()),
                encrypted_phone_code_hash=self.box.encrypt(sent.phone_code_hash),
                expires_at=timezone.now() + CHALLENGE_TTL,
            )
            await sync_to_async(record_event)(
                account, AccountConnectionEvent.Kind.CODE_SENT, "Login code requested", request_id
            )
            return challenge
        finally:
            await client.disconnect()

    def verify(self, user, challenge_id, code, request_id=""):
        with self.redis.lock(f"telegram-auth:{challenge_id}"):
            return async_to_sync(self._verify)(user, challenge_id, code, request_id)

    async def _verify(self, user, challenge_id, code, request_id=""):
        challenge = await self._load_challenge(user, challenge_id)
        self._validate(challenge)
        client = await self._client(self.box.decrypt(challenge.encrypted_session))
        try:
            try:
                await client.sign_in(
                    challenge.account.phone_number,
                    code,
                    phone_code_hash=self.box.decrypt(challenge.encrypted_phone_code_hash),
                )
            except SessionPasswordNeededError:
                challenge.state = AuthenticationChallenge.State.PASSWORD_REQUIRED
                challenge.attempts += 1
                await challenge.asave(update_fields=["state", "attempts", "updated_at"])
                await sync_to_async(record_event)(
                    challenge.account,
                    AccountConnectionEvent.Kind.PASSWORD_REQUIRED,
                    "2FA password required",
                    request_id,
                )
                return challenge
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
                challenge.attempts += 1
                if isinstance(exc, PhoneCodeExpiredError) or challenge.attempts >= MAX_ATTEMPTS:
                    challenge.state = AuthenticationChallenge.State.FAILED
                await challenge.asave(update_fields=["state", "attempts", "updated_at"])
                raise TelegramAuthError("The authentication code is invalid or expired") from exc
            except FloodWaitError as exc:
                raise TelegramAuthError(
                    f"Too many requests, try again in {exc.seconds} seconds"
                ) from exc
            except (OSError, RPCError) as exc:
                logger.warning("telegram_unavailable", extra={"error": exc.__class__.__name__})
                raise TelegramAuthError(
                    "Telegram is temporarily unavailable, try again"
                ) from exc
            return await self._complete(challenge, client, request_id)
        finally:
            await client.disconnect()

    def verify_2fa(self, user, challenge_id, password, request_id=""):
        with self.redis.lock(f"telegram-auth:{challenge_id}"):
            return async_to_sync(self._verify_2fa)(user, challenge_id, password, request_id)

    async def _verify_2fa(self, user, challenge_id, password, request_id=""):
        challenge = await self._load_challenge(user, challenge_id)
        self._validate(challenge, AuthenticationChallenge.State.PASSWORD_REQUIRED)
        client = await self._client(self.box.decrypt(challenge.encrypted_session))
        try:
            try:
                await client.sign_in(password=password)
            except PasswordHashInvalidError as exc:
                challenge.attempts += 1
                if challenge.attempts >= MAX_ATTEMPTS:
                    challenge.state = AuthenticationChallenge.State.FAILED
                await challenge.asave(update_fields=["state", "attempts", "updated_at"])
                raise TelegramAuthError("The 2FA password is invalid") from exc
            except FloodWaitError as exc:
                raise TelegramAuthError(
                    f"Too many requests, try again in {exc.seconds} seconds"
                ) from exc
            except (OSError, RPCError) as exc:
                logger.warning("telegram_unavailable", extra={"error": exc.__class__.__name__})
                raise TelegramAuthError(
                    "Telegram is temporarily unavailable, try again"
                ) from exc
            return await self._complete(challenge, client, request_id)
        finally:
            await client.disconnect()

    async def _load_challenge(self, user, challenge_id):
        try:
            return await AuthenticationChallenge.objects.select_related("account").aget(
                id=challenge_id, user=user
            )
        except AuthenticationChallenge.DoesNotExist as exc:
            raise TelegramAuthError("Challenge is invalid or expired") from exc

    def _validate(self, challenge, state=AuthenticationChallenge.State.CODE_SENT):
        if (
            challenge.state != state
            or challenge.expires_at <= timezone.now()
            or challenge.attempts >= MAX_ATTEMPTS
        ):
            raise TelegramAuthError("Challenge is invalid or expired")

    async def _complete(self, challenge, client, request_id=""):
        me = await client.get_me()
        session = client.session.save()
        return await self._persist_complete(
            challenge.id,
            session,
            me.id,
            " ".join(filter(None, [me.first_name, me.last_name])),
            me.username or "",
            request_id,
        )

    @sync_to_async
    def _persist_complete(
        self, challenge_id, session, telegram_user_id, display_name, username, request_id=""
    ):
        with transaction.atomic():
            challenge = (
                AuthenticationChallenge.objects.select_for_update()
                .select_related("account")
                .get(pk=challenge_id)
            )
            if challenge.state not in {
                AuthenticationChallenge.State.CODE_SENT,
                AuthenticationChallenge.State.PASSWORD_REQUIRED,
            }:
                raise TelegramAuthError("Challenge is invalid or expired")
            encrypted = self.box.encrypt(session)
            account = challenge.account
            try:
                with transaction.atomic():
                    TelegramSession.objects.update_or_create(
                        account=account,
                        defaults={
                            "encrypted_session": encrypted,
                            "key_version": self.box.key_version,
                        },
                    )
                    account.telegram_user_id = telegram_user_id
                    account.display_name = display_name
                    account.username = username
                    account.status = TelegramAccount.Status.CONNECTED
                    account.last_connected_at = timezone.now()
                    account.last_activity_at = timezone.now()
                    account.last_error = ""
                    account.save()
            except IntegrityError as exc:
                account = self._merge_duplicate_identity(
                    challenge,
                    account,
                    session,
                    telegram_user_id,
                    display_name,
                    username,
                )
                if account is None:
                    raise exc
            record_event(account, AccountConnectionEvent.Kind.CONNECTED, request_id=request_id)
            challenge.state = AuthenticationChallenge.State.COMPLETE
            challenge.consumed_at = timezone.now()
            challenge.save()
            return challenge

    def _merge_duplicate_identity(
        self, challenge, pending_account, session, telegram_user_id, display_name, username
    ):
        """Reconnect flow: the same Telegram identity under a new phone number.

        The unique (user, telegram_user_id) constraint fires when an owner
        reconnects an already-known Telegram user with a different phone.
        Move the fresh session onto the existing account row instead of
        creating a second identity.
        """
        existing = (
            TelegramAccount.objects.select_for_update()
            .filter(user=pending_account.user, telegram_user_id=telegram_user_id)
            .exclude(pk=pending_account.pk)
            .first()
        )
        if existing is None:
            return None
        new_phone = pending_account.phone_number
        TelegramSession.objects.update_or_create(
            account=existing,
            defaults={
                "encrypted_session": self.box.encrypt(session),
                "key_version": self.box.key_version,
            },
        )
        challenge.account = existing
        challenge.save(update_fields=["account", "updated_at"])
        pending_account.delete()
        existing.phone_number = new_phone
        existing.display_name = display_name
        existing.username = username
        existing.status = TelegramAccount.Status.CONNECTED
        existing.last_connected_at = timezone.now()
        existing.last_activity_at = timezone.now()
        existing.last_error = ""
        existing.save()
        return existing
