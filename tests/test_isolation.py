"""Phase-1 gate from the design doc: logged in as org A, every list and detail
route returns nothing from org B. Plus the database-level lock (RLS)."""
import pytest
from django.db import connection

from apps.accounts.models import Membership
from apps.core import rls
from apps.orders.models import Order
from apps.pos.models import PosConnection
from tests import factories as f
from tests.conftest import call


@pytest.fixture
def data(world):
    """One of everything in BOTH orgs."""
    d = {}
    for key, o, br, mgr in (("a", world.a, world.a1, world.a_org_mgr), ("b", world.b, world.b1, world.b_org_mgr)):
        item = f.menu_item(o)
        ov = f.override(o, br, item)
        order = f.order(o, br)
        pay = f.payment(order)
        with rls.bypass():
            membership = Membership.all_objects.filter(user=mgr).first()
        d[key] = dict(
            organization=o, branch=br, item=item, category=item.category, branch_menu_item=ov, order=order,
            refund=f.refund(order, pay), conn=(conn := f.connection(o, br)), sync_event=f.sync_event(conn),
            membership=membership, user=mgr)
    return d


# (list url, key in data, whether org-B rows could appear)
LIST_ROUTES = [
    ("/api/v1/organizations/", "organization"), ("/api/v1/branches/", "branch"), ("/api/v1/users/", "user"),
    ("/api/v1/memberships/", "membership"), ("/api/v1/menu/categories/", "category"),
    ("/api/v1/menu/items/", "item"), ("/api/v1/branch-menu-items/", "branch_menu_item"),
    ("/api/v1/orders/", "order"), ("/api/v1/refunds/", "refund"), ("/api/v1/pos/connections/", "conn"),
    ("/api/v1/pos/sync-events/", "sync_event"),
]


def _ids(response):
    body = response.data
    rows = body["results"] if isinstance(body, dict) and "results" in body else body
    return {str(r["id"]) for r in rows}


@pytest.mark.parametrize("url,key", LIST_ROUTES)
def test_list_routes_never_return_other_org(api, world, data, url, key):
    client = api.login(world.a_org_mgr, world.a)
    r = call(client, "get", url)
    assert r.status_code == 200, r.content
    ids = _ids(r)
    assert str(data["a"][key].pk) in ids          # sees own data
    assert str(data["b"][key].pk) not in ids       # never the other org's


@pytest.mark.parametrize("url,key", LIST_ROUTES)
def test_detail_routes_404_for_other_org(api, world, data, url, key):
    client = api.login(world.a_org_mgr, world.a)
    target = f"{url}{data['b'][key].pk}/"
    assert call(client, "get", target).status_code == 404
    assert call(client, "patch", target, {"name": "x"}).status_code in (404, 405, 403)
    assert call(client, "delete", target).status_code in (404, 405, 403)


def test_cannot_pick_an_organization_you_do_not_belong_to(api, world, data):
    client = api.login(world.a_org_mgr, world.a)
    assert call(client, "get", "/api/v1/orders/", org=world.b).status_code == 403


def test_cannot_reference_other_org_objects_in_writes(api, world, data):
    client = api.login(world.a_org_mgr, world.a)
    r = call(client, "post", "/api/v1/menu/items/", {
        "category": str(data["b"]["category"].pk), "name": "x", "sku": "zz", "base_price_minor": 5})
    assert r.status_code == 400
    r = call(client, "post", "/api/v1/pos/connections/", {
        "branch": str(data["b"]["branch"].pk), "provider": str(f.provider().pk), "sync_mode": "webhook"})
    assert r.status_code == 400
    staff = api.login(world.a_staff, world.a)
    r = call(staff, "post", "/api/v1/orders/", {
        "branch": str(data["b"]["branch"].pk), "items": [{"menu_item": str(data["b"]["item"].pk), "quantity": 1}]})
    assert r.status_code == 400


def test_reports_are_isolated(api, world, data):
    f.order(world.a, world.a1, total=700)
    f.order(world.b, world.b1, total=999999)
    client = api.login(world.a_org_mgr, world.a)
    r = call(client, "get", "/api/v1/reports/sales-summary/")
    assert r.status_code == 200
    assert r.data["totals"]["total"] < 999999


# ---- layer 2: PostgreSQL row-level security ---------------------------------------
def test_rls_blocks_even_when_app_filter_is_skipped(world, data):
    """`all_objects` has no app-level filter; only the database stands guard."""
    with rls.tenant_context(world.a.id):
        assert Order.all_objects.filter(organization=world.b).count() == 0
        assert Order.all_objects.filter(organization=world.a).count() >= 1
        assert PosConnection.all_objects.filter(organization=world.b).count() == 0


def test_rls_fails_closed_without_context(world, data):
    assert Order.all_objects.count() == 0
    with connection.cursor() as cur:
        cur.execute('SELECT count(*) FROM "orders_order"')
        assert cur.fetchone()[0] == 0


def test_rls_rejects_writes_into_other_org(world, data):
    from django.db import DatabaseError, transaction

    with rls.tenant_context(world.a.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            Order.all_objects.create(organization=world.b, branch=world.b1, currency="USD",
                                     placed_at="2026-01-01T00:00:00Z")


def test_scoped_manager_fails_closed_without_scope(world, data):
    assert Order.objects.count() == 0
