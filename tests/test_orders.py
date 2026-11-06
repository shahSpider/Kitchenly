from apps.core import rls
from tests import factories as f
from tests.conftest import call


def _new(c, branch, items, **extra):
    return call(c, "post", "/api/v1/orders/", {"branch": str(branch.pk), "items": items, **extra})


def test_prices_come_from_server_with_branch_override_and_tax(api, world):
    with rls.bypass():
        world.a.tax_rates = {"std": 1000}  # 10%
        world.a.save()
    burger = f.menu_item(world.a, price=1000, tax_code="std")
    f.override(world.a, world.a1, burger, price=1200)
    fries = f.menu_item(world.a, price=300)
    c = api.login(world.a_staff, world.a)
    r = _new(c, world.a1, [{"menu_item": str(burger.pk), "quantity": 2}, {"menu_item": str(fries.pk), "quantity": 1}])
    assert r.status_code == 201, r.content
    assert r.data["subtotal_minor"] == 2 * 1200 + 300
    assert r.data["tax_minor"] == 240
    assert r.data["total_minor"] == 2700 + 240
    assert r.data["currency"] == "USD" and r.data["status"] == "open"
    assert {i["unit_price_minor"] for i in r.data["items"]} == {1200, 300}


def test_client_cannot_supply_prices(api, world):
    item = f.menu_item(world.a, price=1000)
    c = api.login(world.a_staff, world.a)
    r = _new(c, world.a1, [{"menu_item": str(item.pk), "quantity": 1, "unit_price_minor": 1}])
    assert r.data["items"][0]["unit_price_minor"] == 1000


def test_history_is_a_snapshot_of_menu_changes(api, world):
    item = f.menu_item(world.a, price=1000, name="Burger")
    c = api.login(world.a_staff, world.a)
    oid = _new(c, world.a1, [{"menu_item": str(item.pk), "quantity": 1}]).data["id"]
    mgr = api.login(world.a_org_mgr, world.a)
    assert call(mgr, "patch", f"/api/v1/menu/items/{item.pk}/", {"name": "Mega Burger", "base_price_minor": 9999}).status_code == 200
    got = call(c, "get", f"/api/v1/orders/{oid}/").data
    assert got["items"][0]["name_snapshot"] == "Burger" and got["items"][0]["unit_price_minor"] == 1000
    assert got["total_minor"] == 1000


def test_unavailable_item_rejected_and_staff_can_toggle(api, world):
    item = f.menu_item(world.a)
    f.override(world.a, world.a1, item, available=False)
    c = api.login(world.a_staff, world.a)
    assert _new(c, world.a1, [{"menu_item": str(item.pk), "quantity": 1}]).status_code == 400
    r = call(c, "post", "/api/v1/branch-menu-items/availability/", {"branch": str(world.a1.pk), "menu_item": str(item.pk), "is_available": True})
    assert r.status_code == 200
    assert _new(c, world.a1, [{"menu_item": str(item.pk), "quantity": 1}]).status_code == 201
    # staff may not change prices
    row = r.data["id"]
    assert call(c, "patch", f"/api/v1/branch-menu-items/{row}/", {"price_minor": 1}).status_code == 403
    # ...nor toggle at another branch
    r = call(c, "post", "/api/v1/branch-menu-items/availability/", {"branch": str(world.a2.pk), "menu_item": str(item.pk), "is_available": False})
    assert r.status_code in (400, 403)


def test_order_numbers_are_sequential_per_branch(api, world):
    item = f.menu_item(world.a)
    c = api.login(world.a_staff, world.a)
    nums = [_new(c, world.a1, [{"menu_item": str(item.pk), "quantity": 1}]).data["order_number"] for _ in range(3)]
    assert nums == ["1", "2", "3"]


