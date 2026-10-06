import uuid
from django.conf import settings
from django.db import models

class TimestampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class TelegramAccount(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"; CONNECTED = "connected"; DISCONNECTED = "disconnected"; INVALID = "invalid"; ERROR = "error"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_accounts")
    telegram_user_id = models.BigIntegerField(null=True, blank=True)
    phone_number = models.CharField(max_length=32)
    display_name = models.CharField(max_length=255, blank=True)
    username = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "phone_number"], name="uniq_user_phone")]

class TelegramSession(TimestampedModel):
    account = models.OneToOneField(TelegramAccount, on_delete=models.CASCADE, related_name="session")
    encrypted_session = models.BinaryField()
    key_version = models.PositiveSmallIntegerField(default=1)

class AuthenticationChallenge(TimestampedModel):
    class State(models.TextChoices):
        CODE_SENT = "code_sent"; PASSWORD_REQUIRED = "password_required"; COMPLETE = "complete"; FAILED = "failed"; EXPIRED = "expired"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account = models.ForeignKey(TelegramAccount, on_delete=models.CASCADE)
    encrypted_session = models.BinaryField()
    encrypted_phone_code_hash = models.BinaryField()
    state = models.CharField(max_length=24, choices=State.choices, default=State.CODE_SENT, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

class TelegramEntity(TimestampedModel):
    class Kind(models.TextChoices):
        SOURCE = "source"; DESTINATION = "destination"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account = models.ForeignKey(TelegramAccount, on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    peer_id = models.BigIntegerField()
    entity_type = models.CharField(max_length=24)
    access_hash_encrypted = models.BinaryField(null=True, blank=True)
    username = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "kind", "peer_id"], name="uniq_account_kind_peer")]
        indexes = [models.Index(fields=["user", "kind"]), models.Index(fields=["account", "peer_id"])]

class RelayConfiguration(TimestampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account = models.ForeignKey(TelegramAccount, on_delete=models.CASCADE)
    source = models.ForeignKey(TelegramEntity, on_delete=models.PROTECT, related_name="source_relays")
    destination = models.ForeignKey(TelegramEntity, on_delete=models.PROTECT, related_name="destination_relays")
    name = models.CharField(max_length=255)
    enabled = models.BooleanField(default=False, db_index=True)
    rules = models.JSONField(default=dict)
    class Meta:
        indexes = [models.Index(fields=["account", "enabled"]), models.Index(fields=["source", "enabled"])]

class ProcessedMessage(TimestampedModel):
    class Status(models.TextChoices):
        PROCESSING = "processing"; PUBLISHED = "published"; FILTERED = "filtered"; FAILED = "failed"
    relay = models.ForeignKey(RelayConfiguration, on_delete=models.CASCADE)
    account = models.ForeignKey(TelegramAccount, on_delete=models.CASCADE)
    source_chat_id = models.BigIntegerField()
    source_message_id = models.BigIntegerField()
    destination_message_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "source_chat_id", "source_message_id", "relay"], name="uniq_processed_message")]
        indexes = [models.Index(fields=["relay", "status", "created_at"]), models.Index(fields=["source_chat_id", "source_message_id"])]

class AuditLog(TimestampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.UUIDField(null=True)
    metadata = models.JSONField(default=dict)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
