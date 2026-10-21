import asyncio
import logging
import os
from contextlib import suppress
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import IntegrityError
from django.utils import timezone
from redis.asyncio import Redis
from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser

from relay.models import (
    ProcessedMessage,
    RelayConfiguration,
    SignalSource,
    TelegramAccount,
)

from .crypto import SecretBox
from .processing import MessageProcessor, TelegramMessage
from .scrape_queue import scrape_queue_key
from .session_store import TelegramSessionStore
from .signals import capture
from .telegram_auth import record_event

logger = logging.getLogger(__name__)


class TelegramRelayWorker:
    """Owns bounded listeners for all accounts assigned to this worker."""

    poll_interval_seconds = 15
    event_queue_size = 1_000
    consumers_per_account = 1
    lock_timeout_seconds = 90
    max_backoff_seconds = 300
    cleanup_interval_seconds = 300

    def __init__(self):
        self.stop_event = asyncio.Event()
        self.processor = MessageProcessor()
        self.api_id = int(os.environ["TELEGRAM_API_ID"])
        self.api_hash = os.environ["TELEGRAM_API_HASH"]
        self.redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        self.poll_interval_seconds = int(
            os.environ.get("WORKER_POLL_INTERVAL_SECONDS", self.poll_interval_seconds)
        )
        self.max_backoff_seconds = int(
            os.environ.get("WORKER_MAX_BACKOFF_SECONDS", self.max_backoff_seconds)
        )
        self.account_tasks: dict[UUID, asyncio.Task] = {}
        self.backoff_until: dict[UUID, float] = {}
        self.failures: dict[UUID, int] = {}
        self._last_cleanup = 0.0

    async def run(self):
        logger.info("worker_started")
        try:
            while not self.stop_event.is_set():
                await self._reconcile_accounts()
                await self._maybe_cleanup()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self.stop_event.wait(), timeout=self.poll_interval_seconds
                    )
        finally:
            for task in self.account_tasks.values():
                task.cancel()
            await asyncio.gather(*self.account_tasks.values(), return_exceptions=True)
            await self.redis.aclose()
            logger.info("worker_stopped")

    async def _maybe_cleanup(self):
        loop = asyncio.get_running_loop()
        if loop.time() - self._last_cleanup < self.cleanup_interval_seconds:
            return
        self._last_cleanup = loop.time()
        await sync_to_async(self._cleanup)()

    @staticmethod
    def _cleanup():
        from .telegram_auth import cleanup_expired_challenges

        cleanup_expired_challenges()

    async def _reconcile_accounts(self):
        desired_ids = {
            account_id
            async for account_id in TelegramAccount.objects.filter(
                status=TelegramAccount.Status.CONNECTED,
                session__isnull=False,
            ).values_list("id", flat=True)
        }
        loop = asyncio.get_running_loop()
        for account_id, task in list(self.account_tasks.items()):
            if task.done():
                with suppress(asyncio.CancelledError, Exception):
                    task.result()
                del self.account_tasks[account_id]
            elif account_id not in desired_ids:
                task.cancel()
        for account_id in desired_ids - self.account_tasks.keys():
            deadline = self.backoff_until.get(account_id, 0.0)
            if loop.time() < deadline:
                continue
            self.account_tasks[account_id] = asyncio.create_task(
                self._run_account(account_id), name=f"telegram-account-{account_id}"
            )

    def _record_failure(self, account_id: UUID, detail: str):
        self.failures[account_id] = self.failures.get(account_id, 0) + 1
        delay = min(2**self.failures[account_id], self.max_backoff_seconds)
        self.backoff_until[account_id] = asyncio.get_running_loop().time() + delay
        logger.warning(
            "account_task_failed",
            extra={"account_id": str(account_id), "failures": self.failures[account_id]},
        )
        return detail

    def _record_success(self, account_id: UUID):
        self.failures.pop(account_id, None)
        self.backoff_until.pop(account_id, None)

    async def _run_account(self, account_id: UUID):
        lock = self.redis.lock(
            f"relay:account-owner:{account_id}",
            timeout=self.lock_timeout_seconds,
            blocking_timeout=0,
        )
        if not await lock.acquire():
            logger.info("account_owned_by_another_worker", extra={"account_id": str(account_id)})
            return
        ownership_lost = asyncio.Event()
        renew_task = asyncio.create_task(self._renew_lock(lock, account_id, ownership_lost))
        client = None
        consumers = []
        try:
            account = await TelegramAccount.objects.aget(pk=account_id)
            session = await TelegramSessionStore().aload_session(account)
            if session is None:
                raise RuntimeError("Telegram account has no authenticated session")
            client = TelegramClient(
                StringSession(session),
                self.api_id,
                self.api_hash,
                auto_reconnect=True,
                connection_retries=None,
                retry_delay=2,
            )
            queue: asyncio.Queue = asyncio.Queue(maxsize=self.event_queue_size)

            @client.on(events.NewMessage())
            async def enqueue(event):
                await queue.put(event)

            await client.connect()
            if not await client.is_user_authorized():
                await TelegramAccount.objects.filter(pk=account_id).aupdate(
                    status=TelegramAccount.Status.INVALID,
                    last_error="Telegram session is no longer authorized",
                )
                await sync_to_async(record_event)(
                    account, "revoked", "Session no longer authorized"
                )
                return
            await TelegramSessionStore().asave_session(account, client.session.save())
            consumers = [
                asyncio.create_task(self._consume(account, client, queue))
                for _ in range(self.consumers_per_account)
            ]
            await TelegramAccount.objects.filter(pk=account_id).aupdate(
                last_connected_at=timezone.now(), last_error=""
            )
            await sync_to_async(record_event)(account, "connected")
            self._record_success(account_id)
            logger.info("account_connected", extra={"account_id": str(account_id)})
            while (
                not self.stop_event.is_set()
                and not ownership_lost.is_set()
                and client.is_connected()
            ):
                status = (
                    await TelegramAccount.objects.filter(pk=account_id)
                    .values_list("status", flat=True)
                    .afirst()
                )
                if status != TelegramAccount.Status.CONNECTED:
                    break
                await self._run_scrapes(account, client)
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("account_connection_failed", extra={"account_id": str(account_id)})
            detail = self._record_failure(account_id, exc.__class__.__name__)
            await TelegramAccount.objects.filter(pk=account_id).aupdate(last_error=detail)
            with suppress(Exception):
                await sync_to_async(record_event)(
                    await TelegramAccount.objects.aget(pk=account_id),
                    "error",
                    detail,
                )
        finally:
            for consumer in consumers:
                consumer.cancel()
            await asyncio.gather(*consumers, return_exceptions=True)
            if client is not None:
                await client.disconnect()
            renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await renew_task
            with suppress(Exception):
                await lock.release()

    async def _renew_lock(self, lock, account_id, ownership_lost):
        while True:
            await asyncio.sleep(self.lock_timeout_seconds / 3)
            try:
                await lock.extend(self.lock_timeout_seconds, replace_ttl=True)
            except Exception:
                logger.exception(
                    "account_lock_renewal_failed", extra={"account_id": str(account_id)}
                )
                ownership_lost.set()
                return

    async def _consume(self, account, client, queue):
        while True:
            event = await queue.get()
            try:
                await self._handle(account, client, event)
            except Exception:
                logger.exception(
                    "event_processing_failed",
                    extra={"account_id": str(account.id), "chat_id": event.chat_id},
                )
            finally:
                queue.task_done()

    async def _handle(self, account, client, event):
        chat_id = event.chat_id
        if chat_id is None:
            return
        relays = [
            relay
            async for relay in RelayConfiguration.objects.filter(
                account=account, source__peer_id=chat_id, enabled=True
            ).select_related("destination")
        ]
        for relay in relays:
            row = await self._claim(account, relay, chat_id, event.id)
            if row is None:
                continue
            result = self.processor.process(
                TelegramMessage(chat_id, event.id, event.raw_text or ""), relay.rules
            )
            if not result.accepted:
                row.status = ProcessedMessage.Status.FILTERED
                await row.asave(update_fields=["status", "updated_at"])
                continue
            try:
                sent = await client.send_message(
                    self._input_peer(relay.destination), result.text, link_preview=False
                )
                row.status = ProcessedMessage.Status.PUBLISHED
                row.destination_message_id = sent.id
            except FloodWaitError as exc:
                row.status = ProcessedMessage.Status.FAILED
                row.error_code = "FLOOD_WAIT"
                row.error_message = f"Retry after {exc.seconds} seconds"
            except RPCError as exc:
                row.status = ProcessedMessage.Status.FAILED
                row.error_code = exc.__class__.__name__
                row.error_message = exc.__class__.__name__
            except Exception as exc:
                logger.exception("relay_publish_failed", extra={"relay_id": str(relay.id)})
                row.status = ProcessedMessage.Status.FAILED
                row.error_code = "UNEXPECTED"
                row.error_message = str(exc)[:500]
            await row.asave()
            if row.status == ProcessedMessage.Status.PUBLISHED:
                await TelegramAccount.objects.filter(pk=account.pk).aupdate(
                    last_activity_at=timezone.now()
                )
        await self._capture_signals(account, chat_id, event)

    async def _capture_signals(self, account, chat_id, event):
        """Store keyword-matched messages from watched chats as signals."""
        sources = [
            source
            async for source in SignalSource.objects.filter(
                account=account, entity__peer_id=chat_id, enabled=True
            ).select_related("entity")
        ]
        if not sources:
            return
        text = getattr(event, "raw_text", "") or ""
        message = getattr(event, "message", None)
        author = getattr(message, "post_author", "") or ""
        author_id = getattr(event, "sender_id", None)
        sent_at = getattr(event, "date", None)
        for source in sources:
            await sync_to_async(capture)(
                account,
                source,
                chat_id=chat_id,
                message_id=event.id,
                text=text,
                author=author,
                author_id=author_id,
                sent_at=sent_at,
            )

    async def _run_scrapes(self, account, client):
        """Run queued history backfills for this account with its own client."""
        requests = await self.redis.lpop(scrape_queue_key(account.pk), 5)
        if not requests:
            return
        if isinstance(requests, str):
            requests = [requests]
        for source_id in requests:
            try:
                await self._scrape_source(account, client, source_id)
            except Exception:
                logger.exception(
                    "signal_scrape_failed", extra={"source_id": str(source_id)}
                )

    async def _scrape_source(self, account, client, source_id):
        source = (
            await SignalSource.objects.filter(pk=source_id, account=account)
            .select_related("entity")
            .afirst()
        )
        if source is None or not source.enabled:
            return
        peer = self._input_peer(source.entity)
        limit = source.backfill_limit or 200
        captured = 0
        async for message in client.iter_messages(peer, limit=limit):
            signal = await sync_to_async(capture)(
                account,
                source,
                chat_id=source.entity.peer_id,
                message_id=message.id,
                text=getattr(message, "raw_text", "") or "",
                author=getattr(message, "post_author", "") or "",
                author_id=getattr(message, "sender_id", None),
                sent_at=getattr(message, "date", None),
            )
            if signal is not None:
                captured += 1
        await SignalSource.objects.filter(pk=source.pk).aupdate(
            last_scraped_at=timezone.now()
        )
        logger.info(
            "signal_source_scraped",
            extra={"source_id": str(source.pk), "captured": captured},
        )

    @sync_to_async
    def _claim(self, account, relay, chat_id, message_id):
        try:
            return ProcessedMessage.objects.create(
                account=account,
                relay=relay,
                source_chat_id=chat_id,
                source_message_id=message_id,
                status=ProcessedMessage.Status.PROCESSING,
            )
        except IntegrityError:
            return None

    def stop(self):
        self.stop_event.set()

    def _input_peer(self, entity):
        raw_id, peer_type = utils.resolve_id(entity.peer_id)
        access_hash = (
            int(SecretBox().decrypt(entity.access_hash_encrypted))
            if entity.access_hash_encrypted
            else None
        )
        if peer_type.__name__ == "PeerChannel":
            if access_hash is None:
                raise RuntimeError("Destination access metadata is missing")
            return InputPeerChannel(raw_id, access_hash)
        if peer_type.__name__ == "PeerUser":
            if access_hash is None:
                raise RuntimeError("Destination access metadata is missing")
            return InputPeerUser(raw_id, access_hash)
        return InputPeerChat(raw_id)
