import asyncio
import logging
import os
from asgiref.sync import sync_to_async
from django.db import IntegrityError
from django.utils import timezone
from telethon import TelegramClient, events
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from relay.models import ProcessedMessage, RelayConfiguration, TelegramAccount
from .crypto import SecretBox
from .processing import MessageProcessor, TelegramMessage

logger = logging.getLogger(__name__)

class TelegramRelayWorker:
    def __init__(self):
        self.clients = []; self.stop_event = asyncio.Event(); self.processor = MessageProcessor()
        self.api_id = int(os.environ["TELEGRAM_API_ID"]); self.api_hash = os.environ["TELEGRAM_API_HASH"]

    async def run(self):
        accounts = [a async for a in TelegramAccount.objects.filter(status=TelegramAccount.Status.CONNECTED).select_related("session")]
        logger.info("worker_start", extra={"account_count": len(accounts), "request_id": ""})
        for account in accounts:
            try: await self._start_account(account)
            except Exception as exc:
                logger.exception("account_connection_failed", extra={"account_id": str(account.id), "request_id": ""})
                await TelegramAccount.objects.filter(pk=account.pk).aupdate(status=TelegramAccount.Status.ERROR, last_error=str(exc)[:500])
        await self.stop_event.wait()
        await asyncio.gather(*(client.disconnect() for client in self.clients), return_exceptions=True)

    async def _start_account(self, account):
        session = SecretBox().decrypt(account.session.encrypted_session)
        client = TelegramClient(StringSession(session), self.api_id, self.api_hash, auto_reconnect=True, connection_retries=None, retry_delay=2)
        @client.on(events.NewMessage())
        async def handler(event): await self._handle(account, client, event)
        await client.connect()
        if not await client.is_user_authorized(): raise RuntimeError("Telegram session is no longer authorized")
        self.clients.append(client)
        await TelegramAccount.objects.filter(pk=account.pk).aupdate(last_connected_at=timezone.now(), last_error="")

    async def _handle(self, account, client, event):
        chat_id = event.chat_id
        relays = [r async for r in RelayConfiguration.objects.filter(account=account, source__peer_id=chat_id, enabled=True).select_related("destination")]
        for relay in relays:
            row = await self._claim(account, relay, chat_id, event.id)
            if not row: continue
            result = self.processor.process(TelegramMessage(chat_id, event.id, event.raw_text or ""), relay.rules)
            if not result.accepted:
                row.status = ProcessedMessage.Status.FILTERED; await row.asave(update_fields=["status", "updated_at"]); continue
            try:
                sent = await client.send_message(self._input_peer(relay.destination), result.text, link_preview=False)
                row.status = ProcessedMessage.Status.PUBLISHED; row.destination_message_id = sent.id
            except FloodWaitError as exc:
                row.status = ProcessedMessage.Status.FAILED; row.error_code = "FLOOD_WAIT"; row.error_message = f"Retry after {exc.seconds} seconds"
            except RPCError as exc:
                row.status = ProcessedMessage.Status.FAILED; row.error_code = exc.__class__.__name__; row.error_message = str(exc)[:500]
            except Exception as exc:
                logger.exception("relay_publish_failed", extra={"relay_id": str(relay.id), "request_id": ""}); row.status = ProcessedMessage.Status.FAILED; row.error_code = "UNEXPECTED"; row.error_message = str(exc)[:500]
            await row.asave()

    @sync_to_async
    def _claim(self, account, relay, chat_id, message_id):
        try: return ProcessedMessage.objects.create(account=account, relay=relay, source_chat_id=chat_id, source_message_id=message_id, status=ProcessedMessage.Status.PROCESSING)
        except IntegrityError: return None

    def stop(self): self.stop_event.set()

    def _input_peer(self, entity):
        raw_id = abs(entity.peer_id)
        access_hash = int(SecretBox().decrypt(entity.access_hash_encrypted)) if entity.access_hash_encrypted else None
        if "channel" in entity.entity_type:
            if access_hash is None: raise RuntimeError("Destination access metadata is missing")
            # Marked channel IDs are -100<channel id>.
            channel_id = int(str(abs(entity.peer_id))[3:]) if str(abs(entity.peer_id)).startswith("100") else raw_id
            return InputPeerChannel(channel_id, access_hash)
        if "user" in entity.entity_type:
            if access_hash is None: raise RuntimeError("Destination access metadata is missing")
            return InputPeerUser(raw_id, access_hash)
        return InputPeerChat(raw_id)
