"""POS pipeline steps: receive -> dedupe -> translate -> apply -> mark."""
from __future__ import annotations

import logging
import random

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.menu.models import MenuItem
from apps.orders.models import Order, OrderItem, Payment

from .adapters.base import CanonicalEvent, UnsupportedEvent, get_adapter
from .models import ExternalRef, SyncEvent

log = logging.getLogger(__name__)

SOURCE_QUEUE = {"webhook": "webhook", "pull": "pull", "file": "import", "push": "push"}


def backoff_seconds(attempts: int) -> int:
    return min(10 * 2 ** max(attempts - 1, 0), 3600) + random.randint(0, 5)


# 1+2. Receive + deduplicate ---------------------------------------------------
def store_raw_events(connection, raws, source, id_prefix=""):
    """Persist raw events. The unique key (connection, kind, event_id) silently
    drops repeats, so replayed webhooks / re-pulls never duplicate work.
    Call inside tenant_context(connection.organization_id)."""
    rows = [SyncEvent(organization_id=connection.organization_id, pos_connection=connection, source=source,
                      kind=r.kind, event_id=f"{id_prefix}{r.event_id}"[:190], payload=r.payload) for r in raws]
    SyncEvent.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)
    if not rows:
        return []
    ids = SyncEvent.objects.filter(pos_connection=connection, event_id__in=[r.event_id for r in rows],
                                   status__in=["received", "failed"]).values_list("id", flat=True)
    return list(ids)


def enqueue_processing(event_ids, source):
    from .tasks import process_sync_event

    queue = SOURCE_QUEUE.get(source, "default")
    for eid in event_ids:
        transaction.on_commit(lambda eid=eid: process_sync_event.apply_async(args=[str(eid)], queue=queue))


# 3+4. Translate + apply ---------------------------------------------------------
def _resolve_menu_item(connection, item, cache):
    key = (item.external_menu_item_id, item.sku)
    if key in cache:
        return cache[key]
    found = None
    if item.external_menu_item_id:
        ref = ExternalRef.objects.filter(pos_connection=connection, entity_type="menu_item",
                                         external_id=item.external_menu_item_id).first()
        if ref:
            found = MenuItem.objects.filter(pk=ref.local_id).first()
    if found is None and item.sku:
        found = MenuItem.objects.filter(organization_id=connection.organization_id, sku=item.sku).first()
        if found and item.external_menu_item_id:
            ExternalRef.objects.get_or_create(
                pos_connection=connection, entity_type="menu_item", external_id=item.external_menu_item_id,
                defaults={"organization_id": connection.organization_id, "local_id": found.pk})
    cache[key] = found  # None => unmapped: the POS may sell an item we haven't mapped yet
    return found


