from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    """Restrict an endpoint to super administrators.

    Telegram account connection and signal source management are operator-only
    surfaces; every other authenticated user consumes the captured signals.
    """

    message = "This action is restricted to super administrators."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_superuser)
