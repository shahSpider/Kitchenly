from django.contrib import admin

from apps.core.admin import TenantAdmin

from .models import PosConnection, PosProvider, SyncEvent


@admin.register(PosProvider)
class PosProviderAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "supports_webhook", "supports_pull", "supports_file", "supports_push")


@admin.register(PosConnection)
class PosConnectionAdmin(TenantAdmin):
    list_display = ("__str__", "organization", "branch", "sync_mode", "status")
    exclude = ("credentials_encrypted", "webhook_secret")
    raw_id_fields = ("organization", "branch")


@admin.register(SyncEvent)
class SyncEventAdmin(TenantAdmin):
    list_display = ("kind", "event_id", "status", "attempts", "received_at")
    list_filter = ("status",)
    readonly_fields = [f.name for f in SyncEvent._meta.fields]
