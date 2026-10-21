from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Grant super administrator rights to an existing account"

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email address of the account to promote")

    def handle(self, *args, **options):
        email = (options["email"] or "").strip().lower()
        if not email:
            raise CommandError("An email address is required")
        user = (
            User.objects.filter(email__iexact=email).first()
            or User.objects.filter(username__iexact=email).first()
        )
        if user is None:
            raise CommandError(f"No account found for {email}")
        if not user.is_superuser:
            user.is_superuser = True
            user.is_staff = True
            user.save(update_fields=["is_superuser", "is_staff"])
        self.stdout.write(self.style.SUCCESS(f"{user.email} is now a super administrator"))
