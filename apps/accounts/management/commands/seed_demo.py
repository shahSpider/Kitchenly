from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Membership, Role, User
from apps.core import rls
from apps.menu.models import MenuCategory, MenuItem
from apps.tenants.models import Branch, Organization


class Command(BaseCommand):
    help = "Create a demo organization with branches, one user per role and a small menu (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="demo-Pass-12345")

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DEBUG=True.")
        pw = opts["password"]
        with rls.bypass():
            org, _ = Organization.objects.get_or_create(
                slug="demo", defaults=dict(name="Demo Restaurants", currency="USD", timezone="America/New_York",
                                           tax_rates={"standard": 800}))
            downtown, _ = Branch.objects.get_or_create(organization=org, code="downtown", defaults=dict(
                name="Downtown", timezone="America/New_York"))
            uptown, _ = Branch.objects.get_or_create(organization=org, code="uptown", defaults=dict(
                name="Uptown", timezone="America/New_York"))
            people = [("owner@demo.test", "org_manager", None), ("manager@demo.test", "branch_manager", downtown),
                      ("staff@demo.test", "branch_staff", downtown)]
            for email, role, branch in people:
                user = User.objects.filter(email=email).first() or User.objects.create_user(email, pw, full_name=email.split("@")[0])
                Membership.objects.get_or_create(user=user, organization=org, branch=branch, role=Role.objects.get(code=role))
            cat, _ = MenuCategory.objects.get_or_create(organization=org, name="Mains")
            for sku, name, price in (("BRG", "Burger", 1200), ("FRS", "Fries", 400), ("COK", "Cola", 250)):
                MenuItem.objects.get_or_create(organization=org, sku=sku, defaults=dict(
                    category=cat, name=name, base_price_minor=price, tax_code="standard"))
        self.stdout.write(self.style.SUCCESS(
            f"Demo ready. Sign in with owner@demo.test / manager@demo.test / staff@demo.test, password: {pw}"))
