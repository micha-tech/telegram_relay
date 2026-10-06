from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from relay.models import AuditLog, ProcessedMessage, RelayConfiguration, TelegramAccount, TelegramEntity

User = get_user_model()

class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(); password = serializers.CharField(write_only=True)
    def validate_password(self, value): validate_password(value); return value
    def create(self, data): return User.objects.create_user(username=data["email"].lower(), email=data["email"].lower(), password=data["password"])

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(); password = serializers.CharField(write_only=True)

class TelegramAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramAccount
        fields = ["id", "phone_number", "telegram_user_id", "display_name", "username", "status", "last_connected_at", "last_error", "created_at"]
        read_only_fields = fields

class TelegramEntitySerializer(serializers.ModelSerializer):
    class Meta:
        model = TelegramEntity
        fields = ["id", "account", "kind", "peer_id", "entity_type", "username", "title", "created_at"]
        read_only_fields = ["user", "entity_type", "username", "title", "created_at"]
    def validate_account(self, account):
        if account.user_id != self.context["request"].user.id: raise serializers.ValidationError("Invalid account")
        return account

class RelaySerializer(serializers.ModelSerializer):
    class Meta:
        model = RelayConfiguration
        fields = ["id", "account", "source", "destination", "name", "enabled", "rules", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]
    def validate(self, attrs):
        user = self.context["request"].user
        account = attrs.get("account", getattr(self.instance, "account", None)); source = attrs.get("source", getattr(self.instance, "source", None)); destination = attrs.get("destination", getattr(self.instance, "destination", None))
        if not account or account.user_id != user.id: raise serializers.ValidationError("Invalid account")
        if source.account_id != account.id or source.kind != "source": raise serializers.ValidationError("Invalid source")
        if destination.account_id != account.id or destination.kind != "destination": raise serializers.ValidationError("Invalid destination")
        return attrs

class ProcessedMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessedMessage
        exclude = []

class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "action", "object_type", "object_id", "metadata", "request_id", "created_at"]
