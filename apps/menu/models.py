from django.db import models

from apps.core.models import TenantModel


class MenuCategory(TenantModel):
    name = models.CharField(max_length=120)
    sort_order = models.IntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["organization"], name="menucat_org_idx")]
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class MenuItem(TenantModel):
    """Master menu (organization level)."""

    category = models.ForeignKey(MenuCategory, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    sku = models.CharField(max_length=64)
    base_price_minor = models.PositiveBigIntegerField()
    tax_code = models.CharField(max_length=32, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "sku"], name="uniq_menuitem_org_sku")]
        indexes = [models.Index(fields=["organization", "category"], name="menuitem_org_cat_idx")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class BranchMenuItem(TenantModel):
    """Per-branch price override and availability."""

    tenant_branch_lookup = "branch_id"

    branch = models.ForeignKey("tenants.Branch", on_delete=models.CASCADE, related_name="menu_items")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="branch_items")
    price_minor = models.PositiveBigIntegerField(null=True, blank=True)  # NULL = base price
    is_available = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "menu_item"], name="uniq_branchmenu_branch_item")]
        indexes = [models.Index(fields=["organization", "branch"], name="branchmenu_org_branch_idx")]

    @property
    def effective_price_minor(self):
        return self.menu_item.base_price_minor if self.price_minor is None else self.price_minor
