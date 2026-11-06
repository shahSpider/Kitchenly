import uuid

from django.db import models

from apps.core.models import TenantManager


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("audit_log is append-only")

    def delete(self):
        raise PermissionError("audit_log is append-only")


class AuditLog(models.Model):
    """Append-only (also enforced by a database trigger, see migration 0002).

    actor / organization are plain UUIDs (no FK) so history survives deletions."""

    tenant_org_lookup = "organization_id"
    tenant_branch_lookup = None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_id = models.UUIDField(null=True, blank=True)
    organization_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=60)
    entity = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=255, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager.from_queryset(AppendOnlyQuerySet)()
    all_objects = models.Manager.from_queryset(AppendOnlyQuerySet)()

    class Meta:
        indexes = [models.Index(fields=["organization_id", "created_at"], name="audit_org_created_idx")]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("audit_log is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("audit_log is append-only")
