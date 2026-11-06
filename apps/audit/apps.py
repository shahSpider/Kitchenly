from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "apps.audit"
    label = "audit"

    def ready(self):
        from django.apps import apps

        from .services import register

        for label in ("tenants.Organization", "tenants.Branch", "accounts.User", "accounts.Membership",
                      "accounts.RolePermission", "menu.MenuCategory", "menu.MenuItem", "menu.BranchMenuItem",
                      "pos.PosConnection", "orders.Refund", "orders.DayClose"):
            register(apps.get_model(label))
        register(apps.get_model("orders.Order"), only_with_actor=True)
