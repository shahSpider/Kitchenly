"""Role x membership matrix from the design doc."""
from tests import factories as f
from tests.conftest import call


def test_branch_staff_cannot_manage_structure(api, world):
    c = api.login(world.a_staff, world.a)
    assert call(c, "post", "/api/v1/branches/", {"name": "X", "code": "x"}).status_code == 403
    assert call(c, "patch", f"/api/v1/branches/{world.a1.pk}/", {"name": "n"}).status_code == 403
    assert call(c, "post", "/api/v1/menu/items/", {}).status_code == 403
    assert call(c, "get", "/api/v1/pos/connections/").status_code == 403
    assert call(c, "get", "/api/v1/reports/sales-summary/").status_code == 403
    assert call(c, "get", "/api/v1/memberships/").status_code == 403


def test_branch_staff_sees_only_own_branch(api, world):
    f.order(world.a, world.a1)
    other = f.order(world.a, world.a2)
    c = api.login(world.a_staff, world.a)
    ids = {r["id"] for r in call(c, "get", "/api/v1/orders/").data["results"]}
    assert str(other.pk) not in ids and len(ids) == 1
    assert call(c, "get", f"/api/v1/orders/{other.pk}/").status_code == 404
    assert {b["id"] for b in call(c, "get", "/api/v1/branches/").data["results"]} == {str(world.a1.pk)}


def test_staff_cannot_create_order_in_another_branch(api, world):
    item = f.menu_item(world.a)
    c = api.login(world.a_staff, world.a)
    r = call(c, "post", "/api/v1/orders/", {"branch": str(world.a2.pk), "items": [{"menu_item": str(item.pk), "quantity": 1}]})
    assert r.status_code in (400, 403, 404)


def test_branch_manager_scope(api, world):
    c = api.login(world.a_br_mgr, world.a)
    assert call(c, "patch", f"/api/v1/branches/{world.a1.pk}/", {"name": "Renamed"}).status_code == 200
    assert call(c, "patch", f"/api/v1/branches/{world.a2.pk}/", {"name": "Nope"}).status_code == 404
    assert call(c, "post", "/api/v1/menu/items/", {}).status_code == 403          # master menu: read only
    assert call(c, "get", "/api/v1/menu/items/").status_code == 200
    assert call(c, "post", "/api/v1/pos/connections/", {}).status_code == 403    # POS connections: read only
    assert call(c, "get", "/api/v1/pos/connections/").status_code == 200
    assert call(c, "get", "/api/v1/reports/sales-summary/").status_code == 200
    assert call(c, "get", "/api/v1/reports/sales-summary/", ).data["totals"]["orders"] == 0


def test_branch_manager_cannot_read_other_branch_report(api, world):
    c = api.login(world.a_br_mgr, world.a)
    r = call(c, "get", f"/api/v1/reports/sales-summary/?branch={world.a2.pk}")
    assert r.status_code == 400


def test_org_manager_cannot_write_orders_but_can_read(api, world):
    item = f.menu_item(world.a)
    c = api.login(world.a_org_mgr, world.a)
    r = call(c, "post", "/api/v1/orders/", {"branch": str(world.a1.pk), "items": [{"menu_item": str(item.pk), "quantity": 1}]})
    assert r.status_code == 403
    assert call(c, "get", "/api/v1/orders/").status_code == 200


def test_org_manager_edits_master_menu(api, world):
    c = api.login(world.a_org_mgr, world.a)
    cat = call(c, "post", "/api/v1/menu/categories/", {"name": "Drinks"})
    assert cat.status_code == 201
    r = call(c, "post", "/api/v1/menu/items/", {"category": cat.data["id"], "name": "Cola", "sku": "COLA", "base_price_minor": 250})
    assert r.status_code == 201
    dup = call(c, "post", "/api/v1/menu/items/", {"category": cat.data["id"], "name": "Cola2", "sku": "COLA", "base_price_minor": 250})
    assert dup.status_code == 400


def test_one_person_two_roles_two_branches(api, world):
    """Manager at A1 and staff at A2: permissions follow the branch, not the person."""
    p = f.person(world.a, "branch_manager", world.a1)
    f.grant(p, world.a, "branch_staff", world.a2)
    o2 = f.order(world.a, world.a2)
    c = api.login(p, world.a)
    assert call(c, "patch", f"/api/v1/branches/{world.a1.pk}/", {"name": "ok"}).status_code == 200
    assert call(c, "patch", f"/api/v1/branches/{world.a2.pk}/", {"name": "no"}).status_code == 403
    assert call(c, "get", f"/api/v1/orders/{o2.pk}/").status_code == 200           # staff may read
    assert call(c, "delete", f"/api/v1/orders/{o2.pk}/").status_code == 403        # but not manager-level delete
    assert call(c, "get", f"/api/v1/reports/sales-summary/?branch={world.a2.pk}").status_code == 400


