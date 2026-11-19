from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from relay.models import (
    AuditLog,
    Invite,
    Membership,
    ProcessedMessage,
    RelayConfiguration,
    Signal,
    SignalSource,
    TelegramAccount,
    TelegramEntity,
    Tenant,
)
from relay.tenancy import can_access_tenant, create_tenant, is_tenant_admin, membership_for

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
    """Create a workspace (default) or join an existing one with an invite code."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    tenant_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    invite_code = serializers.CharField(max_length=64, required=False, allow_blank=True)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_invite_code(self, value):
        value = (value or "").strip()
        if not value:
            return ""
        invite = Invite.objects.select_related("tenant").filter(code=value).first()
        if invite is None:
            raise serializers.ValidationError("Invalid invite code")
        if invite.used_at is not None:
            raise serializers.ValidationError("This invite has already been used")
        if invite.expires_at <= timezone.now():
            raise serializers.ValidationError("This invite has expired")
        if not invite.tenant.is_active:
            raise serializers.ValidationError("This workspace is not active")
        email = (self.initial_data.get("email") or "").strip().lower()
        if invite.email and invite.email != email:
            raise serializers.ValidationError(
                "This invite was issued for a different email address"
            )
        self._invite = invite
        return value

    @transaction.atomic
    def create(self, data):
        email = data["email"].strip().lower()
        if User.objects.filter(username=email).exists():
            raise serializers.ValidationError({"email": "An account already exists"})
        invite = getattr(self, "_invite", None)
        user = User.objects.create_user(
            username=email, email=email, password=data["password"]
        )
        if invite is not None:
            invite = Invite.objects.select_for_update().get(pk=invite.pk)
            if invite.used_at is not None:
                raise serializers.ValidationError(
                    {"invite_code": "This invite has already been used"}
                )
            Membership.objects.create(user=user, tenant=invite.tenant, role=invite.role)
            invite.used_at = timezone.now()
            invite.used_by = user
            invite.save(update_fields=["used_at", "used_by", "updated_at"])
        else:
            create_tenant(data.get("tenant_name") or "", user)
        return user


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
        user = self.context["request"].user
        if not can_access_tenant(user, account.tenant_id):
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
        usable = (
            account
            and can_access_tenant(user, account.tenant_id)
            and (account.user_id == user.id or is_tenant_admin(user))
        )
        if not usable:
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
        user = self.context["request"].user
        if not account:
            raise serializers.ValidationError("Invalid account")
        if not can_access_tenant(user, account.tenant_id):
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


class TenantSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ["id", "name", "slug", "is_active", "role", "member_count", "created_at"]
        read_only_fields = ["id", "slug", "is_active", "role", "member_count", "created_at"]

    def get_role(self, obj) -> str | None:
        request = self.context.get("request")
        membership = membership_for(request.user) if request else None
        return membership.role if membership else None

    def get_member_count(self, obj) -> int:
        return obj.memberships.count()


class MemberSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="user_id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "email", "role", "is_active", "created_at"]
        read_only_fields = fields


class MemberCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(
        choices=[Membership.Role.ADMIN, Membership.Role.MEMBER],
        default=Membership.Role.MEMBER,
    )

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_email(self, value):
        email = value.strip().lower()
        existing = User.objects.filter(username=email).first()
        if existing is not None:
            reactivatable = not existing.is_active and not Membership.objects.filter(
                user=existing
            ).exists()
            if not reactivatable:
                raise serializers.ValidationError("An account already exists for this email")
            self._existing = existing
        return email

    @transaction.atomic
    def create(self, data):
        tenant = self.context["tenant"]
        user = getattr(self, "_existing", None)
        if user is not None:
            user.is_active = True
            user.set_password(data["password"])
            user.save(update_fields=["is_active", "password"])
        else:
            user = User.objects.create_user(
                username=data["email"], email=data["email"], password=data["password"]
            )
        return Membership.objects.create(user=user, tenant=tenant, role=data["role"])


class MemberUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.Role.choices)


class InviteSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default="")
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invite
        fields = [
            "id",
            "code",
            "role",
            "email",
            "created_by_email",
            "expires_at",
            "used_at",
            "is_usable",
            "created_at",
        ]
        read_only_fields = fields


class InviteCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(
        choices=[Membership.Role.ADMIN, Membership.Role.MEMBER],
        default=Membership.Role.MEMBER,
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    expires_in_days = serializers.IntegerField(min_value=1, max_value=30, default=7)


class InvitePreviewSerializer(serializers.Serializer):
    tenant_name = serializers.CharField()
    tenant_slug = serializers.CharField()
    role = serializers.CharField()
    email = serializers.CharField(allow_blank=True)
    expires_at = serializers.DateTimeField()


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
