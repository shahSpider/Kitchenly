"""Shared API plumbing: tenant resolution + role/membership authorization.

Every tenant-aware view derives from `TenantViewMixin`. After authentication it:
  1. loads the caller's memberships,
  2. picks the organization (X-Organization header, or the only one),
  3. activates the tenant scope for the app layer (TenantManager) AND the
     database layer (RLS GUCs),
  4. builds an AccessContext used by `RolePolicy`.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import permissions
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from . import rls, tenancy
from .models import scope_queryset

ACTION_MAP = {
    "list": "read", "retrieve": "read", "create": "create",
    "update": "update", "partial_update": "update", "destroy": "delete",
}
METHOD_MAP = {"GET": "read", "HEAD": "read", "OPTIONS": "read", "POST": "create",
              "PUT": "update", "PATCH": "update", "DELETE": "delete"}


def client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    hops = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if xff and hops:
        parts = [p.strip() for p in xff.split(",")]
        return parts[-hops] if len(parts) >= hops else parts[0]
    return request.META.get("REMOTE_ADDR")


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


class RolePolicy(permissions.BasePermission):
    """Role says WHAT, membership says WHERE; both must agree."""

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        resource = getattr(view, "resource", None)
        if resource is None:
            return True
        ctx = getattr(request, "access", None)
        if ctx is None:
            return False
        if not getattr(view, "mfa_exempt", False) and not getattr(request, "mfa_ok", True):
            self.message = "Multi-factor authentication is required for your role."
            return False
        action = view.get_required_action()
        return bool(action) and ctx.allows(resource, action)

    def has_object_permission(self, request, view, obj):
        ctx = request.access
        action = view.get_required_action()
        return bool(action) and ctx.allows(view.resource, action, view.get_object_branch_id(obj))


class TenantViewMixin:
    resource = None
    required_action = None  # set per @action(...); unknown custom actions are denied
    requires_organization = True
    mfa_exempt = False
    branch_attr = "branch_id"  # dotted path from an object to its branch id
    permission_classes = [permissions.IsAuthenticated, RolePolicy]

    # -- authorization helpers -------------------------------------------------
    def get_required_action(self):
        if self.required_action:
            return self.required_action
        action = getattr(self, "action", None)
        if action:
            return ACTION_MAP.get(action)  # None (deny) for undeclared custom actions
        return METHOD_MAP.get(self.request.method)

    def get_object_branch_id(self, obj):
        if not self.branch_attr:
            return None
        for part in self.branch_attr.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                return None
        return obj

    # -- tenant resolution -----------------------------------------------------
    def perform_authentication(self, request):
        request.user  # noqa: B018 - triggers the authenticators
        if request.user.is_authenticated:
            self.setup_tenant(request)

    def _resolve_org(self, request, user, memberships):
        from apps.tenants.models import Organization

        header = request.headers.get("X-Organization")
        if user.is_platform_admin:
            if not header:
                return None
            as_uuid = _parse_uuid(header)
            qs = Organization.all_objects.filter(id=as_uuid) if as_uuid else Organization.all_objects.filter(slug=header)
            org = qs.first()
            if org is None:
                raise NotFound("Organization not found.")
            return org
        orgs = {}
        for m in memberships:
            orgs[m.organization_id] = m.organization
        if header:
            as_uuid = _parse_uuid(header)
            for org in orgs.values():
                if org.id == as_uuid or org.slug == header:
                    return org
            raise PermissionDenied("You are not a member of that organization.")
        if len(orgs) == 1:
            return next(iter(orgs.values()))
        if not orgs:
            if self.requires_organization:
                raise PermissionDenied("You do not belong to any organization.")
            return None
        if self.requires_organization:
            raise ValidationError({"organization": "Send the X-Organization header (id or slug)."})
        return None

    def setup_tenant(self, request):
        from apps.accounts.access import AccessContext
        from apps.accounts.models import Membership
        from apps.audit import services as audit

        user = request.user
        with rls.bypass():
            memberships = list(
                Membership.all_objects.filter(user=user, is_active=True).select_related("role", "organization")
            )
            org = self._resolve_org(request, user, memberships)
        request.all_memberships = memberships
        request.organization = org
        token = getattr(request.auth, "payload", None) if request.auth is not None else None
        request.mfa_ok = True if token is None else bool(token.get("mfa", False)) or not settings.MFA_ENFORCED

        if org is not None and org.status != "active" and not user.is_platform_admin:
            raise PermissionDenied("This organization is suspended.")

        in_org = [m for m in memberships if org is not None and m.organization_id == org.id]
        request.access = AccessContext(user=user, organization=org, memberships=in_org)

        if user.is_platform_admin:
            scope = tenancy.TenantScope(organization_id=org.id) if org else tenancy.TenantScope(bypass=True)
        elif org is None:
            scope = tenancy.TenantScope()  # fail closed: sees no tenant rows
        else:
            branch_ids = None if any(m.branch_id is None for m in in_org) else frozenset(m.branch_id for m in in_org)
            scope = tenancy.TenantScope(organization_id=org.id, branch_ids=branch_ids)
        self._scope_token = rls.activate(scope)
        self._actor_token = audit.set_actor(user.pk, client_ip(request))

        if user.is_platform_admin and self.resource not in (None, "pos_provider", "role"):
            audit.write("support.access", "http", f"{request.method} {request.path}",
                        organization_id=org.id if org else None)

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        finally:
            tenancy.clear_scope()
            from apps.audit import services as audit

            audit._actor.set(None)

    # -- query scoping ---------------------------------------------------------
    def get_queryset(self):
        qs = super().get_queryset()
        model = qs.model
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation has no tenant
            return qs.none()
        if hasattr(model, "tenant_org_lookup"):
            if qs.query.is_empty():
                raise ImproperlyConfigured(
                    f"{type(self).__name__}.queryset was built from `{model.__name__}.objects` at import time, "
                    "before a tenant scope exists, so it is permanently empty. Use "
                    f"`{model.__name__}.all_objects` at class level; the tenant scope is applied per request.")
            qs = scope_queryset(qs)
        lookup = getattr(model, "tenant_branch_lookup", None)
        if lookup and self.resource:
            branches = self.request.access.branches_for(self.resource, "read")
            if branches is not None:
                qs = qs.filter(**{f"{lookup}__in": branches})
        return qs

    def check_branch_permission(self, branch, action=None):
        """Writing into a branch requires the right role AT that branch."""
        if branch is None:
            return
        action = action or self.get_required_action()
        if not self.request.access.allows(self.resource, action, branch.id):
            raise PermissionDenied("You cannot do this at that branch.")
