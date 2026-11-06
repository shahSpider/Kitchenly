"""Branch menu resolution, cached in Redis (read constantly, changes rarely)."""
from django.conf import settings
from django.core.cache import cache

from .models import BranchMenuItem, MenuCategory, MenuItem


def _ver_key(org_id):
    return f"menu:ver:{org_id}"


def bump_menu_version(org_id):
    try:
        cache.incr(_ver_key(org_id))
    except ValueError:
        cache.set(_ver_key(org_id), 2, None)


def resolve_item(branch_id, menu_item_id):
    """(price_minor, is_available) effective for a branch."""
    item = MenuItem.objects.get(pk=menu_item_id)
    override = BranchMenuItem.objects.filter(branch_id=branch_id, menu_item_id=menu_item_id).first()
    price = item.base_price_minor if override is None or override.price_minor is None else override.price_minor
    available = item.is_active and (override.is_available if override else True)
    return item, price, available


def get_branch_menu(organization_id, branch_id):
    ver = cache.get(_ver_key(organization_id), 1)
    key = f"menu:{organization_id}:{branch_id}:{ver}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    overrides = {b.menu_item_id: b for b in BranchMenuItem.objects.filter(branch_id=branch_id)}
    items_by_cat = {}
    for item in MenuItem.objects.filter(is_active=True).order_by("name"):
        ov = overrides.get(item.id)
        items_by_cat.setdefault(item.category_id, []).append({
            "id": str(item.id), "sku": item.sku, "name": item.name, "description": item.description,
            "price_minor": item.base_price_minor if ov is None or ov.price_minor is None else ov.price_minor,
            "tax_code": item.tax_code,
            "is_available": ov.is_available if ov else True,
        })
    data = [{"id": str(c.id), "name": c.name, "items": items_by_cat.get(c.id, [])}
            for c in MenuCategory.objects.order_by("sort_order", "name")]
    cache.set(key, data, settings.MENU_CACHE_TTL)
    return data
