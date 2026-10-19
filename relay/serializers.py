from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from relay.models import (
    AuditLog,
    ProcessedMessage,
    RelayConfiguration,
    TelegramAccount,
    TelegramEntity,
)

User = get_user_model()


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
        phone = obj.phone_number or ""
        if len(phone) <= 4:
            return "+" * len(phone)
        return f"{phone[0]}{'•' * (len(phone) - 4)}{phone[-4:]}"


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
