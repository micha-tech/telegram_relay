from django.core.management.base import BaseCommand

from relay.services.telegram_auth import cleanup_expired_challenges


class Command(BaseCommand):
    help = "Expire stale Telegram authentication challenges and delete old rows"

    def handle(self, *args, **options):
        cleanup_expired_challenges()
        self.stdout.write(self.style.SUCCESS("Expired Telegram authentication challenges cleaned"))
