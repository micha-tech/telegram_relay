from rest_framework.permissions import BasePermission

from relay.tenancy import is_tenant_admin, is_tenant_owner


class IsTenantAdmin(BasePermission):
    """Restrict an endpoint to workspace administrators (OWNER/ADMIN).

    Telegram account connection and signal source management belong to each
    workspace's admins. Global Django superusers are platform operators and keep
    access for support purposes.
    """

    message = "This action requires workspace administrator rights."

    def has_permission(self, request, view):
        return is_tenant_admin(getattr(request, "user", None))


class IsTenantOwner(BasePermission):
    """Restrict an endpoint to the workspace owner."""

    message = "This action requires workspace owner rights."

    def has_permission(self, request, view):
        return is_tenant_owner(getattr(request, "user", None))


class IsSuperAdmin(IsTenantAdmin):
    """Backwards-compatible alias kept for older imports."""
