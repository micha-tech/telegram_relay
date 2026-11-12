"""Tenant resolution helpers.

The product is multi-tenant with one workspace per user (``Membership.user`` is a
one-to-one). Every Telegram record is reached through ``TelegramAccount.tenant``.

Global Django superusers with no membership are treated as platform operators and
see every tenant; everyone else is confined to their own.
"""

from __future__ import annotations

import secrets

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from relay.models import Invite, Membership, Tenant

INVITE_TTL_DAYS = 7


def membership_for(user) -> Membership | None:
    if not user or not user.is_authenticated:
        return None
    return Membership.objects.select_related("tenant").filter(user=user).first()


def tenant_for(user):
    membership = membership_for(user)
    return membership.tenant if membership else None


def role_for(user) -> str | None:
    membership = membership_for(user)
    return membership.role if membership else None


def is_tenant_admin(user) -> bool:
    """Workspace admins plus platform superusers."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return Membership.objects.filter(user=user, role__in=Membership.ADMIN_ROLES).exists()


def is_tenant_owner(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return Membership.objects.filter(user=user, role=Membership.Role.OWNER).exists()


def unique_slug(name: str) -> str:
    base = slugify(name)[:60] or "workspace"
    candidate = base
    suffix = 2
    while Tenant.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@transaction.atomic
def create_tenant(name: str, owner) -> Tenant:
    """Create a workspace and make ``owner`` its first OWNER."""
    name = (name or "").strip() or f"{owner.email}'s workspace"
    for attempt in range(5):
        try:
            tenant = Tenant.objects.create(name=name[:255], slug=unique_slug(name))
            break
        except IntegrityError:
            if attempt == 4:
                raise
            continue
    Membership.objects.create(user=owner, tenant=tenant, role=Membership.Role.OWNER)
    return tenant


def ensure_membership(user) -> Membership:
    """Return the user's membership, creating a personal workspace if missing.

    Legacy accounts (created before tenancy) and platform superusers have no
    membership until their first write; this keeps that path working.
    """
    membership = membership_for(user)
    if membership is not None:
        return membership
    create_tenant(f"{user.email}'s workspace", user)
    return membership_for(user)


def issue_invite(
    tenant, created_by, *, role: str, email: str = "", days: int = INVITE_TTL_DAYS
) -> Invite:
    return Invite.objects.create(
        tenant=tenant,
        created_by=created_by,
        role=role,
        email=(email or "").strip().lower(),
        code=secrets.token_urlsafe(24),
        expires_at=timezone.now() + timezone.timedelta(days=days),
    )


def tenant_scope(user, field: str) -> Q | None:
    """Build a ``Q`` limiting ``field`` to the user's tenant.

    Returns ``None`` when the caller is a platform operator without a workspace
    (meaning: no restriction), and an unsatisfiable ``Q`` for users who are not
    in any tenant.
    """
    membership = membership_for(user)
    if membership is not None:
        return Q(**{field: membership.tenant_id})
    if getattr(user, "is_superuser", False):
        return None
    return Q(pk__in=[])


def apply_scope(queryset, user, field: str):
    condition = tenant_scope(user, field)
    if condition is None:
        return queryset
    return queryset.filter(condition)


def can_access_tenant(user, tenant_id) -> bool:
    """True when the user may act on records belonging to ``tenant_id``."""
    membership = membership_for(user)
    if membership is not None:
        return str(membership.tenant_id) == str(tenant_id)
    return bool(getattr(user, "is_superuser", False))
