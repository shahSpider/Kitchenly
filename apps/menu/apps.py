from django.apps import AppConfig


class MenuConfig(AppConfig):
    name = "apps.menu"
    label = "menu"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from .models import BranchMenuItem, MenuCategory, MenuItem
        from .services import bump_menu_version

        def _bump(sender, instance, **kw):
            bump_menu_version(instance.organization_id)
            if sender is not MenuCategory:
                _push(instance)

        def _push(instance):
            """This system is master for menu/prices: push changes to two-way POS connections."""
            from apps.pos.models import PosConnection
            from apps.pos.tasks import schedule_push

            conns = PosConnection.all_objects.filter(organization_id=instance.organization_id, push_enabled=True,
                                                     status="active")
            if getattr(instance, "branch_id", None):
                conns = conns.filter(branch_id=instance.branch_id)
            for conn in conns:
                schedule_push(conn)

        for m in (MenuCategory, MenuItem, BranchMenuItem):
            post_save.connect(_bump, sender=m, weak=False, dispatch_uid=f"menu-bump-{m.__name__}")
            post_delete.connect(_bump, sender=m, weak=False, dispatch_uid=f"menu-bump-del-{m.__name__}")
