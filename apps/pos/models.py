
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedJSONField, EncryptedTextField
from apps.core.models import TenantModel, UUIDModel


class PosProvider(UUIDModel):
    """A supported POS vendor (global). Platform admins manage these."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    supports_webhook = models.BooleanField(default=False)
    supports_pull = models.BooleanField(default=False)
    supports_file = models.BooleanField(default=False)
    supports_push = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code


class PosConnection(TenantModel):
    tenant_branch_lookup = "branch_id"

    class SyncMode(models.TextChoices):
        WEBHOOK = "webhook"
        PULL = "pull"
        FILE = "file"

    class Status(models.TextChoices):
        ACTIVE = "active"
        PAUSED = "paused"
        ERROR = "error"

    branch = models.ForeignKey("tenants.Branch", on_delete=models.CASCADE, related_name="pos_connections")
    provider = models.ForeignKey(PosProvider, on_delete=models.PROTECT, related_name="connections")
    name = models.CharField(max_length=120, blank=True)
    credentials_encrypted = EncryptedJSONField(null=True, blank=True)
    webhook_secret = EncryptedTextField(blank=True, default="")
    config = models.JSONField(default=dict, blank=True)  # non-secret settings (e.g. base_url)
    sync_mode = models.CharField(max_length=10, choices=SyncMode.choices, default=SyncMode.WEBHOOK)
    push_enabled = models.BooleanField(default=False)  # two-way: this system is master for menu/prices
    pull_interval_minutes = models.PositiveIntegerField(default=5)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    cursor = models.TextField(blank=True, default="")
    next_pull_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["organization", "branch"], name="posconn_org_branch_idx"),
                   models.Index(fields=["status", "sync_mode"], name="posconn_status_mode_idx")]

    def __str__(self):
        return self.name or f"{self.provider.code}@{self.branch_id}"


class SyncEvent(TenantModel):
    """Every inbound/outbound POS event, stored once (idempotent) then processed."""

    tenant_branch_lookup = "pos_connection__branch_id"

    class Direction(models.TextChoices):
        INBOUND = "in"
        OUTBOUND = "out"

    class Source(models.TextChoices):
        WEBHOOK = "webhook"
        PULL = "pull"
        FILE = "file"
        PUSH = "push"

    class Status(models.TextChoices):
        RECEIVED = "received"
        PROCESSING = "processing"
        PROCESSED = "processed"
        FAILED = "failed"
        DEAD = "dead"  # dead-letter: out of retries, needs a manager's review

    pos_connection = models.ForeignKey(PosConnection, on_delete=models.CASCADE, related_name="sync_events")
    direction = models.CharField(max_length=3, choices=Direction.choices, default=Direction.INBOUND)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.WEBHOOK)
    kind = models.CharField(max_length=80)
    event_id = models.CharField(max_length=190)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED)
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pos_connection", "kind", "event_id"],
                                               name="uniq_syncevent_conn_kind_event")]
        indexes = [models.Index(fields=["status", "received_at"], name="syncevent_status_recv_idx"),
                   models.Index(fields=["organization", "received_at"], name="syncevent_org_recv_idx")]


class ExternalRef(TenantModel):
    """Maps our menu items / orders to the POS's own ids."""

    class EntityType(models.TextChoices):
        MENU_ITEM = "menu_item"
        ORDER = "order"

    tenant_branch_lookup = "pos_connection__branch_id"

    pos_connection = models.ForeignKey(PosConnection, on_delete=models.CASCADE, related_name="external_refs")
    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    local_id = models.UUIDField()
    external_id = models.CharField(max_length=190)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pos_connection", "entity_type", "external_id"],
                                               name="uniq_extref_conn_type_ext")]
        indexes = [models.Index(fields=["pos_connection", "entity_type", "local_id"], name="extref_local_idx")]


class ImportJob(TenantModel):
    class Status(models.TextChoices):
        UPLOADED = "uploaded"
        PROCESSING = "processing"
        DONE = "done"
        FAILED = "failed"

    tenant_branch_lookup = "pos_connection__branch_id"

    pos_connection = models.ForeignKey(PosConnection, on_delete=models.CASCADE, related_name="imports")
    file = models.FileField(upload_to="imports/%Y/%m/")
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADED)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)  # capped list of {row, error}
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pos_connection", "sha256"], name="uniq_import_conn_hash")]


class ReconciliationReport(TenantModel):
    """Nightly comparison of POS totals vs stored totals for a branch-day."""

    tenant_branch_lookup = "pos_connection__branch_id"

    pos_connection = models.ForeignKey(PosConnection, on_delete=models.CASCADE, related_name="reconciliations")
    business_date = models.DateField()
    pos_order_count = models.IntegerField()
    pos_total_minor = models.BigIntegerField()
    local_order_count = models.IntegerField()
    local_total_minor = models.BigIntegerField()
    is_match = models.BooleanField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pos_connection", "business_date"],
                                               name="uniq_recon_conn_date")]
        ordering = ["-business_date"]
