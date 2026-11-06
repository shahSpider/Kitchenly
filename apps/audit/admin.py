from django.contrib import admin

from apps.core.admin import TenantAdmin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(TenantAdmin):
    list_display = ("created_at", "action", "entity", "entity_id", "actor_id", "organization_id")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
