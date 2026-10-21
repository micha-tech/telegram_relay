from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from relay.models import (
    AuditLog,
    ProcessedMessage,
    RelayConfiguration,
    Signal,
    SignalSource,
    TelegramAccount,
    TelegramEntity,
)

User = get_user_model()


def mask_phone(phone: str) -> str:
    """Mask a phone number, keeping only the country prefix and last four digits."""
    value = phone or ""
    if len(value) <= 4:
        return "+" * len(value)
    return f"{value[0]}{'•' * (len(value) - 4)}{value[-4:]}"


def account_label(account: TelegramAccount) -> str:
    """Human readable label for an account, without exposing the phone number."""
    if account.display_name:
        return account.display_name
    if account.username:
        return f"@{account.username}"
    return mask_phone(account.phone_number)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, data):
        return User.objects.create_user(
            username=data["email"].lower(), email=data["email"].lower(), password=data["password"]
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)


class TelegramAuthStartSerializer(serializers.Serializer):
    phone_number = serializers.RegexField(
        regex=r"^\+[1-9]\d{7,14}$",
        max_length=16,
        error_messages={"invalid": "Use an E.164 phone number such as +15551234567"},
    )


class TelegramAuthVerifySerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    code = serializers.RegexField(regex=r"^\d{4,12}$", max_length=12)


class TelegramAuth2FASerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    password = serializers.CharField(write_only=True, max_length=256, trim_whitespace=False)


