import asyncio
import signal
from django.core.management.base import BaseCommand
from relay.services.worker import TelegramRelayWorker

class Command(BaseCommand):
    help = "Run the long-lived Telegram MTProto relay worker"
    def handle(self, *args, **options):
        asyncio.run(self._run())
    async def _run(self):
        worker = TelegramRelayWorker(); loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, worker.stop)
        await worker.run()
