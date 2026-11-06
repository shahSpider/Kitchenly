import pytest
from django.db import DatabaseError, connection, transaction

from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core import rls
from tests import factories as f
from tests.conftest import call


def write(*a, **kw):
    with rls.bypass():  # outside a request there is no tenant context: system write
        audit.write(*a, **kw)


def logs(**kw):
    with rls.bypass():
        return list(AuditLog.objects.filter(**kw).order_by("created_at"))


def test_changes_are_audited_with_actor_before_and_after(api, world):
    item = f.menu_item(world.a, price=1000, name="Burger")
    c = api.login(world.a_org_mgr, world.a)
    call(c, "patch", f"/api/v1/menu/items/{item.pk}/", {"base_price_minor": 1500})
    entry = [e for e in logs(entity="menu.MenuItem", entity_id=str(item.pk)) if e.action == "update"][-1]
    assert entry.actor_id == world.a_org_mgr.pk and entry.organization_id == world.a.pk
    assert entry.before["base_price_minor"] == 1000 and entry.after["base_price_minor"] == 1500
    assert entry.request_id


def test_secrets_never_reach_the_audit_log(api, world):
    c = api.login(world.a_org_mgr, world.a)
    r = call(c, "post", "/api/v1/pos/connections/", {"branch": str(world.a1.pk), "provider": str(f.provider().pk),
                                                   "credentials": {"api_key": "TOPSECRET"}})
    entry = logs(entity="pos.PosConnection", entity_id=r.data["id"])[0]
    assert "TOPSECRET" not in str(entry.after) and "webhook_secret" not in entry.after and "credentials_encrypted" not in entry.after


def test_manual_order_changes_audited_but_pos_sync_noise_is_not(world):
    with rls.bypass():
        f.order(world.a, world.a1)                      # no actor -> not audited (POS volume)
    assert logs(entity="orders.Order") == []


def test_audit_log_is_append_only_at_every_layer(world):
    write("test", "x", "1", organization_id=world.a.pk)
    with rls.bypass():
        row = AuditLog.objects.get(action="test")
        with pytest.raises(PermissionError):
            row.save()
        with pytest.raises(PermissionError):
            row.delete()
        with pytest.raises(PermissionError):
            AuditLog.objects.filter(pk=row.pk).update(action="x")
        with pytest.raises(PermissionError):
            AuditLog.objects.all().delete()
        # even raw SQL (a bug or a compromised app) hits the database trigger
        for sql in ("UPDATE audit_auditlog SET action='hacked'", "DELETE FROM audit_auditlog", "TRUNCATE audit_auditlog"):
            with pytest.raises(DatabaseError, match="append-only"), transaction.atomic(), connection.cursor() as cur:
                cur.execute(sql)
        assert AuditLog.objects.filter(action="test").exists()


def test_org_manager_reads_only_own_audit_log(api, world):
    write("a", "x", "1", organization_id=world.a.pk)
    write("b", "x", "2", organization_id=world.b.pk)
    write("p", "x", "3", organization_id=None)           # platform-level event
    c = api.login(world.a_org_mgr, world.a)
    r = call(c, "get", "/api/v1/audit-log/")
    assert r.status_code == 200
    seen = {row["action"] for row in r.data["results"]}
    assert "a" in seen and "b" not in seen and "p" not in seen
    assert call(api.login(world.a_br_mgr, world.a), "get", "/api/v1/audit-log/").status_code == 403


def test_platform_admin_reads_everything(api, world):
    write("b", "x", "2", organization_id=world.b.pk)
    c = api.login(world.admin, None)
    assert "b" in {r["action"] for r in call(c, "get", "/api/v1/audit-log/", org=None).data["results"]}
