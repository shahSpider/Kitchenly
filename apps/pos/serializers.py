import secrets

from rest_framework import serializers

from apps.core.serializers import TenantSerializer
from apps.tenants.models import Branch

from .models import ImportJob, PosConnection, PosProvider, ReconciliationReport, SyncEvent
from .security import UnsafeURL, validate_outbound_url


class PosProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = PosProvider
        fields = ["id", "code", "name", "supports_webhook", "supports_pull", "supports_file", "supports_push",
                  "is_active"]
        read_only_fields = ["id"]


class PosConnectionSerializer(TenantSerializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects)
    provider = serializers.PrimaryKeyRelatedField(queryset=PosProvider.objects.filter(is_active=True))
    # Secrets are write-only: never returned by the API.
    credentials = serializers.JSONField(write_only=True, required=False)
    has_credentials = serializers.SerializerMethodField()
    webhook_url = serializers.SerializerMethodField()

    class Meta:
        model = PosConnection
        fields = ["id", "branch", "provider", "name", "credentials", "has_credentials", "config", "sync_mode",
                  "push_enabled", "pull_interval_minutes", "status", "last_synced_at", "last_error", "webhook_url",
                  "created_at"]
        read_only_fields = ["id", "last_synced_at", "last_error", "created_at"]

    def get_has_credentials(self, obj):
        return bool(obj.credentials_encrypted)

    def get_webhook_url(self, obj):
        request = self.context.get("request")
        path = f"/webhooks/pos/{obj.pk}/"
        return request.build_absolute_uri(path) if request else path

    def validate_pull_interval_minutes(self, v):
        if not 1 <= v <= 1440:
            raise serializers.ValidationError("Between 1 and 1440 minutes.")
        return v

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be an object.")
        if value.get("base_url"):
            try:
                validate_outbound_url(value["base_url"])
            except UnsafeURL as exc:
                raise serializers.ValidationError({"base_url": str(exc)})
        return value

    def validate(self, attrs):
        provider = attrs.get("provider", getattr(self.instance, "provider", None))
        mode = attrs.get("sync_mode", getattr(self.instance, "sync_mode", "webhook"))
        if not getattr(provider, f"supports_{mode}", False):
            raise serializers.ValidationError({"sync_mode": f"{provider.name} does not support '{mode}'."})
        if attrs.get("push_enabled") and not provider.supports_push:
            raise serializers.ValidationError({"push_enabled": f"{provider.name} does not support push."})
        return attrs

    def create(self, validated):
        creds = validated.pop("credentials", None)
        secret = secrets.token_urlsafe(32)
        obj = PosConnection(credentials_encrypted=creds, webhook_secret=secret, **validated)
        obj.save()
        obj._plain_secret = secret  # revealed once, in the creation response only
        return obj

    def update(self, instance, validated):
        if "credentials" in validated:
            instance.credentials_encrypted = validated.pop("credentials")
        return super().update(instance, validated)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if getattr(instance, "_plain_secret", None):
            data["webhook_secret"] = instance._plain_secret
        return data


class SyncEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncEvent
        fields = ["id", "pos_connection", "direction", "source", "kind", "event_id", "payload", "status",
                  "attempts", "error", "received_at", "processed_at"]
        read_only_fields = fields


class ImportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportJob
        fields = ["id", "pos_connection", "original_name", "sha256", "status", "total_rows", "valid_rows",
                  "error_rows", "errors", "created_at", "finished_at"]
        read_only_fields = fields


class ReconciliationSerializer(serializers.ModelSerializer):
    branch = serializers.UUIDField(source="pos_connection.branch_id", read_only=True)

    class Meta:
        model = ReconciliationReport
        fields = ["id", "pos_connection", "branch", "business_date", "pos_order_count", "pos_total_minor",
                  "local_order_count", "local_total_minor", "is_match"]
        read_only_fields = fields
