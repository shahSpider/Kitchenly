from rest_framework import mixins, serializers, viewsets

from apps.core.api import TenantViewMixin
from apps.core.pagination import CreatedAtCursorPagination

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "actor_id", "organization_id", "action", "entity", "entity_id", "before", "after", "ip",
                  "request_id", "created_at"]
        read_only_fields = fields


class AuditLogViewSet(TenantViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    resource = "audit_log"
    serializer_class = AuditLogSerializer
    queryset = AuditLog.all_objects.all()
    branch_attr = None
    pagination_class = CreatedAtCursorPagination
    filterset_fields = {"entity": ["exact"], "action": ["exact"], "actor_id": ["exact"], "created_at": ["gte", "lte"]}
