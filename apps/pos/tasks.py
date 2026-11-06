"""Celery tasks. Queues: webhook (live orders), pull, import, push, default."""
from __future__ import annotations

import io
import logging
import uuid
import zoneinfo
from datetime import datetime, time, timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core import rls

from . import services
from .adapters.base import AdapterError, get_adapter
from .models import ExternalRef, ImportJob, PosConnection, ReconciliationReport, SyncEvent

log = logging.getLogger(__name__)
MAX_CONSECUTIVE_FAILURES = 10


def _connection(connection_id):
    with rls.bypass():
        return (PosConnection.all_objects.select_related("provider", "branch", "organization")
                .filter(pk=connection_id).first())


# ---- process ------------------------------------------------------------------
@shared_task(bind=True, max_retries=None, ignore_result=True)
def process_sync_event(self, event_id):
    try:
        return services.process_event(event_id)
    except Exception as exc:
        # process_event already recorded the failure; stop retrying once dead-lettered.
        with rls.bypass():
            ev = SyncEvent.all_objects.filter(pk=event_id).values("status", "attempts").first()
        if not ev or ev["status"] == "dead":
            return "dead"
        raise self.retry(exc=exc, countdown=services.backoff_seconds(ev["attempts"]))


@shared_task(ignore_result=True)
def requeue_stuck_events(older_than_minutes=10, limit=500):
    """Safety net: events whose task was lost (worker crash, broker flush)."""
    cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
    with rls.bypass():
        stuck = list(SyncEvent.all_objects.filter(direction="in", status__in=["received", "failed"],
                                                  updated_at__lt=cutoff)
                     .values_list("id", "source")[:limit])
    for eid, source in stuck:
        process_sync_event.apply_async(args=[str(eid)], queue=services.SOURCE_QUEUE.get(source, "default"))
    return len(stuck)


# ---- scheduled pull -------------------------------------------------------------
@shared_task(ignore_result=True)
def schedule_pulls():
    now = timezone.now()
    with rls.bypass():
        due = list(PosConnection.all_objects.filter(status="active", sync_mode="pull", provider__supports_pull=True)
                   .filter(Q(next_pull_at__isnull=True) | Q(next_pull_at__lte=now)).values_list("id", flat=True)[:1000])
    for cid in due:
        pull_connection.apply_async(args=[str(cid)], queue="pull")
    return len(due)


@shared_task(ignore_result=True)
def pull_connection(connection_id):
    lock = f"pull:lock:{connection_id}"
    if not cache.add(lock, 1, 600):  # one pull per connection at a time
        return "locked"
    try:
        return run_pull(connection_id)
    finally:
        cache.delete(lock)


def run_pull(connection_id):
    conn = _connection(connection_id)
    if conn is None or conn.status != "active":
        return "inactive"
    adapter = get_adapter(conn.provider.code)
    now = timezone.now()
    try:
        result = adapter.pull(conn, conn.cursor)
    except AdapterError as exc:
        _pull_failed(conn, exc, now)
        return "failed"
    with rls.tenant_context(conn.organization_id):
        with transaction.atomic():
            ids = services.store_raw_events(conn, result.events, "pull")
            # The cursor only advances once the whole batch is safely stored.
            conn = PosConnection.objects.select_for_update().get(pk=conn.pk)
            conn.cursor = result.cursor
            conn.last_synced_at = now
            conn.next_pull_at = now + timedelta(minutes=conn.pull_interval_minutes)
            conn.consecutive_failures, conn.last_error = 0, ""
            conn.save(update_fields=["cursor", "last_synced_at", "next_pull_at", "consecutive_failures",
                                     "last_error", "updated_at"])
            services.enqueue_processing(ids, "pull")
    return f"stored:{len(ids)}"


