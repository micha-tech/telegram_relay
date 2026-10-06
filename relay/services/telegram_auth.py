import os
from datetime import timedelta
from asgiref.sync import async_to_sync
from django.utils import timezone
from telethon import TelegramClient
from telethon.errors import PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError, SessionPasswordNeededError
from telethon.sessions import StringSession
from relay.models import AuthenticationChallenge, TelegramAccount
from .crypto import SecretBox
from .session_store import TelegramSessionStore

class TelegramAuthError(Exception): pass

class TelegramAuthService:
    def __init__(self, box=None, store=None):
        self.box = box or SecretBox(); self.store = store or TelegramSessionStore(self.box)
        self.api_id = int(os.environ["TELEGRAM_API_ID"]); self.api_hash = os.environ["TELEGRAM_API_HASH"]
    async def _client(self, session=""):
        client = TelegramClient(StringSession(session), self.api_id, self.api_hash)
        await client.connect(); return client
    def start(self, user, phone_number): return async_to_sync(self._start)(user, phone_number)
    async def _start(self, user, phone_number):
        account, _ = await TelegramAccount.objects.aget_or_create(user=user, phone_number=phone_number)
        client = await self._client()
        try:
            sent = await client.send_code_request(phone_number)
            challenge = await AuthenticationChallenge.objects.acreate(user=user, account=account, encrypted_session=self.box.encrypt(client.session.save()), encrypted_phone_code_hash=self.box.encrypt(sent.phone_code_hash), expires_at=timezone.now() + timedelta(minutes=10))
            return challenge
        finally: await client.disconnect()
    def verify(self, user, challenge_id, code): return async_to_sync(self._verify)(user, challenge_id, code)
    async def _verify(self, user, challenge_id, code):
        challenge = await AuthenticationChallenge.objects.select_related("account").aget(id=challenge_id, user=user)
        self._validate(challenge)
        client = await self._client(self.box.decrypt(challenge.encrypted_session))
        try:
            try: await client.sign_in(challenge.account.phone_number, code, phone_code_hash=self.box.decrypt(challenge.encrypted_phone_code_hash))
            except SessionPasswordNeededError:
                challenge.state = AuthenticationChallenge.State.PASSWORD_REQUIRED; challenge.attempts += 1
                await challenge.asave(update_fields=["state", "attempts", "updated_at"]); return challenge
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
                challenge.attempts += 1
                if isinstance(exc, PhoneCodeExpiredError) or challenge.attempts >= 5: challenge.state = AuthenticationChallenge.State.FAILED
                await challenge.asave(update_fields=["state", "attempts", "updated_at"])
                raise TelegramAuthError("The authentication code is invalid or expired") from exc
            return await self._complete(challenge, client)
        finally: await client.disconnect()
    def verify_2fa(self, user, challenge_id, password): return async_to_sync(self._verify_2fa)(user, challenge_id, password)
    async def _verify_2fa(self, user, challenge_id, password):
        challenge = await AuthenticationChallenge.objects.select_related("account").aget(id=challenge_id, user=user)
        self._validate(challenge, AuthenticationChallenge.State.PASSWORD_REQUIRED)
        client = await self._client(self.box.decrypt(challenge.encrypted_session))
        try:
            try: await client.sign_in(password=password)
            except PasswordHashInvalidError as exc:
                challenge.attempts += 1
                if challenge.attempts >= 5: challenge.state = AuthenticationChallenge.State.FAILED
                await challenge.asave(update_fields=["state", "attempts", "updated_at"])
                raise TelegramAuthError("The 2FA password is invalid") from exc
            return await self._complete(challenge, client)
        finally: await client.disconnect()
    def _validate(self, challenge, state=AuthenticationChallenge.State.CODE_SENT):
        if challenge.state != state or challenge.expires_at <= timezone.now() or challenge.attempts >= 5: raise TelegramAuthError("Challenge is invalid or expired")
    async def _complete(self, challenge, client):
        me = await client.get_me(); session = client.session.save()
        # ORM encryption is intentionally explicit in async context.
        from relay.models import TelegramSession
        await TelegramSession.objects.aupdate_or_create(account=challenge.account, defaults={"encrypted_session": self.box.encrypt(session)})
        account = challenge.account; account.telegram_user_id = me.id; account.display_name = " ".join(filter(None, [me.first_name, me.last_name])); account.username = me.username or ""; account.status = TelegramAccount.Status.CONNECTED; account.last_connected_at = timezone.now()
        await account.asave(); challenge.state = AuthenticationChallenge.State.COMPLETE; challenge.consumed_at = timezone.now(); await challenge.asave()
        return challenge
