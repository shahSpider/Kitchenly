"""Reference adapter ("generic"): a documented JSON-over-HTTPS + CSV contract
(see docs/POS_INTEGRATION.md). Vendor adapters follow the same shape: subclass
PosAdapter, translate to canonical events, `@register`."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import time
from collections import OrderedDict
from datetime import UTC, date, datetime

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime

from apps.pos.security import UnsafeURL, validate_outbound_url

from .base import (
    AdapterError,
    CanonicalEvent,
    CanonicalItem,
    CanonicalOrder,
    CanonicalPayment,
    DailyTotals,
    FileParseResult,
    PosAdapter,
    PullResult,
    PushResult,
    RawEvent,
    UnsupportedEvent,
    WebhookRejected,
    register,
)

KINDS = {"order.created", "order.updated", "order.paid", "order.voided"}
MAX_PULL_PAGES = 10


def _int(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer (minor units)")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _dt(value, name):
    dt = parse_datetime(value) if isinstance(value, str) else None
    if dt is None:
        raise ValueError(f"{name} must be an ISO-8601 datetime")
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_order(d: dict) -> CanonicalOrder:
    if not isinstance(d, dict) or not d.get("id"):
        raise ValueError("order.id is required")
    items = []
    for i in d.get("items", []):
        qty = _int(i.get("quantity"), "item.quantity", 1)
        unit = _int(i.get("unit_price"), "item.unit_price")
        line = _int(i["line_total"], "item.line_total") if "line_total" in i else qty * unit
        items.append(CanonicalItem(
            name=str(i.get("name") or i.get("sku") or "Item")[:200], quantity=qty, unit_price_minor=unit,
            line_total_minor=line, sku=str(i.get("sku") or "")[:64],
            external_id=str(i["id"]) if i.get("id") is not None else None,
            external_menu_item_id=str(i["product_id"]) if i.get("product_id") is not None else None))
    payments = []
    for p in d.get("payments", []):
        payments.append(CanonicalPayment(
            method=p.get("method") if p.get("method") in ("cash", "card", "online") else "other",
            amount_minor=_int(p.get("amount"), "payment.amount", 1),
            status=p.get("status") if p.get("status") in ("pending", "captured", "failed") else "captured",
            external_id=str(p["id"]) if p.get("id") is not None else None,
            reference=str(p.get("reference") or "")[:128],
            paid_at=_dt(p["paid_at"], "payment.paid_at") if p.get("paid_at") else None))
    status = d.get("status", "open")
    if status not in ("open", "preparing", "ready", "served", "completed", "voided"):
        raise ValueError(f"unknown order status '{status}'")
    return CanonicalOrder(
        external_id=str(d["id"]), placed_at=_dt(d.get("placed_at"), "order.placed_at"), status=status,
        order_type=d.get("type") if d.get("type") in ("dine_in", "takeaway", "delivery") else "dine_in",
        number=str(d.get("number") or "")[:40], currency=(d.get("currency") or None),
        subtotal_minor=_int(d["subtotal"], "order.subtotal") if "subtotal" in d else None,
        tax_minor=_int(d.get("tax", 0), "order.tax"), discount_minor=_int(d.get("discount", 0), "order.discount"),
        total_minor=_int(d["total"], "order.total") if "total" in d else None, items=items, payments=payments)


@register
class GenericAdapter(PosAdapter):
    code = "generic"
    supports_reconciliation = True

    # ---- webhook ------------------------------------------------------------
    def verify_webhook(self, request, connection) -> bool:
        secret = connection.webhook_secret
        ts = request.headers.get("X-Kitchenly-Timestamp", "")
        sig = request.headers.get("X-Kitchenly-Signature", "")
        if not secret or not ts.isdigit() or not sig.startswith("sha256="):
            return False
        if abs(time.time() - int(ts)) > settings.WEBHOOK_TIMESTAMP_TOLERANCE:
            return False  # replay protection
        expected = hmac.new(secret.encode(), ts.encode() + b"." + request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig[len("sha256="):])

    def parse_webhook(self, request, connection) -> list[RawEvent]:
        try:
            body = json.loads(request.body)
        except ValueError:
            raise WebhookRejected("Body is not valid JSON.", status=400)
        events = body.get("events") if isinstance(body, dict) and "events" in body else [body]
        out = []
        for e in events:
            if not isinstance(e, dict) or not e.get("event_id") or not e.get("type"):
                raise WebhookRejected("Every event needs event_id and type.", status=400)
            out.append(RawEvent(kind=str(e["type"])[:80], event_id=str(e["event_id"])[:190], payload=e))
        return out

    # ---- translate ----------------------------------------------------------
    def translate(self, kind, payload, connection):
        if kind not in KINDS:
            raise UnsupportedEvent(kind)
        data = payload.get("data", {})
        occurred = _dt(payload.get("occurred_at"), "occurred_at")
        order_data = data.get("order")
        if kind == "order.voided" and order_data is None:
            return [CanonicalEvent(kind, str(data.get("order_id") or ""), occurred, None)]
        order = parse_order(order_data)
        return [CanonicalEvent(kind, order.external_id, occurred, order)]

    # ---- HTTP helpers -------------------------------------------------------
    def _call(self, connection, method, path, **kw):
        base = (connection.config or {}).get("base_url")
        if not base:
            raise AdapterError("Connection has no base_url configured.", retryable=False)
        url = base.rstrip("/") + path
        try:
            validate_outbound_url(url)
        except UnsafeURL as exc:
            raise AdapterError(str(exc), retryable=False)
        headers = {"Authorization": f"Bearer {(connection.credentials_encrypted or {}).get('api_key', '')}"}
        try:
            resp = requests.request(method, url, headers=headers, timeout=settings.POS_HTTP_TIMEOUT,
                                    allow_redirects=False, **kw)
        except requests.RequestException as exc:
            raise AdapterError(f"POS unreachable: {exc.__class__.__name__}")
        if resp.status_code >= 400:
            raise AdapterError(f"POS returned HTTP {resp.status_code}",
                               retryable=resp.status_code >= 500 or resp.status_code == 429)
        try:
            return resp.json()
        except ValueError:
            raise AdapterError("POS returned invalid JSON")

    # ---- pull ---------------------------------------------------------------
    def pull(self, connection, since):
        cursor, events = since or "", []
        for _ in range(MAX_PULL_PAGES):
            data = self._call(connection, "GET", "/orders", params={"cursor": cursor, "limit": 200})
            for o in data.get("orders", []):
                version = o.get("version") or o.get("updated_at") or o.get("placed_at")
                events.append(RawEvent(
                    kind="order.updated", event_id=f"{o.get('id')}@{version}",
                    payload={"event_id": f"{o.get('id')}@{version}", "type": "order.updated",
                             "occurred_at": o.get("updated_at") or o.get("placed_at"), "data": {"order": o}}))
            nxt = data.get("next_cursor")
            if not nxt or nxt == cursor:
                break
            cursor = nxt
        return PullResult(events=events, cursor=cursor)

    # ---- file import --------------------------------------------------------
    REQUIRED = ["order_id", "placed_at", "item_name", "quantity", "unit_price_minor"]

    def parse_file(self, file, connection):
        text = io.TextIOWrapper(file, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        missing = [c for c in self.REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            return FileParseResult([], [{"row": 1, "error": f"Missing columns: {', '.join(missing)}"}], 0)
        orders: "OrderedDict[str, dict]" = OrderedDict()
        errors, total = [], 0
        bad_orders = set()
        imported_at = datetime.now(UTC)
        for n, row in enumerate(reader, start=2):
            total += 1
            oid = (row.get("order_id") or "").strip()
            try:
                if not oid:
                    raise ValueError("order_id is required")
                qty, unit = int(row["quantity"]), int(row["unit_price_minor"])
                if qty < 1 or unit < 0:
                    raise ValueError("quantity must be >= 1 and unit_price_minor >= 0")
                _dt(row["placed_at"], "placed_at")
                status = (row.get("status") or "completed").strip()
                if status not in ("open", "preparing", "ready", "served", "completed", "voided"):
                    raise ValueError(f"unknown status '{status}'")
                payment = None
                if (row.get("payment_method") or "").strip():
                    payment = {"method": row["payment_method"].strip(), "amount": int(row["payment_amount_minor"])}
            except (ValueError, KeyError, TypeError) as exc:
                errors.append({"row": n, "error": str(exc) if str(exc) else "invalid value"})
                if oid:
                    bad_orders.add(oid)
                continue
            o = orders.setdefault(oid, {"id": oid, "placed_at": row["placed_at"], "status": status,
                                        "type": (row.get("order_type") or "dine_in").strip(),
                                        "currency": (row.get("currency") or "").strip() or None,
                                        "tax": 0, "discount": 0, "items": [], "payments": [], "_tax_seen": False})
            o["items"].append({"sku": (row.get("item_sku") or "").strip(), "name": row["item_name"].strip(),
                               "quantity": qty, "unit_price": unit})
            try:
                if not o["_tax_seen"] and (row.get("tax_minor") or "").strip():
                    o["tax"], o["_tax_seen"] = int(row["tax_minor"]), True
                if (row.get("discount_minor") or "").strip():
                    o["discount"] = int(row["discount_minor"])
            except ValueError:
                errors.append({"row": n, "error": "tax_minor/discount_minor must be integers"})
                bad_orders.add(oid)
            if payment and not o["payments"]:
                o["payments"].append(payment)
        events = []
        for oid, o in orders.items():
            if oid in bad_orders:
                continue  # never import half an order
            o.pop("_tax_seen")
            sub = sum(i["quantity"] * i["unit_price"] for i in o["items"])
            o.update(subtotal=sub, total=max(sub + o["tax"] - o["discount"], 0))
            payload = {"event_id": oid, "type": "order.updated", "occurred_at": imported_at.isoformat(),
                       "data": {"order": o}}
            events.append(RawEvent(kind="order.updated", event_id=oid, payload=payload))
        return FileParseResult(events=events, errors=errors, total_rows=total)

    # ---- push ---------------------------------------------------------------
    def push_menu(self, connection, items):
        body = {"items": [{"sku": i["sku"], "external_id": i.get("external_id"), "name": i["name"],
                           "price": i["price_minor"], "available": i["is_available"]} for i in items]}
        data = self._call(connection, "POST", "/menu/sync", json=body)
        by_sku = {i["sku"]: i["id"] for i in items}
        mappings = {by_sku[r["sku"]]: str(r["external_id"]) for r in data.get("items", [])
                    if r.get("sku") in by_sku and r.get("external_id") is not None}
        return PushResult(pushed=len(items), mappings=mappings)

    # ---- reconciliation -----------------------------------------------------
    def daily_totals(self, connection, business_date: date):
        data = self._call(connection, "GET", "/reports/daily", params={"date": business_date.isoformat()})
        return DailyTotals(order_count=_int(data.get("order_count"), "order_count"),
                           total_minor=_int(data.get("total_minor"), "total_minor"))