def _pull_failed(conn, exc, now):
    with rls.tenant_context(conn.organization_id):
        c = PosConnection.objects.get(pk=conn.pk)
        c.consecutive_failures += 1
        c.last_error = str(exc)[:1000]
        c.next_pull_at = now + timedelta(seconds=services.backoff_seconds(c.consecutive_failures))
        if not exc.retryable or c.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            c.status = PosConnection.Status.ERROR
        c.save(update_fields=["consecutive_failures", "last_error", "next_pull_at", "status", "updated_at"])
    log.warning("pull failed for connection %s: %s", conn.pk, exc)


# ---- CSV import -----------------------------------------------------------------
@shared_task(ignore_result=True)
def import_file(job_id):
    with rls.bypass():
        job = ImportJob.all_objects.select_related("pos_connection__provider", "pos_connection__branch",
                                                   "pos_connection__organization").filter(pk=job_id).first()
    if job is None or job.status != ImportJob.Status.UPLOADED:
        return "skipped"
    conn = job.pos_connection
    with rls.tenant_context(job.organization_id):
        job = ImportJob.objects.get(pk=job.pk)
        job.status = ImportJob.Status.PROCESSING
        job.save(update_fields=["status", "updated_at"])
    try:
        with job.file.open("rb") as fh:
            raw = fh.read(settings.IMPORT_MAX_BYTES + 1)
        if len(raw) > settings.IMPORT_MAX_BYTES:
            raise AdapterError("File is too large.", retryable=False)
        parsed = get_adapter(conn.provider.code).parse_file(io.BytesIO(raw), conn)
    except Exception as exc:
        with rls.tenant_context(job.organization_id):
            job = ImportJob.objects.get(pk=job.pk)
            job.status, job.errors = ImportJob.Status.FAILED, [{"row": 0, "error": str(exc)[:500]}]
            job.finished_at = timezone.now()
            job.save()
        raise
    with rls.tenant_context(job.organization_id):
        with transaction.atomic():
            ids = services.store_raw_events(conn, parsed.events, "file", id_prefix=f"{job.sha256[:12]}:")
            job = ImportJob.objects.get(pk=job.pk)
            job.total_rows, job.valid_rows = parsed.total_rows, len(parsed.events)
            job.error_rows, job.errors = len(parsed.errors), parsed.errors[:200]
            job.status, job.finished_at = ImportJob.Status.DONE, timezone.now()
            job.save()
            services.enqueue_processing(ids, "file")
    return f"imported:{len(parsed.events)}"


# ---- push menu/prices -----------------------------------------------------------
class PushRetry(Exception):
    pass


def schedule_push(connection):
    """Debounced: many edits in a short window become a single push."""
    if connection.push_enabled and connection.status == "active":
        if cache.add(f"push:pending:{connection.pk}", 1, 20):
            transaction.on_commit(lambda: push_menu.apply_async(args=[str(connection.pk)], countdown=10, queue="push"))


