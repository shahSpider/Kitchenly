"""The one interface every POS vendor implements. Everything outside
`apps/pos/adapters` only ever sees `CanonicalEvent`, never a vendor payload."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


class AdapterError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class WebhookRejected(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


class UnsupportedEvent(Exception):
    """A valid event we deliberately do not act on (marked processed, not failed)."""


@dataclass
class RawEvent:
    kind: str
    event_id: str
    payload: dict


@dataclass
class CanonicalItem:
    name: str
    quantity: int
    unit_price_minor: int
    line_total_minor: int
    sku: str = ""
    external_id: str | None = None
    external_menu_item_id: str | None = None


@dataclass
class CanonicalPayment:
    method: str
    amount_minor: int
    status: str = "captured"
    external_id: str | None = None
    reference: str = ""
    paid_at: datetime | None = None


@dataclass
class CanonicalOrder:
    external_id: str
    placed_at: datetime
    status: str = "open"            # open|preparing|ready|served|completed|voided
    order_type: str = "dine_in"
    number: str = ""
    currency: str | None = None
    subtotal_minor: int | None = None
    tax_minor: int = 0
    discount_minor: int = 0
    total_minor: int | None = None
    items: list = field(default_factory=list)
    payments: list = field(default_factory=list)


@dataclass
class CanonicalEvent:
    kind: str                       # order.created | order.updated | order.paid | order.voided
    external_order_id: str
    occurred_at: datetime           # POS timestamp/version: older never overwrites newer
    order: CanonicalOrder | None = None


@dataclass
class PullResult:
    events: list
    cursor: str


@dataclass
class FileParseResult:
    events: list
    errors: list                    # [{"row": int, "error": str}]
    total_rows: int = 0


@dataclass
class PushResult:
    pushed: int
    mappings: dict = field(default_factory=dict)  # local menu item id -> external id


@dataclass
class DailyTotals:
    order_count: int
    total_minor: int


class PosAdapter:
    code = "base"
    supports_reconciliation = False

    def verify_webhook(self, request, connection) -> bool:
        """Signature + timestamp check against the per-connection secret."""
        raise NotImplementedError

    def parse_webhook(self, request, connection) -> list[RawEvent]:
        """Cheap: identify events and keep the raw payload. No business logic."""
        raise NotImplementedError

    def translate(self, kind: str, payload: dict, connection) -> list[CanonicalEvent]:
        """Vendor payload -> canonical events (runs in the worker)."""
        raise NotImplementedError

    def pull(self, connection, since: str) -> PullResult:
        raise NotImplementedError

    def parse_file(self, file, connection) -> FileParseResult:
        raise NotImplementedError

    def push_menu(self, connection, items: list[dict]) -> PushResult:
        raise NotImplementedError

    def daily_totals(self, connection, business_date: date) -> DailyTotals:
        raise NotImplementedError


_REGISTRY: dict[str, PosAdapter] = {}


def register(adapter_cls):
    _REGISTRY[adapter_cls.code] = adapter_cls()
    return adapter_cls


def get_adapter(code: str) -> PosAdapter:
    from . import generic  # noqa: F401  (registers built-in adapters)

    try:
        return _REGISTRY[code]
    except KeyError:
        raise AdapterError(f"No adapter registered for provider '{code}'", retryable=False)
