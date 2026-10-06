import os
from asgiref.sync import async_to_sync
from asgiref.sync import sync_to_async
from telethon import TelegramClient, utils
from telethon.sessions import StringSession
from relay.models import TelegramEntity
from .crypto import SecretBox

class TelegramEntityService:
    def list_dialogs(self, account): return async_to_sync(self._list_dialogs)(account)
    async def _list_dialogs(self, account):
        # Access through an async ORM query to avoid blocking the event loop.
        row = await account.__class__.objects.select_related("session").aget(pk=account.pk)
        session = SecretBox().decrypt(row.session.encrypted_session)
        client = TelegramClient(StringSession(session), int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"])
        await client.connect()
        try:
            result = []
            async for dialog in client.iter_dialogs():
                peer_id = utils.get_peer_id(dialog.entity)
                entity_type = dialog.entity.__class__.__name__.lower()
                access_hash = getattr(dialog.entity, "access_hash", None)
                common = {"user": account.user, "account": account, "peer_id": peer_id}
                defaults = {"entity_type": entity_type, "username": getattr(dialog.entity, "username", None) or "", "title": dialog.name or "", "access_hash_encrypted": SecretBox().encrypt(str(access_hash)) if access_hash is not None else None}
                source, _ = await sync_to_async(TelegramEntity.objects.update_or_create)(**common, kind=TelegramEntity.Kind.SOURCE, defaults=defaults)
                destination, _ = await sync_to_async(TelegramEntity.objects.update_or_create)(**common, kind=TelegramEntity.Kind.DESTINATION, defaults=defaults)
                result.append({"peer_id": peer_id, "entity_type": entity_type, "username": defaults["username"], "title": defaults["title"], "source_id": str(source.id), "destination_id": str(destination.id)})
            return result
        finally: await client.disconnect()
