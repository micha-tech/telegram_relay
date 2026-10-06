import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="TelegramAccount", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("telegram_user_id", models.BigIntegerField(blank=True, null=True)), ("phone_number", models.CharField(max_length=32)), ("display_name", models.CharField(blank=True, max_length=255)), ("username", models.CharField(blank=True, max_length=255)),
            ("status", models.CharField(choices=[("pending", "Pending"), ("connected", "Connected"), ("disconnected", "Disconnected"), ("invalid", "Invalid"), ("error", "Error")], db_index=True, default="pending", max_length=20)), ("last_connected_at", models.DateTimeField(blank=True, null=True)), ("last_error", models.CharField(blank=True, max_length=500)),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_accounts", to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="AuditLog", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("action", models.CharField(db_index=True, max_length=100)), ("object_type", models.CharField(blank=True, max_length=80)), ("object_id", models.UUIDField(null=True)), ("metadata", models.JSONField(default=dict)), ("request_id", models.CharField(blank=True, db_index=True, max_length=64)), ("user", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="AuthenticationChallenge", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("encrypted_session", models.BinaryField()), ("encrypted_phone_code_hash", models.BinaryField()),
            ("state", models.CharField(choices=[("code_sent", "Code Sent"), ("password_required", "Password Required"), ("complete", "Complete"), ("failed", "Failed"), ("expired", "Expired")], db_index=True, default="code_sent", max_length=24)), ("expires_at", models.DateTimeField(db_index=True)), ("attempts", models.PositiveSmallIntegerField(default=0)), ("consumed_at", models.DateTimeField(blank=True, null=True)),
            ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="relay.telegramaccount")), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="TelegramSession", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("encrypted_session", models.BinaryField()), ("key_version", models.PositiveSmallIntegerField(default=1)), ("account", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="session", to="relay.telegramaccount")),
        ]),
        migrations.CreateModel(name="TelegramEntity", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("kind", models.CharField(choices=[("source", "Source"), ("destination", "Destination")], max_length=16)), ("peer_id", models.BigIntegerField()), ("entity_type", models.CharField(max_length=24)), ("access_hash_encrypted", models.BinaryField(blank=True, null=True)), ("username", models.CharField(blank=True, max_length=255)), ("title", models.CharField(blank=True, max_length=255)),
            ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="relay.telegramaccount")), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="RelayConfiguration", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("name", models.CharField(max_length=255)), ("enabled", models.BooleanField(db_index=True, default=False)), ("rules", models.JSONField(default=dict)),
            ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="relay.telegramaccount")), ("destination", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="destination_relays", to="relay.telegramentity")), ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="source_relays", to="relay.telegramentity")), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="ProcessedMessage", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("source_chat_id", models.BigIntegerField()), ("source_message_id", models.BigIntegerField()), ("destination_message_id", models.BigIntegerField(blank=True, null=True)), ("status", models.CharField(choices=[("processing", "Processing"), ("published", "Published"), ("filtered", "Filtered"), ("failed", "Failed")], db_index=True, max_length=16)), ("error_code", models.CharField(blank=True, max_length=80)), ("error_message", models.CharField(blank=True, max_length=500)),
            ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="relay.telegramaccount")), ("relay", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="relay.relayconfiguration")),
        ]),
        migrations.AddConstraint(model_name="telegramaccount", constraint=models.UniqueConstraint(fields=("user", "phone_number"), name="uniq_user_phone")),
        migrations.AddConstraint(model_name="telegramentity", constraint=models.UniqueConstraint(fields=("account", "kind", "peer_id"), name="uniq_account_kind_peer")),
        migrations.AddIndex(model_name="telegramentity", index=models.Index(fields=["user", "kind"], name="relay_teleg_user_id_58a89a_idx")),
        migrations.AddIndex(model_name="telegramentity", index=models.Index(fields=["account", "peer_id"], name="relay_teleg_account_131153_idx")),
        migrations.AddIndex(model_name="relayconfiguration", index=models.Index(fields=["account", "enabled"], name="relay_relay_account_2d2d25_idx")),
        migrations.AddIndex(model_name="relayconfiguration", index=models.Index(fields=["source", "enabled"], name="relay_relay_source__88be38_idx")),
        migrations.AddConstraint(model_name="processedmessage", constraint=models.UniqueConstraint(fields=("account", "source_chat_id", "source_message_id", "relay"), name="uniq_processed_message")),
        migrations.AddIndex(model_name="processedmessage", index=models.Index(fields=["relay", "status", "created_at"], name="relay_proce_relay_i_b86066_idx")),
        migrations.AddIndex(model_name="processedmessage", index=models.Index(fields=["source_chat_id", "source_message_id"], name="relay_proce_source__d9b35f_idx")),
    ]