def test_default_branch_when_only_one(api, world):
    item = f.menu_item(world.a)
    c = api.login(world.a_staff, world.a)
    r = call(c, "post", "/api/v1/orders/", {"items": [{"menu_item": str(item.pk), "quantity": 1}]})
    assert r.status_code == 201 and str(r.data["branch"]) == str(world.a1.pk)


def test_payments_and_status_flow(api, world):
    item = f.menu_item(world.a, price=1000)
    c = api.login(world.a_staff, world.a)
    o = _new(c, world.a1, [{"menu_item": str(item.pk), "quantity": 1}]).data
    url = f"/api/v1/orders/{o['id']}"
    assert call(c, "post", f"{url}/payments/", {"method": "cash", "amount_minor": 1500}).status_code == 400   # overpay
    assert call(c, "post", f"{url}/payments/", {"method": "cash", "amount_minor": 400}).status_code == 201
    assert call(c, "post", f"{url}/payments/", {"method": "card", "amount_minor": 600}).status_code == 201
    assert call(c, "post", f"{url}/status/", {"status": "completed"}).data["closed_at"]
    assert call(c, "post", f"{url}/status/", {"status": "open"}).status_code == 400    # no going back
    assert call(c, "post", f"{url}/status/", {"status": "voided"}).status_code == 400  # void needs approval
    assert len(call(c, "get", f"{url}/").data["payments"]) == 2


def test_staff_cannot_edit_or_delete_orders_directly(api, world):
    o = f.order(world.a, world.a1)
    c = api.login(world.a_staff, world.a)
    assert call(c, "patch", f"/api/v1/orders/{o.pk}/", {"notes": "x"}).status_code == 403
    assert call(c, "delete", f"/api/v1/orders/{o.pk}/").status_code == 403


def test_branch_manager_updates_discount_and_deletes_open_orders(api, world):
    o = f.order(world.a, world.a1, total=2000)
    c = api.login(world.a_br_mgr, world.a)
    r = call(c, "patch", f"/api/v1/orders/{o.pk}/", {"discount_minor": 500, "notes": "VIP"})
    assert r.status_code == 200 and r.data["total_minor"] == 1500
    assert call(c, "patch", f"/api/v1/orders/{o.pk}/", {"discount_minor": 99999}).status_code == 400
    assert call(c, "delete", f"/api/v1/orders/{o.pk}/").status_code == 204


def test_refund_requires_manager_approval(api, world):
    o = f.order(world.a, world.a1, total=1000)
    pay = f.payment(o)
    staff, mgr = api.login(world.a_staff, world.a), api.login(world.a_br_mgr, world.a)
    r = call(staff, "post", "/api/v1/refunds/", {"order": str(o.pk), "payment": str(pay.pk), "amount_minor": 400, "reason": "cold"})
    assert r.status_code == 201 and r.data["status"] == "requested"
    rid = r.data["id"]
    assert call(staff, "post", f"/api/v1/refunds/{rid}/approve/").status_code == 403     # staff cannot approve
    assert call(staff, "get", "/api/v1/refunds/").status_code == 403
    assert call(mgr, "post", f"/api/v1/refunds/{rid}/approve/").data["status"] == "approved"
    assert call(mgr, "post", f"/api/v1/refunds/{rid}/approve/").status_code == 400       # already decided
    # over-refund guard
    r2 = call(staff, "post", "/api/v1/refunds/", {"order": str(o.pk), "payment": str(pay.pk), "amount_minor": 700})
    assert call(mgr, "post", f"/api/v1/refunds/{r2.data['id']}/approve/").status_code == 400
    # amount larger than payment is refused up front
    assert call(staff, "post", "/api/v1/refunds/", {"order": str(o.pk), "payment": str(pay.pk), "amount_minor": 5000}).status_code == 400