def test_multi_org_user_must_choose_org(api, world):
    p = f.person(world.a, "branch_staff", world.a1)
    f.grant(p, world.b, "branch_staff", world.b1)
    c = api.login(p, None)
    assert call(c, "get", "/api/v1/orders/", org=None).status_code == 400
    assert call(c, "get", "/api/v1/orders/", org=world.a).status_code == 200
    assert call(c, "get", "/api/v1/orders/", org=world.b).status_code == 200


def test_roles_cannot_escalate(api, world):
    c = api.login(world.a_br_mgr, world.a)
    r = call(c, "post", "/api/v1/memberships/", {"email": "new@example.com", "role": "org_manager"})
    assert r.status_code == 400
    r = call(c, "post", "/api/v1/memberships/", {"email": "new@example.com", "role": "branch_manager", "branch": str(world.a1.pk)})
    assert r.status_code == 400
    r = call(c, "post", "/api/v1/memberships/", {"email": "new@example.com", "role": "branch_staff", "branch": str(world.a2.pk)})
    assert r.status_code == 400  # not their branch
    r = call(c, "post", "/api/v1/memberships/", {"email": "new@example.com", "role": "branch_staff", "branch": str(world.a1.pk)})
    assert r.status_code == 201


def test_branch_manager_cannot_see_or_edit_org_manager_memberships(api, world):
    c = api.login(world.a_br_mgr, world.a)
    ids = {m["user_email"] for m in call(c, "get", "/api/v1/memberships/").data["results"]}
    assert "a.org@example.com" not in ids
    assert "a2.staff@example.com" not in ids


def test_org_manager_invites_user_with_email(api, world):

    c = api.login(world.a_org_mgr, world.a)
    from django.test.utils import CaptureQueriesContext  # noqa: F401

    r = call(c, "post", "/api/v1/memberships/", {"email": "Cook@Example.com", "full_name": "Cook",
                                               "role": "branch_staff", "branch": str(world.a1.pk)})
    assert r.status_code == 201


def test_user_viewset_staff_sees_self_only(api, world):
    c = api.login(world.a_staff, world.a)
    r = call(c, "get", "/api/v1/users/")
    assert [u["email"] for u in r.data["results"]] == [world.a_staff.email]


def test_roles_endpoint(api, world):
    c = api.login(world.a_staff, None)
    r = call(c, "get", "/api/v1/roles/")
    assert r.status_code == 200
    assert {x["code"] for x in r.data} == {"org_manager", "branch_manager", "branch_staff"}


def test_suspended_organization_is_locked_out(api, world):
    from apps.core import rls

    c = api.login(world.a_org_mgr, world.a)
    with rls.bypass():
        world.a.status = "suspended"
        world.a.save()
    assert call(c, "get", "/api/v1/orders/").status_code == 403


def test_unauthenticated_is_rejected(api, world):
    from rest_framework.test import APIClient

    assert APIClient().get("/api/v1/orders/").status_code == 401


def test_platform_admin_support_access_is_audit_logged_and_read_only(api, world):
    from apps.audit.models import AuditLog
    from apps.core import rls

    f.order(world.a, world.a1)
    c = api.login(world.admin, None)
    assert call(c, "get", "/api/v1/orders/", org=world.a).status_code == 200
    with rls.bypass():
        logs = AuditLog.objects.filter(action="support.access", actor_id=world.admin.pk, organization_id=world.a.pk)
        assert logs.exists()
    item = f.menu_item(world.a)
    r = call(c, "post", "/api/v1/orders/", {"branch": str(world.a1.pk), "items": [{"menu_item": str(item.pk), "quantity": 1}]}, org=world.a)
    assert r.status_code == 403  # support access is read-only on orders


def test_platform_admin_creates_and_suspends_organizations(api, world):

    c = api.login(world.admin, None)
    r = call(c, "post", "/api/v1/organizations/", {"name": "Charlie", "slug": "charlie", "owner_email": "boss@charlie.com", "owner_name": "Boss"}, org=None)
    assert r.status_code == 201, r.content
    assert call(c, "get", "/api/v1/organizations/", org=None).data["count"] >= 3
    assert call(c, "post", f"/api/v1/organizations/{r.data['id']}/suspend/", org=None).status_code == 200


def test_org_manager_cannot_create_or_suspend_organizations(api, world):
    c = api.login(world.a_org_mgr, world.a)
    assert call(c, "post", "/api/v1/organizations/", {"name": "X", "slug": "x"}).status_code == 403
    assert call(c, "post", f"/api/v1/organizations/{world.a.pk}/suspend/").status_code == 403
    r = call(c, "patch", f"/api/v1/organizations/{world.a.pk}/", {"name": "Alpha 2", "plan": "enterprise", "max_branches": 999})
    assert r.status_code == 200 and r.data["plan"] == "standard" and r.data["max_branches"] == 50
