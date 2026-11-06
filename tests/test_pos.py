import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.core import rls
from apps.orders.models import Order
from apps.pos import services, tasks
from apps.pos.models import ExternalRef, PosConnection, ReconciliationReport, SyncEvent
from tests import factories as f
from tests.conftest import call

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def order_payload(oid="A-1", total=2000, status="open", at=NOW, sku="BRG", **extra):
    return {"id": oid, "number": "7", "type": "dine_in", "status": status, "placed_at": NOW.isoformat(),
            "currency": "USD", "subtotal": total, "tax": 0, "total": total,
            "items": [{"id": "l1", "product_id": f"P-{sku}", "sku": sku, "name": "Burger", "quantity": 2,
                       "unit_price": total // 2}],
            "payments": [{"id": "pay1", "method": "card", "amount": total}] if status == "completed" else [],
            **extra}


def event(eid, kind="order.updated", at=NOW, **order):
    return {"event_id": eid, "type": kind, "occurred_at": at.isoformat(), "data": {"order": order_payload(**order)}}


def post_webhook(conn, body, secret="whsec_test", ts=None, sign=True, client=None):
    raw = json.dumps(body).encode()
    ts = str(int(time.time()) if ts is None else ts)
    headers = {}
    if sign:
        sig = hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
        headers = {"HTTP_X_KITCHENLY_TIMESTAMP": ts, "HTTP_X_KITCHENLY_SIGNATURE": f"sha256={sig}"}
    return (client or APIClient()).post(f"/webhooks/pos/{conn.pk}/", data=raw, content_type="application/json", **headers)


@pytest.fixture
def conn(world):
    return f.connection(world.a, world.a1)


def process_all(conn):
    with rls.bypass():
        ids = list(SyncEvent.all_objects.filter(pos_connection=conn, status__in=["received", "failed"]).values_list("id", flat=True))
    return [services.process_event(i) for i in ids]


# ---- webhook receiver ------------------------------------------------------------
def test_webhook_stores_raw_event_and_acks(conn, world):
    r = post_webhook(conn, event("e1"))
    assert r.status_code == 200 and r.json()["received"] == 1
    with rls.bypass():
        ev = SyncEvent.all_objects.get(pos_connection=conn)
    assert ev.status == "received" and ev.organization_id == world.a.id
    assert ev.payload["data"]["order"]["id"] == "A-1"   # raw payload kept
    with rls.bypass():
        assert Order.all_objects.count() == 0             # nothing applied in the request


def test_webhook_rejects_bad_signature_stale_timestamp_and_unknown_connection(conn):
    assert post_webhook(conn, event("e1"), secret="wrong").status_code == 401
    assert post_webhook(conn, event("e1"), sign=False).status_code == 401
    assert post_webhook(conn, event("e1"), ts=int(time.time()) - 3600).status_code == 401   # replay
    assert APIClient().post("/webhooks/pos/00000000-0000-0000-0000-000000000000/", data=b"{}", content_type="application/json").status_code == 404
    assert APIClient().post("/webhooks/pos/not-a-uuid/", data=b"{}", content_type="application/json").status_code == 404
    with rls.bypass():
        assert SyncEvent.all_objects.count() == 0


def test_webhook_malformed_and_oversized(conn, settings):
    raw = b"not json"
    ts = str(int(time.time()))
    sig = hmac.new(b"whsec_test", ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    r = APIClient().post(f"/webhooks/pos/{conn.pk}/", data=raw, content_type="application/json",
                         HTTP_X_KITCHENLY_TIMESTAMP=ts, HTTP_X_KITCHENLY_SIGNATURE=f"sha256={sig}")
    assert r.status_code == 400
    settings.WEBHOOK_MAX_BODY_BYTES = 10
    assert post_webhook(conn, event("e1")).status_code == 413


def test_webhook_for_paused_or_non_webhook_connection_is_404(world):
    paused = f.connection(world.a, world.a1, status="paused")
    pull = f.connection(world.a, world.a2, mode="pull")
    assert post_webhook(paused, event("e1")).status_code == 404
    assert post_webhook(pull, event("e1")).status_code == 404


def test_webhook_is_rate_limited(conn, settings):
    from rest_framework.throttling import SimpleRateThrottle

    with mock.patch.object(SimpleRateThrottle, "get_rate", return_value="3/min"):
        codes = [post_webhook(conn, event(f"e{i}")).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


# ---- idempotency ------------------------------------------------------------------
def test_repeated_webhooks_never_duplicate(conn, world):
    for _ in range(3):
        assert post_webhook(conn, event("e1")).status_code == 200
    with rls.bypass():
        assert SyncEvent.all_objects.count() == 1
    assert process_all(conn) == ["processed"]
    # same order, new event id (an update) -> still one order
    assert post_webhook(conn, event("e2", at=NOW + timedelta(minutes=1), total=3000)).status_code == 200
    process_all(conn)
    with rls.bypass():
        orders = list(Order.all_objects.all())
    assert len(orders) == 1 and orders[0].total_minor == 3000
    with rls.bypass():
        assert orders[0].items.count() == 1       # items replaced, not duplicated


def test_reprocessing_a_processed_event_is_a_noop(conn):
    post_webhook(conn, event("e1"))
    process_all(conn)
    with rls.bypass():
        ev = SyncEvent.all_objects.get()
    assert services.process_event(ev.pk) == "processed"
    with rls.bypass():
        assert Order.all_objects.count() == 1


# ---- safety rules -----------------------------------------------------------------
def test_org_and_branch_come_from_the_connection_not_the_payload(conn, world):
    body = event("e1")
    body["data"]["order"].update(organization_id=str(world.b.id), branch_id=str(world.b1.id))
    body["organization_id"] = str(world.b.id)
    post_webhook(conn, body)
    process_all(conn)
    with rls.bypass():
        o = Order.all_objects.get()
    assert o.organization_id == world.a.id and o.branch_id == world.a1.id and o.pos_connection_id == conn.id


def test_older_event_never_overwrites_newer(conn):
    post_webhook(conn, event("new", at=NOW + timedelta(minutes=5), total=5000, status="completed"))
    process_all(conn)
    post_webhook(conn, event("old", at=NOW, total=1000, status="open"))    # arrives late
    process_all(conn)
    with rls.bypass():
        o = Order.all_objects.get()
    assert o.total_minor == 5000 and o.status == "completed"


def test_paid_and_voided_events(conn):
    post_webhook(conn, event("e1", kind="order.created"))
    post_webhook(conn, event("e2", kind="order.paid", at=NOW + timedelta(minutes=1), status="completed"))
    process_all(conn)
    with rls.bypass():
        o = Order.all_objects.get()
        assert o.status == "completed" and o.payments.count() == 1 and o.closed_at
    void = {"event_id": "e3", "type": "order.voided", "occurred_at": (NOW + timedelta(minutes=2)).isoformat(),
            "data": {"order_id": "A-1"}}
    post_webhook(conn, void)
    process_all(conn)
    with rls.bypass():
        assert Order.all_objects.get().status == "voided"


def test_unmapped_items_are_kept_and_mapped_by_sku(conn, world):
    item = f.menu_item(world.a, sku="BRG", price=1000)
    post_webhook(conn, event("e1", sku="BRG"))
    post_webhook(conn, event("e2", oid="A-2", sku="UNKNOWN"))
    process_all(conn)
    with rls.bypass():
        mapped = Order.all_objects.get(external_id="A-1").items.get()
        unmapped = Order.all_objects.get(external_id="A-2").items.get()
        assert mapped.menu_item_id == item.id
        assert unmapped.menu_item_id is None and unmapped.name_snapshot == "Burger"   # the POS may sell unmapped items
        assert ExternalRef.all_objects.filter(pos_connection=conn, external_id="P-BRG", local_id=item.id).exists()


def test_unknown_event_types_are_ignored_not_failed(conn):
    post_webhook(conn, {"event_id": "x", "type": "shift.opened", "occurred_at": NOW.isoformat(), "data": {}})
    assert process_all(conn) == ["processed"]


# ---- failure handling -------------------------------------------------------------
def test_failures_retry_then_dead_letter_and_manager_can_retry(conn, world, api, settings):
    settings.SYNC_EVENT_MAX_ATTEMPTS = 3
    bad = event("bad")
    bad["data"]["order"]["items"][0]["quantity"] = "oops"
    post_webhook(conn, bad)
    with rls.bypass():
        ev = SyncEvent.all_objects.get()
    for expected in ("failed", "failed", "dead"):
        with pytest.raises(ValueError):
            services.process_event(ev.pk)
        with rls.bypass():
            ev.refresh_from_db()
        assert ev.status == expected
    assert "quantity" in ev.error and ev.attempts == 3
    assert services.process_event(ev.pk) == "dead"          # dead events are left alone
    with rls.bypass():
        assert Order.all_objects.count() == 0                # failed work left no half-applied order

    mgr = api.login(world.a_org_mgr, world.a)
    dead = call(mgr, "get", "/api/v1/pos/sync-events/?status=dead").data["results"]
    assert [d["id"] for d in dead] == [str(ev.pk)]
    r = call(mgr, "post", f"/api/v1/pos/sync-events/{ev.pk}/retry/")
    assert r.status_code == 202 and r.data["status"] == "received" and r.data["attempts"] == 0
    # branch manager can see the list but not retry
    bm = api.login(world.a_br_mgr, world.a)
    assert call(bm, "get", "/api/v1/pos/sync-events/").status_code == 200
    assert call(bm, "post", f"/api/v1/pos/sync-events/{ev.pk}/retry/").status_code == 403


def test_a_failed_apply_rolls_back_everything(conn):
    post_webhook(conn, event("e1"))
    with rls.bypass():
        ev = SyncEvent.all_objects.get()
    with mock.patch("apps.pos.services.OrderItem.objects.bulk_create", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            services.process_event(ev.pk)
    with rls.bypass():
        assert Order.all_objects.count() == 0
        assert SyncEvent.all_objects.get().status == "failed"


def test_stuck_events_are_requeued(conn):
    post_webhook(conn, event("e1"))
    with rls.bypass():
        SyncEvent.all_objects.update(updated_at=datetime.now(UTC) - timedelta(hours=1))
    with mock.patch.object(tasks.process_sync_event, "apply_async") as m:
        assert tasks.requeue_stuck_events() == 1
    assert m.call_args.kwargs["queue"] == "webhook"


# ---- scheduled pull ---------------------------------------------------------------
def fake_response(payload, status=200):
    r = mock.Mock(status_code=status)
    r.json.return_value = payload
    return r


def test_pull_saves_cursor_only_after_batch_is_stored(world):
    c = f.connection(world.a, world.a1, mode="pull", cursor="c0")
    o1 = order_payload("P-1")
    o1["updated_at"] = NOW.isoformat()
    pages = [fake_response({"orders": [o1], "next_cursor": "c1"}), fake_response({"orders": [], "next_cursor": "c1"})]
    with mock.patch("apps.pos.adapters.generic.requests.request", side_effect=pages) as req:
        with mock.patch("apps.pos.services.enqueue_processing"):
            # storing fails -> cursor must NOT move, so nothing is lost
            with mock.patch("apps.pos.services.store_raw_events", side_effect=RuntimeError("db down")):
                with pytest.raises(RuntimeError):
                    tasks.run_pull(c.pk)
    with rls.bypass():
        assert PosConnection.all_objects.get(pk=c.pk).cursor == "c0"
        assert SyncEvent.all_objects.count() == 0
    pages = [fake_response({"orders": [o1], "next_cursor": "c1"}), fake_response({"orders": [], "next_cursor": "c1"})]
    with mock.patch("apps.pos.adapters.generic.requests.request", side_effect=pages) as req:
        with mock.patch("apps.pos.services.enqueue_processing"):
            assert tasks.run_pull(c.pk) == "stored:1"
        assert req.call_args_list[0].kwargs["params"]["cursor"] == "c0"      # resumed from the saved cursor
        assert req.call_args_list[0].kwargs["headers"]["Authorization"] == "Bearer k"
    with rls.bypass():
        got = PosConnection.all_objects.get(pk=c.pk)
        assert got.cursor == "c1" and got.last_synced_at and got.next_pull_at > got.last_synced_at
    process_all(c)
    with rls.bypass():
        assert Order.all_objects.get().external_id == "P-1"
    # pulling the same page again creates no duplicates
    pages = [fake_response({"orders": [o1], "next_cursor": "c1"})]
    with mock.patch("apps.pos.adapters.generic.requests.request", side_effect=pages):
        with mock.patch("apps.pos.services.enqueue_processing"):
            tasks.run_pull(c.pk)
    with rls.bypass():
        assert SyncEvent.all_objects.count() == 1


def test_pull_failure_backs_off_and_eventually_pauses(world):
    c = f.connection(world.a, world.a1, mode="pull")
    with mock.patch("apps.pos.adapters.generic.requests.request", return_value=fake_response({}, 500)):
        assert tasks.run_pull(c.pk) == "failed"
        with rls.bypass():
            got = PosConnection.all_objects.get(pk=c.pk)
        assert got.consecutive_failures == 1 and got.status == "active" and "500" in got.last_error
        assert got.next_pull_at > datetime.now(UTC)
    with mock.patch("apps.pos.adapters.generic.requests.request", return_value=fake_response({}, 401)):
        tasks.run_pull(c.pk)                              # auth failure is not retryable
    with rls.bypass():
        assert PosConnection.all_objects.get(pk=c.pk).status == "error"


def test_schedule_pulls_only_enqueues_due_active_pull_connections(world):
    due = f.connection(world.a, world.a1, mode="pull")
    f.connection(world.a, world.a2, mode="pull", next_pull_at=datetime.now(UTC) + timedelta(hours=1))
    f.connection(world.b, world.b1, mode="webhook")
    f.connection(world.b, world.b2, mode="pull", status="paused")
    with mock.patch.object(tasks.pull_connection, "apply_async") as m:
        assert tasks.schedule_pulls() == 1
    assert m.call_args.kwargs["args"] == [str(due.pk)] and m.call_args.kwargs["queue"] == "pull"


# ---- CSV import -------------------------------------------------------------------
CSV = (
    "order_id,placed_at,status,order_type,item_sku,item_name,quantity,unit_price_minor,payment_method,payment_amount_minor\n"
    "1001,2026-11-05T10:00:00Z,completed,dine_in,BRG,Burger,2,1000,card,2000\n"
    "1002,2026-11-05T11:00:00Z,completed,takeaway,FRS,Fries,1,300,cash,300\n"
    "1002,2026-11-05T11:00:00Z,completed,takeaway,COK,Cola,1,200,,\n"
    "1003,not-a-date,completed,dine_in,BRG,Burger,1,1000,,\n"
    "1004,2026-11-05T12:00:00Z,completed,dine_in,BRG,Burger,0,1000,,\n"
)


def upload(client, conn, content=CSV, org=None):
    return call(client, "post", f"/api/v1/pos/connections/{conn.pk}/imports/",
                {"file": SimpleUploadedFile("day.csv", content.encode(), content_type="text/csv")},
                format="multipart", org=org)


def test_csv_import_validates_rows_and_rejects_duplicate_files(world, api, settings):
    c = f.connection(world.a, world.a1, mode="file")
    mgr = api.login(world.a_org_mgr, world.a)
    r = upload(mgr, c)
    assert r.status_code == 202, r.content
    with mock.patch.object(tasks.process_sync_event, "apply_async"):
        assert tasks.import_file(r.data["id"]) == "imported:2"
    job = call(mgr, "get", f"/api/v1/pos/imports/{r.data['id']}/").data
    assert job["status"] == "done" and job["total_rows"] == 5 and job["valid_rows"] == 2 and job["error_rows"] == 2
    assert {e["row"] for e in job["errors"]} == {5, 6}
    process_all(c)
    with rls.bypass():
        assert sorted(Order.all_objects.values_list("external_id", "total_minor")) == [("1001", 2000), ("1002", 500)]
    # the same file again is rejected
    dup = upload(mgr, c)
    assert dup.status_code == 409 and dup.data["import_id"] == r.data["id"]
    # a corrected file is accepted and updates, not duplicates
    fixed = CSV.replace("1001,2026-11-05T10:00:00Z,completed,dine_in,BRG,Burger,2,1000", "1001,2026-11-05T10:00:00Z,completed,dine_in,BRG,Burger,3,1000").replace("card,2000", "card,3000")
    r2 = upload(mgr, c, fixed)
    assert r2.status_code == 202
    with mock.patch.object(tasks.process_sync_event, "apply_async"):
        tasks.import_file(r2.data["id"])
    process_all(c)
    with rls.bypass():
        assert Order.all_objects.count() == 2 and Order.all_objects.get(external_id="1001").total_minor == 3000


def test_csv_with_wrong_columns_is_reported(world, api):
    c = f.connection(world.a, world.a1, mode="file")
    mgr = api.login(world.a_org_mgr, world.a)
    r = upload(mgr, c, "foo,bar\n1,2\n")
    with mock.patch.object(tasks.process_sync_event, "apply_async"):
        tasks.import_file(r.data["id"])
    job = call(mgr, "get", f"/api/v1/pos/imports/{r.data['id']}/").data
    assert job["valid_rows"] == 0 and "Missing columns" in job["errors"][0]["error"]


def test_only_managers_can_upload_and_only_for_own_org(world, api):
    c = f.connection(world.a, world.a1, mode="file")
    assert upload(api.login(world.a_staff, world.a), c).status_code == 403
    assert upload(api.login(world.a_br_mgr, world.a), c).status_code == 403
    assert upload(api.login(world.b_org_mgr, world.b), c).status_code == 404


# ---- push -------------------------------------------------------------------------
def test_push_menu_uses_external_refs_and_this_system_is_master(world):
    c = f.connection(world.a, world.a1, push_enabled=True)
    burger = f.menu_item(world.a, sku="BRG", price=1000)
    f.override(world.a, world.a1, burger, price=1100)
    resp = fake_response({"items": [{"sku": "BRG", "external_id": "ext-77"}]})
    with mock.patch("apps.pos.adapters.generic.requests.request", return_value=resp) as req:
        assert tasks.push_menu.apply(args=[str(c.pk)]).result == "pushed:1"
    sent = req.call_args.kwargs["json"]["items"][0]
    assert sent["price"] == 1100 and sent["external_id"] is None             # branch price wins
    with rls.bypass():
        assert ExternalRef.all_objects.get(pos_connection=c).external_id == "ext-77"
        assert SyncEvent.all_objects.get(direction="out").status == "processed"
    with mock.patch("apps.pos.adapters.generic.requests.request", return_value=resp) as req:
        tasks.push_menu.apply(args=[str(c.pk)])
    assert req.call_args.kwargs["json"]["items"][0]["external_id"] == "ext-77"   # reuses the mapping


def test_push_failure_is_recorded_and_retried(world):
    c = f.connection(world.a, world.a1, push_enabled=True)
    f.menu_item(world.a)
    with mock.patch("apps.pos.adapters.generic.requests.request", return_value=fake_response({}, 503)):
        result = tasks.push_menu.apply(args=[str(c.pk)])
    assert result.result == "dead"                       # retried 6 times with backoff, then dead-lettered
    with rls.bypass():
        out = SyncEvent.all_objects.get(direction="out")  # one event shared by every retry
    assert out.status == "dead" and out.attempts == 7 and "503" in out.error


def test_menu_change_triggers_a_debounced_push(world, api, django_capture_on_commit_callbacks):
    f.connection(world.a, world.a1, push_enabled=True)
    item = f.menu_item(world.a)
    mgr = api.login(world.a_org_mgr, world.a)
    from django.core.cache import cache

    cache.clear()  # setup itself (creating the item) already triggered a push request
    with mock.patch.object(tasks.push_menu, "apply_async") as m:
        with django_capture_on_commit_callbacks(execute=True):
            call(mgr, "patch", f"/api/v1/menu/items/{item.pk}/", {"base_price_minor": 1234})
            call(mgr, "patch", f"/api/v1/menu/items/{item.pk}/", {"base_price_minor": 1300})
    assert m.call_count == 1


# ---- reconciliation ---------------------------------------------------------------
def test_reconciliation_flags_differences(world):
    c = f.connection(world.a, world.a1, mode="pull")
    day = datetime(2026, 11, 5).date()
    f.order(world.a, world.a1, total=1000, status="completed", pos_connection=c, external_id="1",
            placed_at=datetime(2026, 11, 5, 10, tzinfo=UTC))
    ok = fake_response({"order_count": 1, "total_minor": 1000})
    with mock.patch("apps.pos.adapters.generic.requests.request", return_value=ok):
        assert tasks.reconcile(c.pk, day).is_match is True
    bad = fake_response({"order_count": 2, "total_minor": 2500})
    with mock.patch("apps.pos.adapters.generic.requests.request", return_value=bad):
        rep = tasks.reconcile(c.pk, day)
    assert rep.is_match is False and rep.pos_total_minor == 2500 and rep.local_total_minor == 1000
    with rls.bypass():
        assert ReconciliationReport.all_objects.count() == 1          # updated, not duplicated


def test_reconciliations_endpoint(world, api):
    c = f.connection(world.a, world.a1, mode="pull")
    with rls.bypass():
        ReconciliationReport.objects.create(organization=world.a, pos_connection=c, business_date="2026-11-05",
                                            pos_order_count=1, pos_total_minor=5, local_order_count=0,
                                            local_total_minor=0, is_match=False)
    mgr = api.login(world.a_br_mgr, world.a)
    r = call(mgr, "get", "/api/v1/pos/reconciliations/?is_match=false")
    assert r.status_code == 200 and r.data["count"] == 1
    assert call(api.login(world.a_staff, world.a), "get", "/api/v1/pos/reconciliations/").status_code == 403


# ---- connection management + secrets ---------------------------------------------
def test_connection_secrets_are_encrypted_and_never_returned(world, api, db):
    from django.db import connection as dbc

    mgr = api.login(world.a_org_mgr, world.a)
    r = call(mgr, "post", "/api/v1/pos/connections/", {
        "branch": str(world.a1.pk), "provider": str(f.provider().pk), "sync_mode": "webhook", "name": "Till 1",
        "credentials": {"api_key": "SUPER-SECRET-KEY"}, "config": {"base_url": "https://pos.example.com"}})
    assert r.status_code == 201, r.content
    assert len(r.data["webhook_secret"]) >= 32                      # shown once, at creation
    assert "SUPER-SECRET-KEY" not in json.dumps(r.data, default=str) and r.data["has_credentials"] is True
    got = call(mgr, "get", f"/api/v1/pos/connections/{r.data['id']}/").data
    assert "webhook_secret" not in got and "credentials" not in got and "SUPER-SECRET-KEY" not in json.dumps(got, default=str)
    with rls.tenant_context(world.a.id):
        with dbc.cursor() as cur:
            cur.execute("SELECT credentials_encrypted, webhook_secret FROM pos_posconnection WHERE id=%s", [r.data["id"]])
            cipher, secret = cur.fetchone()
    assert "SUPER-SECRET-KEY" not in cipher and r.data["webhook_secret"] not in secret   # ciphertext at rest
    with rls.tenant_context(world.a.id):
        assert PosConnection.objects.get(pk=r.data["id"]).credentials_encrypted == {"api_key": "SUPER-SECRET-KEY"}
    rot = call(mgr, "post", f"/api/v1/pos/connections/{r.data['id']}/rotate-secret/")
    assert rot.data["webhook_secret"] != r.data["webhook_secret"]


def test_connection_validation(world, api, settings):
    mgr = api.login(world.a_org_mgr, world.a)
    base = {"branch": str(world.a1.pk), "provider": str(f.provider().pk)}
    assert call(mgr, "post", "/api/v1/pos/connections/", {**base, "sync_mode": "bogus"}).status_code == 400
    settings.POS_ALLOW_PRIVATE_NETWORKS = False
    for bad in ("http://pos.example.com", "https://127.0.0.1/x", "https://10.0.0.5", "ftp://x"):
        r = call(mgr, "post", "/api/v1/pos/connections/", {**base, "sync_mode": "pull", "config": {"base_url": bad}})
        assert r.status_code == 400, bad                              # SSRF guard


def test_providers_visible_to_managers_not_writable(world, api):
    mgr = api.login(world.a_org_mgr, world.a)
    assert call(mgr, "get", "/api/v1/pos/providers/").status_code == 200
    assert call(mgr, "post", "/api/v1/pos/providers/", {"code": "x", "name": "X"}).status_code == 403
    admin = api.login(world.admin, None)
    assert call(admin, "post", "/api/v1/pos/providers/", {"code": "acme", "name": "Acme", "supports_webhook": True}, org=None).status_code == 201
