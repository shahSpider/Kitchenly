"""Plain helper factories. Fixtures are created inside rls.bypass() (system
context); API calls in tests run with the real tenant scoping + RLS."""
import itertools

from django.utils import timezone

from apps.accounts.models import Membership, Role, User
from apps.core import rls
from apps.menu.models import BranchMenuItem, MenuCategory, MenuItem
from apps.orders.models import Order, OrderItem, Payment, Refund
from apps.pos.models import PosConnection, PosProvider, SyncEvent
from apps.tenants.models import Branch, Organization

PASSWORD = "Sup3r-secret-pass"
_n = itertools.count(1)


def org(slug=None, **kw):
    slug = slug or f"org{next(_n)}"
    with rls.bypass():
        return Organization.objects.create(name=slug.title(), slug=slug, currency=kw.pop("currency", "USD"), **kw)


def branch(organization, code=None, **kw):
    code = code or f"b{next(_n)}"
    with rls.bypass():
        return Branch.objects.create(organization=organization, name=f"Branch {code}", code=code, **kw)


def user(email=None, platform_admin=False, **kw):
    email = email or f"user{next(_n)}@example.com"
    with rls.bypass():
        return User.objects.create_user(email, PASSWORD, is_platform_admin=platform_admin, **kw)


def grant(u, organization, role, br=None):
    with rls.bypass():
        return Membership.objects.create(user=u, organization=organization, branch=br,
                                         role=Role.objects.get(code=role))


def person(organization, role, br=None, email=None):
    u = user(email)
    grant(u, organization, role, br)
    return u


def menu_item(organization, sku=None, price=1000, tax_code="", category=None, name=None):
    sku = sku or f"sku{next(_n)}"
    with rls.bypass():
        cat = category or MenuCategory.objects.create(organization=organization, name="Mains")
        return MenuItem.objects.create(organization=organization, category=cat, name=name or f"Item {sku}",
                                       sku=sku, base_price_minor=price, tax_code=tax_code)


def override(organization, br, item, price=None, available=True):
    with rls.bypass():
        return BranchMenuItem.objects.create(organization=organization, branch=br, menu_item=item,
                                             price_minor=price, is_available=available)


def provider(code="generic"):
    return PosProvider.objects.get(code=code)


def connection(organization, br, mode="webhook", secret="whsec_test", **kw):
    with rls.bypass():
        return PosConnection.objects.create(
            organization=organization, branch=br, provider=provider(), sync_mode=mode, webhook_secret=secret,
            credentials_encrypted={"api_key": "k"}, config=kw.pop("config", {"base_url": "https://pos.example.com"}),
            **kw)


def order(organization, br, total=2000, status="open", **kw):
    with rls.bypass():
        o = Order.objects.create(organization=organization, branch=br, status=status, currency="USD",
                                 placed_at=kw.pop("placed_at", timezone.now()), subtotal_minor=total,
                                 total_minor=total, order_number=str(next(_n)), **kw)
        OrderItem.objects.create(organization=organization, order=o, name_snapshot="Burger", quantity=1,
                                 unit_price_minor=total, line_total_minor=total)
        return o


def payment(o, amount=None, method="cash"):
    with rls.bypass():
        return Payment.objects.create(organization=o.organization, order=o, method=method,
                                      amount_minor=amount or o.total_minor, paid_at=timezone.now())


def refund(o, p=None, user_=None):
    with rls.bypass():
        return Refund.objects.create(organization=o.organization, order=o, payment=p, amount_minor=100,
                                     requested_by=user_)


def sync_event(conn, event_id="e1", status="received"):
    with rls.bypass():
        return SyncEvent.objects.create(organization=conn.organization, pos_connection=conn, kind="order.updated",
                                        event_id=event_id, payload={}, status=status)
