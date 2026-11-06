"""Order business rules shared by the API and the POS sync worker."""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.menu.services import resolve_item

from .models import Order, OrderItem, Payment, Refund

# Manual-order status machine (POS orders are managed by the POS).
TRANSITIONS = {
    "open": {"preparing", "ready", "served", "completed"},
    "preparing": {"ready", "served", "completed"},
    "ready": {"served", "completed"},
    "served": {"completed"},
    "completed": set(),
    "voided": set(),
}


class RuleViolation(Exception):
    pass


def tax_for(line_total, tax_code, tax_rates):
    bps = tax_rates.get(tax_code, 0) if tax_code else 0
    return int((Decimal(line_total) * bps / Decimal(10000)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def recompute_totals(order, tax_minor=None):
    subtotal = sum(i.line_total_minor for i in order.items.all())
    order.subtotal_minor = subtotal
    if tax_minor is not None:
        order.tax_minor = tax_minor
    order.total_minor = max(subtotal + order.tax_minor - order.discount_minor, 0)


@transaction.atomic
def create_manual_order(*, organization, branch, user, order_type, items, discount_minor=0, notes="",
                        payments=()):
    """items: [{"menu_item": MenuItem, "quantity": int}]. Prices are resolved
    server-side from the branch menu; the client never supplies a price."""
    order = Order(organization=organization, branch=branch, order_type=order_type, notes=notes,
                  currency=organization.currency, placed_at=timezone.now(), created_by=user,
                  order_number=str(_next_number(branch)), discount_minor=discount_minor)
    order.save()
    tax = 0
    for row in items:
        item, price, available = resolve_item(branch.id, row["menu_item"].id)
        if not available:
            raise RuleViolation(f"'{item.name}' is not available at this branch.")
        line = price * row["quantity"]
        tax += tax_for(line, item.tax_code, organization.tax_rates)
        OrderItem.objects.create(organization=organization, order=order, menu_item=item, name_snapshot=item.name,
                                 sku_snapshot=item.sku, quantity=row["quantity"], unit_price_minor=price,
                                 line_total_minor=line)
    if discount_minor and discount_minor > sum(i.line_total_minor for i in order.items.all()):
        raise RuleViolation("Discount exceeds the subtotal.")
    recompute_totals(order, tax_minor=tax)
    order.save()
    for p in payments:
        add_payment(order, **p)
    return order


def _next_number(branch):
    from apps.tenants.models import Branch

    return Branch.next_order_number(branch.id)


@transaction.atomic
def add_payment(order, *, method, amount_minor, reference="", status=Payment.Status.CAPTURED):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == Order.Status.VOIDED:
        raise RuleViolation("Cannot pay a voided order.")
    paid = sum(p.amount_minor for p in order.payments.filter(status__in=["captured", "partially_refunded"]))
    if amount_minor <= 0:
        raise RuleViolation("Amount must be positive.")
    if paid + amount_minor > order.total_minor:
        raise RuleViolation("Payments exceed the order total.")
    return Payment.objects.create(organization=order.organization, order=order, method=method,
                                  amount_minor=amount_minor, reference=reference, status=status,
                                  paid_at=timezone.now())


@transaction.atomic
def change_status(order, new_status):
    if order.pos_connection_id:
        raise RuleViolation("This order is managed by the POS system.")
    if new_status not in TRANSITIONS.get(order.status, set()):
        raise RuleViolation(f"Cannot move an order from '{order.status}' to '{new_status}'.")
    order.status = new_status
    if new_status == Order.Status.COMPLETED:
        order.closed_at = timezone.now()
    order.save(update_fields=["status", "closed_at", "updated_at"])
    return order


@transaction.atomic
def decide_refund(refund, approver, approve: bool):
    refund = Refund.objects.select_for_update().get(pk=refund.pk)
    if refund.status != Refund.Status.REQUESTED:
        raise RuleViolation("This request was already decided.")
    refund.approved_by = approver
    refund.decided_at = timezone.now()
    if not approve:
        refund.status = Refund.Status.REJECTED
        refund.save()
        return refund
    order = Order.objects.select_for_update().get(pk=refund.order_id)
    if refund.kind == Refund.Kind.VOID:
        order.status = Order.Status.VOIDED
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "closed_at", "updated_at"])
    else:
        payment = Payment.objects.select_for_update().get(pk=refund.payment_id)
        done = sum(r.amount_minor for r in payment.refunds.filter(status=Refund.Status.APPROVED))
        if done + refund.amount_minor > payment.amount_minor:
            raise RuleViolation("Refund exceeds the amount paid.")
        payment.status = (Payment.Status.REFUNDED if done + refund.amount_minor == payment.amount_minor
                          else Payment.Status.PARTIALLY_REFUNDED)
        payment.save(update_fields=["status", "updated_at"])
    refund.status = Refund.Status.APPROVED
    refund.save()
    return refund