def apply_order_event(connection, event: CanonicalEvent) -> str:
    """Upsert the order inside the caller's transaction.

    Organization and branch come from the connection, never from the payload.
    Returns 'applied' | 'stale' | 'ignored'."""
    order = (Order.objects.select_for_update()
             .filter(pos_connection=connection, external_id=event.external_order_id).first())
    if order and order.pos_updated_at and event.occurred_at < order.pos_updated_at:
        return "stale"  # an older event must never overwrite a newer one
    data = event.order
    if data is None:  # e.g. void for an order we have not stored
        if order is None:
            return "ignored"
        order.status = Order.Status.VOIDED
        order.pos_updated_at = event.occurred_at
        order.closed_at = order.closed_at or event.occurred_at
        order.save(update_fields=["status", "pos_updated_at", "closed_at", "updated_at"])
        return "applied"

    currency = (data.currency or connection.organization.currency).upper()
    subtotal = data.subtotal_minor if data.subtotal_minor is not None else sum(i.line_total_minor for i in data.items)
    total = data.total_minor if data.total_minor is not None else max(subtotal + data.tax_minor - data.discount_minor, 0)
    status = Order.Status.VOIDED if event.kind == "order.voided" else data.status
    fields = dict(order_number=data.number or event.external_order_id[:40], status=status,
                  order_type=data.order_type, subtotal_minor=subtotal, tax_minor=data.tax_minor,
                  discount_minor=data.discount_minor, total_minor=total, currency=currency,
                  placed_at=data.placed_at, pos_updated_at=event.occurred_at,
                  closed_at=event.occurred_at if status in ("completed", "voided") else None)
    if order is None:
        try:
            with transaction.atomic():
                order = Order.objects.create(organization_id=connection.organization_id, branch_id=connection.branch_id,
                                             pos_connection=connection, external_id=event.external_order_id, **fields)
        except IntegrityError:  # concurrent insert of the same order
            order = Order.objects.select_for_update().get(pos_connection=connection,
                                                          external_id=event.external_order_id)
    for k, v in fields.items():
        setattr(order, k, v)
    order.save()

    order.items.all().delete()
    cache = {}
    OrderItem.objects.bulk_create([
        OrderItem(organization_id=connection.organization_id, order=order,
                  menu_item=_resolve_menu_item(connection, i, cache), name_snapshot=i.name, sku_snapshot=i.sku,
                  quantity=i.quantity, unit_price_minor=i.unit_price_minor, line_total_minor=i.line_total_minor,
                  external_id=i.external_id) for i in data.items])
    for p in data.payments:
        values = dict(method=p.method, amount_minor=p.amount_minor, status=p.status, reference=p.reference,
                      paid_at=p.paid_at or event.occurred_at)
        if p.external_id:
            Payment.objects.update_or_create(order=order, external_id=p.external_id,
                                             defaults={**values, "organization_id": connection.organization_id})
        elif not order.payments.filter(amount_minor=p.amount_minor, method=p.method).exists():
            Payment.objects.create(organization_id=connection.organization_id, order=order, **values)
    return "applied"


# 5. Mark ------------------------------------------------------------------------
def process_event(event_id) -> str:
    """Translate + apply one stored event. Returns the final status.
    Raises after recording the failure so the Celery task can retry with backoff."""
    from apps.core import rls

    with rls.bypass():
        ev = (SyncEvent.all_objects.select_related("pos_connection__provider", "pos_connection__branch",
                                                   "pos_connection__organization").filter(pk=event_id).first())
    if ev is None or ev.status in ("processed", "dead"):
        return ev.status if ev else "missing"
    connection = ev.pos_connection
    try:
        with rls.tenant_context(ev.organization_id):
            with transaction.atomic():
                locked = SyncEvent.objects.select_for_update(skip_locked=True).filter(
                    pk=ev.pk, status__in=["received", "failed"]).first()
                if locked is None:
                    return "skipped"  # another worker has it, or it is already done
                try:
                    events = get_adapter(connection.provider.code).translate(locked.kind, locked.payload, connection)
                    outcomes = [apply_order_event(connection, e) for e in events]
                    note = ",".join(outcomes)
                except UnsupportedEvent:
                    note = "unsupported"
                locked.status = SyncEvent.Status.PROCESSED
                locked.processed_at = timezone.now()
                locked.attempts += 1
                locked.error = "" if note in ("applied", "unsupported", "") else f"note: {note}"
                locked.save(update_fields=["status", "processed_at", "attempts", "error", "updated_at"])
                return "processed"
    except Exception as exc:
        _record_failure(ev.pk, ev.organization_id, exc)
        raise


def _record_failure(event_pk, org_id, exc):
    from apps.core import rls

    with rls.tenant_context(org_id):
        ev = SyncEvent.objects.get(pk=event_pk)
        ev.attempts += 1
        ev.error = f"{exc.__class__.__name__}: {exc}"[:2000]
        ev.status = SyncEvent.Status.DEAD if ev.attempts >= settings.SYNC_EVENT_MAX_ATTEMPTS else SyncEvent.Status.FAILED
        ev.save(update_fields=["attempts", "error", "status", "updated_at"])
        if ev.status == SyncEvent.Status.DEAD:
            log.error("sync_event %s moved to dead-letter after %s attempts: %s", ev.pk, ev.attempts, ev.error)
