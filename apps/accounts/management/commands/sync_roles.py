from django.core.management.base import BaseCommand

from apps.accounts.models import Role, RolePermission
from apps.accounts.seed import sync


class Command(BaseCommand):
    help = "Re-sync roles and the role->permission matrix with apps/accounts/seed.py (idempotent)."

    def handle(self, *args, **opts):
        sync(Role, RolePermission)
        self.stdout.write(self.style.SUCCESS("Roles and permissions are in sync."))