class RelayRulesSerializer(serializers.Serializer):
    blocked_phrases = serializers.ListField(
        child=serializers.CharField(max_length=500), required=False, max_length=100
    )
    remove_urls = serializers.BooleanField(required=False)
    remove_source_name = serializers.BooleanField(required=False)
    source_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    replacements = serializers.ListField(
        child=serializers.DictField(), required=False, max_length=100
    )
    prefix = serializers.CharField(max_length=2_000, required=False, allow_blank=True)
    suffix = serializers.CharField(max_length=2_000, required=False, allow_blank=True)
    drop_empty = serializers.BooleanField(required=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(f"Unknown rule keys: {', '.join(sorted(unknown))}")
        return attrs

    def validate_replacements(self, value):
        result = []
        for index, replacement in enumerate(value):
            if set(replacement) != {"from", "to"}:
                raise serializers.ValidationError(
                    f"Replacement {index} must contain only 'from' and 'to'"
                )
            old = replacement["from"]
            new = replacement["to"]
            if not isinstance(old, str) or not old or len(old) > 500:
                raise serializers.ValidationError(f"Replacement {index} has an invalid 'from'")
            if not isinstance(new, str) or len(new) > 500:
                raise serializers.ValidationError(f"Replacement {index} has an invalid 'to'")
            result.append({"from": old, "to": new})
        return result


class SignalRulesSerializer(serializers.Serializer):
    keywords = serializers.ListField(
        child=serializers.CharField(max_length=200),
        max_length=100,
        allow_empty=False,
        error_messages={"empty": "Add at least one keyword to watch for."},
    )
    blocked_phrases = serializers.ListField(
        child=serializers.CharField(max_length=500), required=False, max_length=100
    )
    min_length = serializers.IntegerField(required=False, min_value=0, max_value=4000)
    case_sensitive = serializers.BooleanField(required=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(f"Unknown rule keys: {', '.join(sorted(unknown))}")
        return attrs

    def validate_keywords(self, value):
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise serializers.ValidationError("Add at least one keyword to watch for.")
        return cleaned


class TelegramAccountSerializer(serializers.ModelSerializer):
    phone_masked = serializers.SerializerMethodField()

    class Meta:
        model = TelegramAccount
        fields = [
            "id",
            "phone_masked",
            "telegram_user_id",
            "display_name",
            "username",
            "status",
            "last_connected_at",
            "last_activity_at",
            "last_error",
            "created_at",
        ]
        read_only_fields = fields

    def get_phone_masked(self, obj) -> str:
        return mask_phone(obj.phone_number or "")


class TelegramEntitySerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramEntity
        fields = [
            "id",
            "account",
            "kind",
            "peer_id",
            "entity_type",
            "username",
            "title",
            "created_at",
        ]
        read_only_fields = ["user", "entity_type", "username", "title", "created_at"]

    def validate_account(self, account):
        if account.user_id != self.context["request"].user.id:
            raise serializers.ValidationError("Invalid account")
        return account


class RelaySerializer(serializers.ModelSerializer):
    rules = serializers.JSONField()

    class Meta:
        model = RelayConfiguration
        fields = [
            "id",
            "account",
            "source",
            "destination",
            "name",
            "enabled",
            "rules",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        user = self.context["request"].user
        account = attrs.get("account", getattr(self.instance, "account", None))
        source = attrs.get("source", getattr(self.instance, "source", None))
        destination = attrs.get("destination", getattr(self.instance, "destination", None))
        if not account or account.user_id != user.id:
            raise serializers.ValidationError("Invalid account")
        if source.account_id != account.id or source.kind != "source":
            raise serializers.ValidationError("Invalid source")
        if destination.account_id != account.id or destination.kind != "destination":
            raise serializers.ValidationError("Invalid destination")
        return attrs

    def validate_rules(self, value):
        serializer = RelayRulesSerializer(data=value)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class SignalSourceSerializer(serializers.ModelSerializer):
    rules = serializers.JSONField()
    forward_to = serializers.PrimaryKeyRelatedField(
        queryset=TelegramEntity.objects.all(), allow_null=True, required=False
    )
    chat_title = serializers.SerializerMethodField()
    chat_username = serializers.SerializerMethodField()
    peer_id = serializers.SerializerMethodField()
    account_label = serializers.SerializerMethodField()

    class Meta:
        model = SignalSource
        fields = [
            "id",
            "account",
            "account_label",
            "entity",
            "forward_to",
            "chat_title",
            "chat_username",
            "peer_id",
            "name",
            "enabled",
            "forward_enabled",
            "capture_all",
            "rules",
            "backfill_limit",
            "last_scraped_at",
            "last_signal_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["last_scraped_at", "last_signal_at", "created_at", "updated_at"]

    def get_chat_title(self, obj) -> str:
        return obj.entity.title or ""

    def get_chat_username(self, obj) -> str:
        return obj.entity.username or ""

    def get_peer_id(self, obj) -> int:
        return obj.entity.peer_id

    def get_account_label(self, obj) -> str:
        return account_label(obj.account)

    def validate(self, attrs):
        account = attrs.get("account", getattr(self.instance, "account", None))
        entity = attrs.get("entity", getattr(self.instance, "entity", None))
        forward_to = attrs.get("forward_to", getattr(self.instance, "forward_to", None))
        if not account:
            raise serializers.ValidationError("Invalid account")
        if not entity or entity.account_id != account.id or entity.kind != "source":
            raise serializers.ValidationError("Invalid chat")
        if forward_to and forward_to.account_id != account.id:
            raise serializers.ValidationError(
                {"forward_to": "Forward target must belong to the same account."}
            )
        if forward_to and entity and forward_to.peer_id == entity.peer_id:
            raise serializers.ValidationError(
                {"forward_to": "Forward target must be a different chat."}
            )
        return attrs

    def validate_rules(self, value):
        if self.initial_data.get("capture_all") is True or (
            isinstance(self.instance, SignalSource) and self.instance.capture_all
        ):
            if not isinstance(value, dict):
                raise serializers.ValidationError("Invalid rules")
            return value
        serializer = SignalRulesSerializer(data=value)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class SignalSerializer(serializers.ModelSerializer):
    source_name = serializers.SerializerMethodField()
    account_label = serializers.SerializerMethodField()

    class Meta:
        model = Signal
        fields = [
            "id",
            "account",
            "account_label",
            "source",
            "source_name",
            "chat_id",
            "message_id",
            "text",
            "author",
            "author_id",
            "matched_terms",
            "sent_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_source_name(self, obj) -> str:
        source = obj.source
        return source.name or source.entity.title or source.entity.username or ""

    def get_account_label(self, obj) -> str:
        return account_label(obj.account)


class ProcessedMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessedMessage
        exclude = []


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "object_type",
            "object_id",
            "metadata",
            "request_id",
            "created_at",
        ]
