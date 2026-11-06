import uuid

from django.db import models

from . import tenancy


def scope_queryset(qs):
    """Apply the active tenant scope to a queryset (fails closed)."""
    model = qs.model
    scope = tenancy.get_scope()
    if scope is None:
        return qs.none()
    if scope.bypass:
        return qs
    if scope.organization_id is None:
        return qs.none()
    qs = qs.filter(**{model.tenant_org_lookup: scope.organization_id})
    branch_lookup = model.tenant_branch_lookup
    if branch_lookup and scope.branch_ids is not None:
        qs = qs.filter(**{f"{branch_lookup}__in": scope.branch_ids})
    return qs


class TenantManager(models.Manager):
    """Adds the organization (and branch) filter to every query, failing closed.

    Layer 1 of the tenant isolation described in the design doc. Code that
    legitimately needs unscoped rows (workers, auth) uses `Model.all_objects`
    inside an explicit `rls.bypass()` / `rls.tenant_context()` block.
    """

    def get_queryset(self):
        return scope_queryset(super().get_queryset())


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(UUIDModel):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantScopedModel(TimestampedModel):  # noqa: DJ012
    # How a row maps to its organization / branch, used by TenantManager.
    tenant_org_lookup = "organization_id"
    tenant_branch_lookup = None  # e.g. "branch_id", "order__branch_id"

    objects = TenantManager()
    all_objects = models.Manager()  # noqa: DJ012

    class Meta:
        abstract = True


class TenantModel(TenantScopedModel):
    organization = models.ForeignKey(
        "tenants.Organization", on_delete=models.PROTECT, related_name="%(app_label)s_%(class)s_set"
    )

    class Meta:
        abstract = True
