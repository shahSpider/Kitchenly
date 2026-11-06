from django.conf import settings
from django.db import models

from apps.core.models import TenantModel


class Order(TenantModel):
    tenant_branch_lookup = "branch_id"

    class Status(models.TextChoices):
        OPEN = "open"
        PREPARING = "preparing"
        READY = "ready"
        SERVED = "served"
        COMPLETED = "completed"
        VOIDED = "voided"

    class Type(models.TextChoices):
        DINE_IN = "dine_in"
        TAKEAWAY = "takeaway"
        DELIVERY = "delivery"

    branch = models.ForeignKey("tenants.Branch", on_delete=models.PROTECT, related_name="orders")
    # NULL = entered by hand by staff.
    pos_connection = models.ForeignKey("pos.PosConnection", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="orders")
    external_id = models.CharField(max_length=128, blank=True, null=True)
    order_number = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    order_type = models.CharField(max_length=20, choices=Type.choices, default=Type.DINE_IN)
    subtotal_minor = models.BigIntegerField(default=0)
    tax_minor = models.BigIntegerField(default=0)
    discount_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3)
    placed_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    # Version guard: an older POS event never overwrites a newer one.
    pos_updated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pos_connection", "external_id"], name="uniq_order_pos_external"),
        ]
        indexes = [
            models.Index(fields=["branch", "placed_at"], name="order_branch_placed_idx"),
            models.Index(fields=["organization", "placed_at"], name="order_org_placed_idx"),
        ]
        ordering = ["-placed_at"]


class OrderItem(TenantModel):
    tenant_branch_lookup = "order__branch_id"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    menu_item = models.ForeignKey("menu.MenuItem", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    name_snapshot = models.CharField(max_length=200)
    sku_snapshot = models.CharField(max_length=64, blank=True)
    quantity = models.PositiveIntegerField()
    unit_price_minor = models.BigIntegerField()
    line_total_minor = models.BigIntegerField()
    external_id = models.CharField(max_length=128, blank=True, null=True)

    class Meta:
        indexes = [models.Index(fields=["order"], name="orderitem_order_idx")]


class Payment(TenantModel):
    tenant_branch_lookup = "order__branch_id"

    class Method(models.TextChoices):
        CASH = "cash"
        CARD = "card"
        ONLINE = "online"
        OTHER = "other"

    class Status(models.TextChoices):
        PENDING = "pending"
        CAPTURED = "captured"
        FAILED = "failed"
        PARTIALLY_REFUNDED = "partially_refunded"
        REFUNDED = "refunded"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=20, choices=Method.choices)
    amount_minor = models.BigIntegerField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.CAPTURED)
    reference = models.CharField(max_length=128, blank=True)
    external_id = models.CharField(max_length=128, blank=True, null=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "external_id"], name="uniq_payment_order_external")]
        indexes = [models.Index(fields=["order"], name="payment_order_idx")]


class Refund(TenantModel):
    """Refunds and voids. Staff request; a branch manager approves."""

    tenant_branch_lookup = "order__branch_id"

    class Kind(models.TextChoices):
        REFUND = "refund"
        VOID = "void"

    class Status(models.TextChoices):
        REQUESTED = "requested"
        APPROVED = "approved"
        REJECTED = "rejected"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="refunds")
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="refunds")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.REFUND)
    amount_minor = models.BigIntegerField(default=0)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                     related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["payment"], name="refund_payment_idx"),
                   models.Index(fields=["order"], name="refund_order_idx")]


class DayClose(TenantModel):
    """A branch manager closing the business day; stores a totals snapshot."""

    tenant_branch_lookup = "branch_id"

    branch = models.ForeignKey("tenants.Branch", on_delete=models.CASCADE, related_name="day_closes")
    business_date = models.DateField()
    order_count = models.PositiveIntegerField()
    total_minor = models.BigIntegerField()
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "business_date"], name="uniq_dayclose_branch_date")]
