from relay.models import TelegramAccount, TelegramSession

from .crypto import SecretBox


class TelegramSessionStore:
    def __init__(self, box=None):
        self.box = box or SecretBox()

    def save_session(self, account: TelegramAccount, session: str):
        return TelegramSession.objects.update_or_create(
            account=account,
            defaults={
                "encrypted_session": self.box.encrypt(session),
                "key_version": self.box.key_version,
            },
        )[0]

    def load_session(self, account: TelegramAccount) -> str | None:
        try:
            row = account.session
        except TelegramSession.DoesNotExist:
            return None
        return self.box.decrypt_versioned(row.encrypted_session, row.key_version)

    def delete_session(self, account):
        TelegramSession.objects.filter(account=account).delete()

    def session_exists(self, account):
        return TelegramSession.objects.filter(account=account).exists()

    async def asave_session(self, account: TelegramAccount, session: str):
        return (
            await TelegramSession.objects.aupdate_or_create(
                account=account,
                defaults={
                    "encrypted_session": self.box.encrypt(session),
                    "key_version": self.box.key_version,
                },
            )
        )[0]

    async def aload_session(self, account: TelegramAccount) -> str | None:
        try:
            row = await TelegramSession.objects.aget(account=account)
        except TelegramSession.DoesNotExist:
            return None
        return self.box.decrypt_versioned(row.encrypted_session, row.key_version)