@shared_task(bind=True, max_retries=6, ignore_result=True)
def push_menu(self, connection_id):
    cache.delete(f"push:pending:{connection_id}")
    conn = _connection(connection_id)
    if conn is None or conn.status != "active" or not conn.push_enabled:
        return "inactive"
    from apps.menu.models import BranchMenuItem, MenuItem

    with rls.tenant_context(conn.organization_id):
        refs = {r.local_id: r.external_id for r in ExternalRef.objects.filter(pos_connection=conn,
                                                                              entity_type="menu_item")}
        overrides = {b.menu_item_id: b for b in BranchMenuItem.objects.filter(branch_id=conn.branch_id)}
        items = []
        for m in MenuItem.objects.filter(is_active=True):
            ov = overrides.get(m.id)
            items.append({"id": m.id, "sku": m.sku, "name": m.name, "external_id": refs.get(m.id),
                          "price_minor": m.base_price_minor if ov is None or ov.price_minor is None else ov.price_minor,
                          "is_available": ov.is_available if ov else True})
        # One outbound event for the whole task, shared by its retries (the task id is stable).
        event, _ = SyncEvent.objects.get_or_create(
            pos_connection=conn, kind="menu.push", event_id=(self.request.id or uuid.uuid4().hex)[:190],
            defaults=dict(organization_id=conn.organization_id, direction="out", source="push",
                          payload={"item_count": len(items)}))
    try:
        result = get_adapter(conn.provider.code).push_menu(conn, items)
    except AdapterError as exc:
        with rls.tenant_context(conn.organization_id):
            ev = SyncEvent.objects.get(pk=event.pk)
            ev.attempts += 1
            ev.error = str(exc)[:1000]
            final = (not exc.retryable) or self.request.retries >= self.max_retries
            ev.status = "dead" if final else "failed"
            ev.save()
        if final:
            return "dead"
        raise self.retry(exc=exc, countdown=services.backoff_seconds(self.request.retries + 1))
    with rls.tenant_context(conn.organization_id):
        with transaction.atomic():
            for local_id, ext in result.mappings.items():
                ExternalRef.objects.update_or_create(
                    pos_connection=conn, entity_type="menu_item", external_id=ext,
                    defaults={"organization_id": conn.organization_id, "local_id": local_id})
            ev = SyncEvent.objects.get(pk=event.pk)
            ev.status, ev.processed_at, ev.attempts = "processed", timezone.now(), ev.attempts + 1
            ev.save()
    return f"pushed:{result.pushed}"


# ---- nightly reconciliation -----------------------------------------------------
@shared_task(ignore_result=True)
def reconcile_all(run_hour_local=2):
    """Hourly: for every branch that has passed `run_hour_local` today, reconcile
    yesterday (branch-local) once."""
    with rls.bypass():
        conns = list(PosConnection.all_objects.select_related("branch", "provider")
                     .filter(status="active", provider__is_active=True))
    done = 0
    for c in conns:
        tz = zoneinfo.ZoneInfo(c.branch.timezone)
        local_now = timezone.now().astimezone(tz)
        if local_now.hour < run_hour_local:
            continue
        yesterday = (local_now - timedelta(days=1)).date()
        if not get_adapter(c.provider.code).supports_reconciliation:
            continue
        with rls.bypass():
            if ReconciliationReport.all_objects.filter(pos_connection=c, business_date=yesterday).exists():
                continue
        reconcile_connection.apply_async(args=[str(c.pk), yesterday.isoformat()], queue="default")
        done += 1
    return done


@shared_task(ignore_result=True, autoretry_for=(AdapterError,), retry_backoff=60, max_retries=5)
def reconcile_connection(connection_id, business_date):
    return reconcile(connection_id, datetime.strptime(business_date, "%Y-%m-%d").date())


def reconcile(connection_id, day):
    from apps.orders.models import Order

    conn = _connection(connection_id)
    if conn is None:
        return None
    totals = get_adapter(conn.provider.code).daily_totals(conn, day)
    tz = zoneinfo.ZoneInfo(conn.branch.timezone)
    start, end = datetime.combine(day, time.min, tz), datetime.combine(day, time.max, tz)
    with rls.tenant_context(conn.organization_id):
        qs = Order.objects.filter(pos_connection=conn, placed_at__range=(start, end)).exclude(status="voided")
        agg = qs.aggregate(total=Sum("total_minor"))
        local_count, local_total = qs.count(), agg["total"] or 0
        report, _ = ReconciliationReport.objects.update_or_create(
            pos_connection=conn, business_date=day,
            defaults=dict(organization_id=conn.organization_id, pos_order_count=totals.order_count,
                          pos_total_minor=totals.total_minor, local_order_count=local_count,
                          local_total_minor=local_total,
                          is_match=(totals.order_count == local_count and totals.total_minor == local_total)))
    if not report.is_match:
        log.warning("reconciliation MISMATCH branch=%s date=%s pos=%s/%s local=%s/%s", conn.branch_id, day,
                    totals.order_count, totals.total_minor, local_count, local_total)
    return report
