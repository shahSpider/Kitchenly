from django.contrib import admin

from apps.core.admin import TenantAdmin

from .models import Branch, Organization


@admin.register(Organization)
class OrganizationAdmin(TenantAdmin):
    list_display = ("name", "slug", "status", "plan", "currency")
    search_fields = ("name", "slug")


@admin.register(Branch)
class BranchAdmin(TenantAdmin):
    list_display = ("name", "code", "organization", "is_active")
    search_fields = ("name", "code")
    raw_id_fields = ("organization",)
