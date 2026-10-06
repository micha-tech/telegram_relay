from django.contrib.auth import authenticate
from django.db import IntegrityError
from django.http import JsonResponse
from rest_framework import serializers, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from relay.models import AuditLog, ProcessedMessage, RelayConfiguration, TelegramAccount, TelegramEntity
from relay.responses import ok
from relay.serializers import AuditLogSerializer, LoginSerializer, ProcessedMessageSerializer, RegisterSerializer, RelaySerializer, TelegramAccountSerializer, TelegramEntitySerializer
from relay.services.telegram_auth import TelegramAuthError, TelegramAuthService
from relay.services.telegram_entities import TelegramEntityService

@api_view(["GET"])
@permission_classes([AllowAny])
def health(request): return JsonResponse({"status": "ok"})

def tokens(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh), "access_expires_seconds": 900}

@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    serializer = RegisterSerializer(data=request.data); serializer.is_valid(raise_exception=True)
    try: user = serializer.save()
    except IntegrityError: raise serializers.ValidationError({"email": "An account already exists"})
    return ok({"user": {"id": user.id, "email": user.email}, **tokens(user)}, status=201)

@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    serializer = LoginSerializer(data=request.data); serializer.is_valid(raise_exception=True)
    user = authenticate(username=serializer.validated_data["email"].lower(), password=serializer.validated_data["password"])
    if not user: raise serializers.ValidationError("Invalid credentials")
    return ok({"user": {"id": user.id, "email": user.email}, **tokens(user)})

@api_view(["GET"])
def me(request): return ok({"id": request.user.id, "email": request.user.email})

@api_view(["POST"])
def logout(request):
    # JWTs are held by the client; discard the access/refresh pair on logout.
    return ok(message="Logged out")

class AccountViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TelegramAccountSerializer
    def get_queryset(self): return TelegramAccount.objects.filter(user=self.request.user)
    @action(detail=True, methods=["post"])
    def disconnect(self, request, pk=None):
        account = self.get_object(); account.status = TelegramAccount.Status.DISCONNECTED; account.save(update_fields=["status", "updated_at"])
        return ok(self.get_serializer(account).data)
    @action(detail=True, methods=["get"])
    def dialogs(self, request, pk=None):
        return ok(TelegramEntityService().list_dialogs(self.get_object()))

class EntityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TelegramEntitySerializer
    def get_queryset(self): return TelegramEntity.objects.filter(user=self.request.user)

class RelayViewSet(viewsets.ModelViewSet):
    serializer_class = RelaySerializer
    def get_queryset(self): return RelayConfiguration.objects.filter(user=self.request.user).select_related("source", "destination")
    def perform_create(self, serializer):
        relay = serializer.save(user=self.request.user)
        AuditLog.objects.create(user=self.request.user, action="relay.created", object_type="relay", object_id=relay.id, request_id=getattr(self.request, "request_id", ""))
    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None): return self._set(request, True)
    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None): return self._set(request, False)
    def _set(self, request, enabled):
        relay = self.get_object(); relay.enabled = enabled; relay.save(update_fields=["enabled", "updated_at"])
        AuditLog.objects.create(user=request.user, action=f"relay.{'enabled' if enabled else 'disabled'}", object_type="relay", object_id=relay.id, request_id=getattr(request, "request_id", ""))
        return ok(self.get_serializer(relay).data)

class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProcessedMessageSerializer
    def get_queryset(self): return ProcessedMessage.objects.filter(relay__user=self.request.user).order_by("-created_at")

class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    def get_queryset(self): return AuditLog.objects.filter(user=self.request.user).order_by("-created_at")

class TelegramAuthThrottle(ScopedRateThrottle): scope = "telegram_auth"

@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
def telegram_auth_start(request):
    phone = serializers.CharField(max_length=32).run_validation(request.data.get("phone_number"))
    challenge = TelegramAuthService().start(request.user, phone)
    return ok({"challenge_id": str(challenge.id), "status": challenge.state, "expires_at": challenge.expires_at}, status=202)

@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
def telegram_auth_verify(request):
    try: challenge = TelegramAuthService().verify(request.user, request.data.get("challenge_id"), serializers.CharField(max_length=12).run_validation(request.data.get("code")))
    except TelegramAuthError as exc: raise serializers.ValidationError(str(exc)) from exc
    return ok({"challenge_id": str(challenge.id), "status": challenge.state})

@api_view(["POST"])
@throttle_classes([TelegramAuthThrottle])
def telegram_auth_2fa(request):
    password = serializers.CharField(write_only=True, max_length=256).run_validation(request.data.get("password"))
    try: challenge = TelegramAuthService().verify_2fa(request.user, request.data.get("challenge_id"), password)
    except TelegramAuthError as exc: raise serializers.ValidationError(str(exc)) from exc
    return ok({"challenge_id": str(challenge.id), "status": challenge.state})
