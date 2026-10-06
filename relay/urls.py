from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from relay import views

router = DefaultRouter()
router.register("telegram/accounts", views.AccountViewSet, basename="accounts")
router.register("telegram/entities", views.EntityViewSet, basename="entities")
router.register("relays", views.RelayViewSet, basename="relays")
router.register("messages", views.MessageViewSet, basename="messages")
router.register("logs", views.AuditLogViewSet, basename="logs")
urlpatterns = [
    path("auth/register", views.register), path("auth/login", views.login), path("auth/logout", views.logout), path("auth/refresh", TokenRefreshView.as_view()), path("auth/me", views.me),
    path("telegram/auth/start", views.telegram_auth_start), path("telegram/auth/verify", views.telegram_auth_verify), path("telegram/auth/2fa", views.telegram_auth_2fa),
    path("", include(router.urls)),
]
