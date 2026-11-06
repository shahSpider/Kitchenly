from django.db import models, transaction

from apps.core.models import TenantModel, TenantScopedModel


class Organization(TenantScopedModel):
    """A restaurant group (the tenant). Scoped by its own id."""

    tenant_org_lookup = "id"

    class Status(models.TextChoices):
        ACTIVE = "active"
        SUSPENDED = "suspended"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    currency = models.CharField(max_length=3, default="USD")  # ISO 4217
    timezone = models.CharField(max_length=64, default="UTC")
    plan = models.CharField(max_length=50, default="standard")
    max_branches = models.PositiveIntegerField(default=50)
    # tax_code -> basis points, e.g. {"standard": 1500}
    tax_rates = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class Branch(TenantModel):
    tenant_branch_lookup = "id"

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=40)
    address = models.TextField(blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    is_active = models.BooleanField(default=True)
    order_seq = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="uniq_branch_org_code")]
        indexes = [models.Index(fields=["organization"], name="branch_org_idx")]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @classmethod
    def next_order_number(cls, branch_id) -> int:
        """Per-branch sequential number. Row lock keeps it gap-free and unique."""
        with transaction.atomic():
            b = cls.all_objects.select_for_update().only("id", "order_seq").get(pk=branch_id)
            b.order_seq += 1
            b.save(update_fields=["order_seq"])
            return b.order_seq
