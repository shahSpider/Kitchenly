from django.contrib import admin

from apps.core.admin import TenantAdmin

from .models import Membership, Role, RolePermission, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "full_name", "is_active", "is_platform_admin", "totp_enabled")
    search_fields = ("email", "full_name")
    exclude = ("password", "totp_secret", "last_login")


@admin.register(Membership)
class MembershipAdmin(TenantAdmin):
    list_display = ("user", "organization", "branch", "role", "is_active")
    raw_id_fields = ("user", "organization", "branch")


admin.site.register(Role)
admin.site.register(RolePermission)
