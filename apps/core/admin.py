from django.contrib import admin


class TenantAdmin(admin.ModelAdmin):
    """Admin runs with cross-tenant access (platform admins only; audit-logged
    by AdminSupportAccessMiddleware), so it must bypass the scoped manager."""

    def get_queryset(self, request):
        qs = self.model.all_objects.get_queryset()
        ordering = self.get_ordering(request)
        return qs.order_by(*ordering) if ordering else qs

    def has_delete_permission(self, request, obj=None):
        return False  # deletion is destructive across tenants; use suspend/deactivate flows
