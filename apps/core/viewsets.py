from rest_framework import viewsets

from .api import TenantViewMixin


class TenantModelViewSet(TenantViewMixin, viewsets.ModelViewSet):
    """ModelViewSet with tenant scoping, role checks and org injection on create."""

    def perform_create(self, serializer):
        self.check_branch_permission(serializer.validated_data.get("branch"))
        serializer.save(organization=self.request.organization)

    def perform_update(self, serializer):
        self.check_branch_permission(serializer.validated_data.get("branch"))
        serializer.save()


class TenantReadOnlyViewSet(TenantViewMixin, viewsets.ReadOnlyModelViewSet):
    pass
