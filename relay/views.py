import logging

from django.contrib.auth import authenticate
from django.db import IntegrityError, connection
from django.http import JsonResponse
from rest_framework import serializers, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from relay.models import (
    AccountConnectionEvent,
    AuditLog,
    ProcessedMessage,
    RelayConfiguration,
    TelegramAccount,
    TelegramEntity,
)
from relay.responses import ok
from relay.serializers import (
    AuditLogSerializer,
    LoginSerializer,
    LogoutSerializer,
    ProcessedMessageSerializer,
    RegisterSerializer,
    RelaySerializer,
    TelegramAccountSerializer,
    TelegramAuth2FASerializer,
    TelegramAuthStartSerializer,
    TelegramAuthVerifySerializer,
    TelegramEntitySerializer,
)
from relay.services.account_lifecycle import AccountLifecycleError, AccountLifecycleService
from relay.services.redis import RedisService
from relay.services.telegram_auth import TelegramAuthError, TelegramAuthService
from relay.services.telegram_entities import TelegramEntityService

logger = logging.getLogger(__name__)


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


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        user = serializer.save()
    except IntegrityError as exc:
        raise serializers.ValidationError({"email": "An account already exists"}) from exc
    return ok({"user": {"id": user.id, "email": user.email}, **tokens(user)}, status=201)


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
    return ok({"user": {"id": user.id, "email": user.email}, **tokens(user)})


@api_view(["GET"])
def me(request):
    return ok({"id": request.user.id, "email": request.user.email})


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
    serializer_class = TelegramAccountSerializer

    def get_queryset(self):
        return TelegramAccount.objects.filter(user=self.request.user).order_by("created_at")

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

    def get_queryset(self):
        return TelegramEntity.objects.filter(user=self.request.user)


class RelayViewSet(EnvelopeMixin, viewsets.ModelViewSet):
    serializer_class = RelaySerializer

    def get_queryset(self):
        return RelayConfiguration.objects.filter(user=self.request.user).select_related(
            "source", "destination"
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
        return ProcessedMessage.objects.filter(relay__user=self.request.user).order_by(
            "-created_at"
        )


class AuditLogViewSet(EnvelopeMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        return AuditLog.objects.filter(user=self.request.user).order_by("-created_at")


class TelegramAuthThrottle(ScopedRateThrottle):
    scope = "telegram_auth"


@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
def telegram_auth_start(request):
    serializer = TelegramAuthStartSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        challenge = TelegramAuthService().start(
            request.user,
            serializer.validated_data["phone_number"],
            request_id=getattr(request, "request_id", ""),
        )
    except TelegramAuthError as exc:
        raise serializers.ValidationError(str(exc)) from exc
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
        raise serializers.ValidationError(str(exc)) from exc
    return ok({"challenge_id": str(challenge.id), "status": challenge.state})


@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
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
        raise serializers.ValidationError(str(exc)) from exc
    return ok({"challenge_id": str(challenge.id), "status": challenge.state})