def test_void_flow(api, world):
    o = f.order(world.a, world.a1, total=1000)
    staff, mgr = api.login(world.a_staff, world.a), api.login(world.a_br_mgr, world.a)
    r = call(staff, "post", "/api/v1/refunds/", {"order": str(o.pk), "kind": "void", "reason": "mistake"})
    assert r.status_code == 201
    call(mgr, "post", f"/api/v1/refunds/{r.data['id']}/approve/")
    assert call(mgr, "get", f"/api/v1/orders/{o.pk}/").data["status"] == "voided"
    # voided orders drop out of sales
    assert call(mgr, "get", "/api/v1/reports/sales-summary/").data["totals"]["orders"] == 0


def test_staff_cannot_request_refund_for_other_branch(api, world):
    o = f.order(world.a, world.a2)
    staff = api.login(world.a_staff, world.a)
    r = call(staff, "post", "/api/v1/refunds/", {"order": str(o.pk), "kind": "void"})
    assert r.status_code == 400  # order is invisible to this person


def test_close_day(api, world):
    f.order(world.a, world.a1, total=1500, status="completed")
    mgr = api.login(world.a_br_mgr, world.a)
    r = call(mgr, "post", f"/api/v1/branches/{world.a1.pk}/close-day/", {})
    assert r.status_code == 201 and r.data["order_count"] == 1 and r.data["total_minor"] == 1500
    assert call(mgr, "post", f"/api/v1/branches/{world.a1.pk}/close-day/", {}).status_code == 409
    staff = api.login(world.a_staff, world.a)
    assert call(staff, "post", f"/api/v1/branches/{world.a1.pk}/close-day/", {}).status_code == 403


def test_close_day_blocked_by_open_orders(api, world):
    f.order(world.a, world.a1, status="open")
    mgr = api.login(world.a_br_mgr, world.a)
    assert call(mgr, "post", f"/api/v1/branches/{world.a1.pk}/close-day/", {}).status_code == 400


def test_reports(api, world):
    o = f.order(world.a, world.a1, total=1000, status="completed")
    f.payment(o, method="card")
    f.order(world.a, world.a2, total=500, status="completed")
    mgr = api.login(world.a_org_mgr, world.a)
    r = call(mgr, "get", "/api/v1/reports/sales-summary/").data
    assert r["totals"]["orders"] == 2 and r["totals"]["total"] == 1500
    by_branch = call(mgr, "get", "/api/v1/reports/sales-summary/?group_by=branch").data["groups"]
    assert {g["branch"]: g["total_minor"] for g in by_branch} == {str(world.a1.pk): 1000, str(world.a2.pk): 500}
    assert call(mgr, "get", "/api/v1/reports/top-items/").data["items"][0]["name"] == "Burger"
    assert call(mgr, "get", "/api/v1/reports/payment-methods/").data["methods"][0]["method"] == "card"
    assert call(mgr, "get", "/api/v1/reports/sales-summary/?from=2026-02-01&to=2025-01-01").status_code == 400


def test_branch_menu_view_resolves_overrides(api, world):
    a = f.menu_item(world.a, price=1000, name="A")
    b = f.menu_item(world.a, price=500, name="B")
    f.override(world.a, world.a1, a, price=800)
    f.override(world.a, world.a1, b, available=False)
    c = api.login(world.a_staff, world.a)
    cats = call(c, "get", f"/api/v1/branches/{world.a1.pk}/menu/").data["categories"]
    items = {i["name"]: i for cat in cats for i in cat["items"]}
    assert items["A"]["price_minor"] == 800 and items["B"]["is_available"] is False
    assert call(c, "get", f"/api/v1/branches/{world.a2.pk}/menu/").status_code == 404
    # cache is invalidated when the menu changes
    mgr = api.login(world.a_org_mgr, world.a)
    call(mgr, "patch", f"/api/v1/menu/items/{a.pk}/", {"name": "A2"})
    cats = call(c, "get", f"/api/v1/branches/{world.a1.pk}/menu/").data["categories"]
    assert "A2" in {i["name"] for cat in cats for i in cat["items"]}
