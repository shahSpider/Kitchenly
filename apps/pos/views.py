import hashlib
import secrets

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.core.api import TenantViewMixin
from apps.core.pagination import CreatedAtCursorPagination, ReceivedAtCursorPagination
from apps.core.viewsets import TenantModelViewSet

from . import services, tasks
from .models import ImportJob, PosConnection, PosProvider, ReconciliationReport, SyncEvent
from .serializers import (
    ImportJobSerializer,
    PosConnectionSerializer,
    PosProviderSerializer,
    ReconciliationSerializer,
    SyncEventSerializer,
)


class PosProviderViewSet(TenantViewMixin, viewsets.ModelViewSet):
    """Global catalogue of supported POS adapters. Platform admins manage it."""

    resource = "pos_provider"
    requires_organization = False
    serializer_class = PosProviderSerializer
    branch_attr = None
    pagination_class = None

    def get_queryset(self):
        qs = PosProvider.objects.order_by("name")
        return qs if self.request.access.is_platform_admin else qs.filter(is_active=True)


class PosConnectionViewSet(TenantModelViewSet):
    resource = "pos_connection"
    serializer_class = PosConnectionSerializer
    queryset = PosConnection.all_objects.select_related("provider")
    filterset_fields = ["branch", "provider", "status", "sync_mode"]

    def perform_update(self, serializer):
        super().perform_update(serializer)
        tasks.schedule_push(serializer.instance)

    @action(detail=True, methods=["post"], url_path="rotate-secret", required_action="update")
    def rotate_secret(self, request, pk=None):
        conn = self.get_object()
        conn.webhook_secret = secrets.token_urlsafe(32)
        conn.save(update_fields=["webhook_secret", "updated_at"])
        return Response({"webhook_secret": conn.webhook_secret})

    @action(detail=True, methods=["post"], url_path="sync-now", required_action="update")
    def sync_now(self, request, pk=None):
        conn = self.get_object()
        if conn.sync_mode != "pull":
            raise ValidationError({"detail": "Only pull connections can be synced on demand."})
        transaction.on_commit(lambda: tasks.pull_connection.apply_async(args=[str(conn.pk)], queue="pull"))
        return Response({"detail": "Pull queued."}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"], url_path="push-menu", required_action="update")
    def push_menu(self, request, pk=None):
        conn = self.get_object()
        if not conn.push_enabled:
            raise ValidationError({"detail": "Push is not enabled for this connection."})
        transaction.on_commit(lambda: tasks.push_menu.apply_async(args=[str(conn.pk)], queue="push"))
        return Response({"detail": "Push queued."}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"], required_action="update", parser_classes=[MultiPartParser])
    def imports(self, request, pk=None):
        """Upload a CSV. A file whose hash was already imported is rejected."""
        conn = self.get_object()
        if not conn.provider.supports_file:
            raise ValidationError({"detail": "This POS provider does not support file import."})
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "Required."})
        if upload.size > settings.IMPORT_MAX_BYTES:
            raise ValidationError({"file": "File is too large."})
        content = upload.read()
        digest = hashlib.sha256(content).hexdigest()
        existing = ImportJob.objects.filter(pos_connection=conn, sha256=digest).first()
        if existing:
            return Response({"detail": "This file was already imported.", "import_id": str(existing.id)},
                            status=status.HTTP_409_CONFLICT)
        job = ImportJob(organization=request.organization, pos_connection=conn, original_name=upload.name[:255],
                        sha256=digest, created_by=request.user)
        job.file.save(f"{digest[:16]}.csv", ContentFile(content), save=False)
        job.save()
        transaction.on_commit(lambda: tasks.import_file.apply_async(args=[str(job.pk)], queue="import"))
        return Response(ImportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class SyncEventViewSet(TenantViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Event log + dead-letter list. Managers review failures and retry them."""

    resource = "sync_event"
    serializer_class = SyncEventSerializer
    queryset = SyncEvent.all_objects.all()
    branch_attr = "pos_connection.branch_id"
    pagination_class = ReceivedAtCursorPagination
    filterset_fields = ["status", "kind", "pos_connection", "source", "direction"]

    @action(detail=True, methods=["post"], required_action="update")
    def retry(self, request, pk=None):
        ev = self.get_object()
        if ev.status not in ("failed", "dead") or ev.direction != "in":
            raise ValidationError({"detail": "Only failed or dead inbound events can be retried."})
        ev.status, ev.attempts, ev.error = "received", 0, ""
        ev.save(update_fields=["status", "attempts", "error", "updated_at"])
        services.enqueue_processing([ev.pk], ev.source)
        return Response(self.get_serializer(ev).data, status=status.HTTP_202_ACCEPTED)


class ImportJobViewSet(TenantViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    resource = "pos_connection"
    serializer_class = ImportJobSerializer
    queryset = ImportJob.all_objects.all()
    branch_attr = "pos_connection.branch_id"
    pagination_class = CreatedAtCursorPagination
    filterset_fields = ["pos_connection", "status"]


class ReconciliationViewSet(TenantViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                            viewsets.GenericViewSet):
    resource = "report"
    serializer_class = ReconciliationSerializer
    queryset = ReconciliationReport.all_objects.select_related("pos_connection")
    branch_attr = "pos_connection.branch_id"
    filterset_fields = {"is_match": ["exact"], "business_date": ["gte", "lte"], "pos_connection": ["exact"]}
