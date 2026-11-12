import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_default_tenant(apps, schema_editor):
    """Put every existing user and account into a single 'Default' workspace."""
    User = apps.get_model("auth", "User")
    Tenant = apps.get_model("relay", "Tenant")
    Membership = apps.get_model("relay", "Membership")
    TelegramAccount = apps.get_model("relay", "TelegramAccount")

    if not User.objects.exists():
        return

    tenant, _ = Tenant.objects.get_or_create(
        slug="default", defaults={"name": "Default"}
    )
    owner = User.objects.filter(is_superuser=True).order_by("id").first()
    for user in User.objects.all().order_by("id"):
        role = "owner" if owner is not None and user.pk == owner.pk else "member"
        Membership.objects.get_or_create(
            user=user, defaults={"tenant": tenant, "role": role}
        )

    for account in TelegramAccount.objects.filter(tenant__isnull=True):
        membership = Membership.objects.filter(user_id=account.user_id).first()
        account.tenant_id = (
            membership.tenant_id if membership is not None else tenant.id
        )
        account.save(update_fields=["tenant"])


def drop_default_tenant(apps, schema_editor):
    # Tenants are meaningful data; rolling back only drops the schema.
    pass


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("relay", "0007_remove_signal_matched_terms_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=80, unique=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="Membership",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("owner", "Owner"),
                            ("admin", "Admin"),
                            ("member", "Member"),
                        ],
                        db_index=True,
                        default="member",
                        max_length=16,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="relay.tenant",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="membership",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["tenant", "role"], name="relay_membe_tenant__250911_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "user"), name="uniq_tenant_user"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Invite",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(db_index=True, max_length=64, unique=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("owner", "Owner"),
                            ("admin", "Admin"),
                            ("member", "Member"),
                        ],
                        default="member",
                        max_length=16,
                    ),
                ),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_invites",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invites",
                        to="relay.tenant",
                    ),
                ),
                (
                    "used_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="used_invites",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.AddField(
            model_name="telegramaccount",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="telegram_accounts",
                to="relay.tenant",
            ),
        ),
        migrations.RunPython(backfill_default_tenant, drop_default_tenant),
        migrations.AlterField(
            model_name="telegramaccount",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="telegram_accounts",
                to="relay.tenant",
            ),
        ),
        migrations.AddIndex(
            model_name="telegramaccount",
            index=models.Index(fields=["tenant", "status"], name="relay_teleg_tenant__1ca495_idx"),
        ),
    ]
