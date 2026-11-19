import logging
from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import IntegrityError, connection
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from relay.models import (
    AccountConnectionEvent,
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
from relay.pagination import SignalsPagination
from relay.permissions import IsTenantAdmin, IsTenantOwner
from relay.responses import ok
from relay.serializers import (
    AuditLogSerializer,
    InviteCreateSerializer,
    InviteSerializer,
    LoginSerializer,
    LogoutSerializer,
    MemberCreateSerializer,
    MemberSerializer,
    MemberUpdateSerializer,
    ProcessedMessageSerializer,
    RegisterSerializer,
    RelaySerializer,
    SignalSerializer,
    SignalSourceSerializer,
    TelegramAccountSerializer,
    TelegramAuth2FASerializer,
    TelegramAuthStartSerializer,
    TelegramAuthVerifySerializer,
    TelegramEntitySerializer,
    TenantSerializer,
)
from relay.services.account_lifecycle import AccountLifecycleError, AccountLifecycleService
from relay.services.redis import RedisService
from relay.services.scrape_queue import enqueue_scrape
from relay.services.telegram_auth import (
    TelegramAuthError,
    TelegramAuthService,
    TelegramFloodWaitError,
)
from relay.services.telegram_entities import TelegramEntityService
from relay.tenancy import (
    apply_scope,
    ensure_active_membership,
    is_tenant_owner,
    issue_invite,
    membership_for,
)

logger = logging.getLogger(__name__)


class FloodWaitResponse(APIException):
    """Client error carrying the retry window reported by Telegram."""

    status_code = 400
    default_code = "flood_wait"

    def __init__(self, seconds: int):
        super().__init__(f"Too many requests, try again in {seconds} seconds")
        self.retry_after_seconds = seconds


def raise_telegram_auth_error(exc: TelegramAuthError):
    if isinstance(exc, TelegramFloodWaitError):
        raise FloodWaitResponse(exc.seconds) from exc
    raise serializers.ValidationError(str(exc)) from exc


@api_view(["GET"])
@permission_classes([AllowAny])
def health_live(request):
    return JsonResponse({"status": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
def health_ready(request):
    try:
        connection.ensure_connection()
        RedisService().health()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


def tokens(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "access_expires_seconds": 900,
    }


def tenant_payload(membership: Membership | None) -> dict | None:
    if membership is None:
        return None
    return {
        "id": str(membership.tenant_id),
        "name": membership.tenant.name,
        "slug": membership.tenant.slug,
    }


def user_payload(user):
    membership = membership_for(user)
    return {
        "id": user.id,
        "email": user.email,
        "is_superuser": bool(user.is_superuser),
        "role": membership.role if membership else None,
        "tenant": tenant_payload(membership),
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        user = serializer.save()
    except IntegrityError as exc:
        raise serializers.ValidationError({"email": "An account already exists"}) from exc
    return ok({"user": user_payload(user), **tokens(user)}, status=201)


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(
        username=serializer.validated_data["email"].lower(),
        password=serializer.validated_data["password"],
    )
    if not user:
        raise serializers.ValidationError("Invalid credentials")
    return ok({"user": user_payload(user), **tokens(user)})


@api_view(["GET"])
def me(request):
    return ok(user_payload(request.user))


@api_view(["POST"])
def logout(request):
    serializer = LogoutSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        RefreshToken(serializer.validated_data["refresh"]).blacklist()
    except TokenError as exc:
        raise serializers.ValidationError("Invalid refresh token") from exc
    return ok(message="Logged out")


class EnvelopeMixin:
    """Wrap DRF viewset payloads in the shared {success, data} envelope."""

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        if page is not None:
            return ok(self.get_paginated_response(serializer.data).data)
        return ok(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        return ok(self.get_serializer(self.get_object()).data)


class AccountViewSet(EnvelopeMixin, viewsets.ReadOnlyModelViewSet):
    """Workspace administrative view over the tenant's Telegram accounts."""

    serializer_class = TelegramAccountSerializer
    permission_classes = [IsTenantAdmin]

    def get_queryset(self):
        return apply_scope(
            TelegramAccount.objects.select_related("user").order_by("created_at"),
            self.request.user,
            "tenant",
        )

    @action(detail=True, methods=["post"])
    def disconnect(self, request, pk=None):
        account = self.get_object()
        account = AccountLifecycleService().disconnect(
            account, request_id=getattr(request, "request_id", "")
        )
        return ok(self.get_serializer(account).data)

    @action(detail=True, methods=["post"])
    def reconnect(self, request, pk=None):
        account = self.get_object()
        try:
            account = AccountLifecycleService().reconnect(
                account, request_id=getattr(request, "request_id", "")
            )
        except AccountLifecycleError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return ok(self.get_serializer(account).data)

    @action(detail=True, methods=["get"])
    def health(self, request, pk=None):
        return ok(AccountLifecycleService().health(self.get_object()))

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        account = self.get_object()
        events = AccountConnectionEvent.objects.filter(account=account).order_by("-created_at")[
            :20
        ]
        return ok(
            [
                {
                    "kind": event.kind,
                    "detail": event.detail,
                    "created_at": event.created_at,
                }
                for event in events
            ]
        )

    @action(detail=True, methods=["get"])
    def dialogs(self, request, pk=None):
        return ok(TelegramEntityService().list_dialogs(self.get_object()))


class EntityViewSet(EnvelopeMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = TelegramEntitySerializer
    permission_classes = [IsTenantAdmin]

    def get_queryset(self):
        return apply_scope(
            TelegramEntity.objects.select_related("account", "user"),
            self.request.user,
            "account__tenant",
        )


class SignalViewSet(EnvelopeMixin, viewsets.ReadOnlyModelViewSet):
    """Scraped signals. Members read the feed of their own workspace."""

    serializer_class = SignalSerializer
    pagination_class = SignalsPagination

    def get_queryset(self):
        queryset = apply_scope(
            Signal.objects.select_related("account", "source", "source__entity"),
            self.request.user,
            "account__tenant",
        )
        params = self.request.query_params
        account = params.get("account")
        if account:
            queryset = queryset.filter(account_id=account)
        source = params.get("source")
        if source:
            queryset = queryset.filter(source_id=source)
        chat_id = params.get("chat_id")
        if chat_id:
            queryset = queryset.filter(chat_id=chat_id)
        text = (params.get("q") or "").strip()
        if text:
            queryset = queryset.filter(text__icontains=text)
        since = params.get("since")
        if since:
            moment = parse_datetime(since)
            if moment:
                if timezone.is_naive(moment):
                    moment = timezone.make_aware(moment, timezone.get_current_timezone())
                queryset = queryset.filter(
                    Q(sent_at__gte=moment)
                    | Q(sent_at__isnull=True, created_at__gte=moment)
                )
        return queryset.order_by("-sent_at", "-created_at")

    @action(detail=False, methods=["get"])
    def stats(self, request):
        queryset = self.get_queryset()
        now = timezone.now()
        total = queryset.count()
        last_24h = queryset.filter(sent_at__gte=now - timedelta(hours=24)).count()
        by_source = [
            {
                "source_id": str(row["source_id"]),
                "name": row["source__name"] or row["source__entity__title"] or "",
                "count": row["count"],
            }
            for row in queryset.values(
                "source_id", "source__name", "source__entity__title"
            )
            .annotate(count=Count("id"))
            .order_by("-count")[:20]
        ]
        by_account = [
            {
                "account_id": str(row["account_id"]),
                "name": row["account__display_name"] or "",
                "count": row["count"],
            }
            for row in queryset.values("account_id", "account__display_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:20]
        ]
        return ok(
            {
                "total": total,
                "last_24h": last_24h,
                "active_sources": apply_scope(
                    SignalSource.objects.filter(enabled=True), request.user, "account__tenant"
                ).count(),
                "by_source": by_source,
                "by_account": by_account,
            }
        )


class SignalSourceViewSet(EnvelopeMixin, viewsets.ModelViewSet):
    """Watched chats whose messages become signals. Workspace administrators only."""

    serializer_class = SignalSourceSerializer
    permission_classes = [IsTenantAdmin]

    def get_queryset(self):
        return apply_scope(
            SignalSource.objects.select_related("account", "entity").order_by("created_at"),
            self.request.user,
            "account__tenant",
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = serializer.save()
        AuditLog.objects.create(
            user=request.user,
            action="signal_source.created",
            object_type="signal_source",
            object_id=source.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(self.get_serializer(source).data, status=201)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        source = serializer.save()
        AuditLog.objects.create(
            user=request.user,
            action="signal_source.updated",
            object_type="signal_source",
            object_id=source.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(self.get_serializer(source).data)

    def destroy(self, request, *args, **kwargs):
        source = self.get_object()
        self.perform_destroy(source)
        AuditLog.objects.create(
            user=request.user,
            action="signal_source.deleted",
            object_type="signal_source",
            object_id=source.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(message="Signal source deleted")

    @action(detail=True, methods=["post"])
    def scrape(self, request, pk=None):
        """Queue a history backfill for this source; the worker performs it."""
        source = self.get_object()
        enqueue_scrape(source.account_id, source.id)
        AuditLog.objects.create(
            user=request.user,
            action="signal_source.scrape_requested",
            object_type="signal_source",
            object_id=source.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok({"queued": True, "source_id": str(source.id)}, status=202)


class RelayViewSet(EnvelopeMixin, viewsets.ModelViewSet):
    serializer_class = RelaySerializer

    def get_permissions(self):
        # Members may read relays; only workspace admins may change them.
        if self.action in ("list", "retrieve", "metadata"):
            return [IsAuthenticated()]
        return [IsTenantAdmin()]

    def get_queryset(self):
        return apply_scope(
            RelayConfiguration.objects.select_related("source", "destination").order_by(
                "-created_at"
            ),
            self.request.user,
            "account__tenant",
        )

    def perform_create(self, serializer):
        relay = serializer.save(user=self.request.user)
        AuditLog.objects.create(
            user=self.request.user,
            action="relay.created",
            object_type="relay",
            object_id=relay.id,
            request_id=getattr(self.request, "request_id", ""),
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return ok(serializer.data, status=201)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return ok(serializer.data)

    def perform_update(self, serializer):
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        self.perform_destroy(self.get_object())
        return ok(message="Relay deleted")

    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        return self._set(request, True)

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        return self._set(request, False)

    def _set(self, request, enabled):
        relay = self.get_object()
        relay.enabled = enabled
        relay.save(update_fields=["enabled", "updated_at"])
        AuditLog.objects.create(
            user=request.user,
            action=f"relay.{'enabled' if enabled else 'disabled'}",
            object_type="relay",
            object_id=relay.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(self.get_serializer(relay).data)


class MessageViewSet(EnvelopeMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = ProcessedMessageSerializer

    def get_queryset(self):
        return apply_scope(
            ProcessedMessage.objects.order_by("-created_at"),
            self.request.user,
            "relay__account__tenant",
        )


class AuditLogViewSet(EnvelopeMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        return AuditLog.objects.filter(user=self.request.user).order_by("-created_at")


class TenantViewSet(viewsets.GenericViewSet):
    """The caller's workspace: read it, or rename it as the owner."""

    serializer_class = TenantSerializer

    def get_permissions(self):
        if self.action in ("update", "partial_update", "rename"):
            return [IsTenantOwner()]
        return [IsTenantAdmin()]

    def get_queryset(self):
        return apply_scope(Tenant.objects.all(), self.request.user, "id")

    def _current(self):
        membership = membership_for(self.request.user)
        if membership is None:
            raise serializers.ValidationError("You are not a member of a workspace")
        if not membership.tenant.is_active:
            raise PermissionDenied("This workspace is inactive")
        return membership.tenant

    @action(detail=False, methods=["get"])
    def current(self, request):
        tenant = self._current()
        return ok(self.get_serializer(tenant, context={"request": request}).data)

    @action(detail=False, methods=["patch"])
    def rename(self, request):
        tenant = self._current()
        serializer = self.get_serializer(tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        AuditLog.objects.create(
            user=request.user,
            action="tenant.renamed",
            object_type="tenant",
            object_id=tenant.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(serializer.data)


class MemberViewSet(EnvelopeMixin, viewsets.GenericViewSet):
    """Users inside the caller's workspace. Manageable by workspace admins."""

    serializer_class = MemberSerializer
    permission_classes = [IsTenantAdmin]
    lookup_field = "user_id"

    def get_queryset(self):
        return apply_scope(
            Membership.objects.select_related("user", "tenant").order_by("created_at"),
            self.request.user,
            "tenant",
        )

    def list(self, request, *args, **kwargs):
        return ok(self.get_serializer(self.get_queryset(), many=True).data)

    def create(self, request, *args, **kwargs):
        membership = ensure_active_membership(request.user)
        serializer = MemberCreateSerializer(
            data=request.data, context={"tenant": membership.tenant}
        )
        serializer.is_valid(raise_exception=True)
        if (
            serializer.validated_data["role"] == Membership.Role.ADMIN
            and not is_tenant_owner(request.user)
        ):
            raise serializers.ValidationError(
                {"role": "Only the workspace owner can add administrators"}
            )
        member = serializer.save()
        AuditLog.objects.create(
            user=request.user,
            action="member.created",
            object_type="membership",
            object_id=member.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(self.get_serializer(member).data, status=201)

    def update(self, request, *args, **kwargs):
        member = self.get_object()
        serializer = MemberUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data["role"]
        if member.user_id == request.user.id:
            raise serializers.ValidationError("You cannot change your own role")
        if not is_tenant_owner(request.user):
            raise serializers.ValidationError("Only the workspace owner can change roles")
        if member.role == Membership.Role.OWNER and role != Membership.Role.OWNER:
            self._ensure_another_owner(member)
        member.role = role
        member.save(update_fields=["role", "updated_at"])
        return ok(self.get_serializer(member).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        member = self.get_object()
        if member.user_id == request.user.id:
            raise serializers.ValidationError("You cannot remove yourself")
        if not is_tenant_owner(request.user) and member.role != Membership.Role.MEMBER:
            raise serializers.ValidationError("Only the workspace owner can remove administrators")
        if member.role == Membership.Role.OWNER:
            self._ensure_another_owner(member)
        user = member.user
        member.delete()
        # Keep the user row (and anything it connected) but revoke access.
        user.is_active = False
        user.save(update_fields=["is_active"])
        AuditLog.objects.create(
            user=request.user,
            action="member.removed",
            object_type="membership",
            object_id=member.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(message="Member removed")

    @staticmethod
    def _ensure_another_owner(member):
        others = Membership.objects.filter(
            tenant_id=member.tenant_id, role=Membership.Role.OWNER
        ).exclude(pk=member.pk)
        if not others.exists():
            raise serializers.ValidationError("A workspace must keep at least one owner")


class InviteViewSet(EnvelopeMixin, viewsets.GenericViewSet):
    """Single-use invite codes for joining the workspace."""

    serializer_class = InviteSerializer
    permission_classes = [IsTenantAdmin]

    def get_queryset(self):
        return apply_scope(
            Invite.objects.select_related("created_by").order_by("-created_at"),
            self.request.user,
            "tenant",
        )

    def list(self, request, *args, **kwargs):
        return ok(self.get_serializer(self.get_queryset(), many=True).data)

    def create(self, request, *args, **kwargs):
        membership = ensure_active_membership(request.user)
        serializer = InviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["role"] == Membership.Role.ADMIN and not is_tenant_owner(request.user):
            raise serializers.ValidationError(
                {"role": "Only the workspace owner can invite administrators"}
            )
        invite = issue_invite(
            membership.tenant,
            request.user,
            role=data["role"],
            email=data["email"],
            days=data["expires_in_days"],
        )
        AuditLog.objects.create(
            user=request.user,
            action="invite.created",
            object_type="invite",
            object_id=invite.id,
            request_id=getattr(request, "request_id", ""),
        )
        return ok(self.get_serializer(invite).data, status=201)

    def destroy(self, request, *args, **kwargs):
        invite = self.get_object()
        invite.delete()
        return ok(message="Invite revoked")


@api_view(["GET"])
@permission_classes([AllowAny])
def invite_preview(request, code):
    invite = Invite.objects.select_related("tenant").filter(code=code).first()
    if invite is None or not invite.is_usable or not invite.tenant.is_active:
        raise serializers.ValidationError("This invite is not valid")
    return ok(
        {
            "tenant_name": invite.tenant.name,
            "tenant_slug": invite.tenant.slug,
            "role": invite.role,
            "email": invite.email,
            "expires_at": invite.expires_at,
        }
    )


class TelegramAuthThrottle(ScopedRateThrottle):
    scope = "telegram_auth"


@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
@permission_classes([IsTenantAdmin])
def telegram_auth_start(request):
    serializer = TelegramAuthStartSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    ensure_active_membership(request.user)
    try:
        challenge = TelegramAuthService().start(
            request.user,
            serializer.validated_data["phone_number"],
            request_id=getattr(request, "request_id", ""),
        )
    except TelegramAuthError as exc:
        raise_telegram_auth_error(exc)
    return ok(
        {
            "challenge_id": str(challenge.id),
            "status": challenge.state,
            "expires_at": challenge.expires_at,
        },
        status=202,
    )


@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
@permission_classes([IsTenantAdmin])
def telegram_auth_verify(request):
    serializer = TelegramAuthVerifySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        challenge = TelegramAuthService().verify(
            request.user,
            serializer.validated_data["challenge_id"],
            serializer.validated_data["code"],
            request_id=getattr(request, "request_id", ""),
        )
    except TelegramAuthError as exc:
        raise_telegram_auth_error(exc)
    return ok(
        {
            "challenge_id": str(challenge.id),
            "status": challenge.state,
            "expires_at": challenge.expires_at,
        }
    )


@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
@permission_classes([IsTenantAdmin])
def telegram_auth_2fa(request):
    serializer = TelegramAuth2FASerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        challenge = TelegramAuthService().verify_2fa(
            request.user,
            serializer.validated_data["challenge_id"],
            serializer.validated_data["password"],
            request_id=getattr(request, "request_id", ""),
        )
    except TelegramAuthError as exc:
        raise_telegram_auth_error(exc)
    return ok(
        {
            "challenge_id": str(challenge.id),
            "status": challenge.state,
            "expires_at": challenge.expires_at,
        }
    )
