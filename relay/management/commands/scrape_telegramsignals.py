from django.core.management.base import BaseCommand

from relay.models import SignalSource
from relay.services.scrape_queue import enqueue_scrape


class Command(BaseCommand):
    help = "Queue history backfills for signal sources; the Telegram worker performs them"

    def add_arguments(self, parser):
        parser.add_argument("--account", help="Only queue sources for this account id")
        parser.add_argument("--source", help="Only queue this signal source id")
        parser.add_argument("--limit", type=int, default=200, help="Messages to read per source")

    def handle(self, *args, **options):
        sources = SignalSource.objects.select_related("account").filter(enabled=True)
        if options.get("source"):
            sources = sources.filter(pk=options["source"])
        if options.get("account"):
            sources = sources.filter(account_id=options["account"])
        limit = options["limit"]
        queued = 0
        for source in sources:
            if limit and source.backfill_limit != limit:
                source.backfill_limit = limit
                source.save(update_fields=["backfill_limit", "updated_at"])
            enqueue_scrape(source.account_id, source.id)
            queued += 1
        self.stdout.write(self.style.SUCCESS(f"Queued {queued} signal source backfill(s)"))
