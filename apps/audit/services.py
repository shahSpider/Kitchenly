"""Audit trail: who changed what, with before/after values."""
from __future__ import annotations

from contextvars import ContextVar

from django.db.models.signals import post_delete, post_save, pre_save

from apps.core.logging import request_id_var

# (actor_id, ip) for the current request/task
_actor: ContextVar[tuple | None] = ContextVar("audit_actor", default=None)

SENSITIVE = {"password", "credentials_encrypted", "webhook_secret", "totp_secret", "credentials"}


def set_actor(actor_id, ip=None):
    return _actor.set((actor_id, ip))


def reset_actor(token):
    _actor.reset(token)


def current_actor():
    return _actor.get()


def _snapshot(instance):
    data = {}
    for f in instance._meta.concrete_fields:
        if f.name in SENSITIVE:
            continue
        value = getattr(instance, f.attname)
        data[f.attname] = value if isinstance(value, (int, float, bool, type(None))) else str(value)
    return data


def _org_id(instance):
    if instance._meta.label == "tenants.Organization":
        return instance.pk
    return getattr(instance, "organization_id", None)


def write(action, entity, entity_id="", before=None, after=None, organization_id=None, actor_id=None, ip=None):
    from .models import AuditLog

    actor = current_actor()
    if actor_id is None and actor:
        actor_id = actor[0]
    if ip is None and actor:
        ip = actor[1]
    rid = request_id_var.get()
    AuditLog.all_objects.create(
        actor_id=actor_id, organization_id=organization_id, action=action, entity=entity,
        entity_id=str(entity_id or ""), before=before, after=after, ip=ip,
        request_id="" if rid == "-" else rid,
    )


def record_support_access(user, method, path, ip, organization_id=None):
    write("support.access", "http", path, after={"method": method}, organization_id=organization_id,
          actor_id=user.pk, ip=ip)


def register(model, only_with_actor=False):
    """Audit create/update/delete of `model`."""

    def _pre(sender, instance, raw=False, **kw):
        if raw:
            return
        if instance._state.adding:
            instance._audit_before = None
            return
        row = sender._base_manager.filter(pk=instance.pk).first()
        instance._audit_before = _snapshot(row) if row else None

    def _post(sender, instance, created, raw=False, **kw):
        if raw or (only_with_actor and not current_actor()):
            return
        write("create" if created else "update", sender._meta.label, instance.pk,
              before=getattr(instance, "_audit_before", None), after=_snapshot(instance),
              organization_id=_org_id(instance))

    def _del(sender, instance, **kw):
        if only_with_actor and not current_actor():
            return
        write("delete", sender._meta.label, instance.pk, before=_snapshot(instance),
              organization_id=_org_id(instance))

    uid = f"audit-{model._meta.label}"
    pre_save.connect(_pre, sender=model, weak=False, dispatch_uid=uid + "-pre")
    post_save.connect(_post, sender=model, weak=False, dispatch_uid=uid + "-post")
    post_delete.connect(_del, sender=model, weak=False, dispatch_uid=uid + "-del")
