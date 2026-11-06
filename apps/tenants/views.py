import zoneinfo
from datetime import datetime, time

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.api import TenantViewMixin
from apps.core.viewsets import TenantModelViewSet

from .models import Branch, Organization
from .serializers import BranchSerializer, OrganizationSerializer


class OrganizationViewSet(TenantViewMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                          mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Platform admins manage every organization; managers see/edit their own.
    No DELETE: suspend instead (history must stay intact)."""

    resource = "organization"
    serializer_class = OrganizationSerializer
    queryset = Organization.all_objects.order_by("name")
    branch_attr = None
    filterset_fields = ["status", "plan"]
    search_fields = ["name", "slug"]

    @action(detail=True, methods=["post"], required_action="update")
    def suspend(self, request, pk=None):
        return self._set_status(request.access, "suspended")

    @action(detail=True, methods=["post"], required_action="update")
    def activate(self, request, pk=None):
        return self._set_status(request.access, "active")

    def _set_status(self, ctx, value):
        if not ctx.is_platform_admin:
            self.permission_denied(self.request, message="Only platform admins can change organization status.")
        org = self.get_object()
        org.status = value
        org.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(org).data)


class BranchViewSet(TenantModelViewSet):
    resource = "branch"
    serializer_class = BranchSerializer
    queryset = Branch.all_objects.order_by("name")
    branch_attr = "id"
    filterset_fields = ["is_active"]
    search_fields = ["name", "code"]

    @action(detail=True, methods=["post"], url_path="close-day", required_action="close_day")
    @transaction.atomic
    def close_day(self, request, pk=None):
        """Close a business day (branch-local date) and snapshot its totals."""
        from apps.orders.models import DayClose, Order

        branch = self.get_object()
        raw = request.data.get("business_date")
        tz = zoneinfo.ZoneInfo(branch.timezone)
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date() if raw else timezone.now().astimezone(tz).date()
        except ValueError:
            raise ValidationError({"business_date": "Use YYYY-MM-DD."})
        if DayClose.objects.filter(branch=branch, business_date=day).exists():
            return Response({"detail": "Day already closed."}, status=status.HTTP_409_CONFLICT)
        start = datetime.combine(day, time.min, tz)
        end = datetime.combine(day, time.max, tz)
        open_orders = Order.objects.filter(branch=branch, placed_at__range=(start, end),
                                           status__in=["open", "preparing", "ready", "served"]).count()
        if open_orders:
            raise ValidationError({"detail": f"{open_orders} order(s) are still open."})
        agg = Order.objects.filter(branch=branch, placed_at__range=(start, end)).exclude(status="voided") \
            .aggregate(n=Count("id"), total=Sum("total_minor"))
        close = DayClose.objects.create(organization=request.organization, branch=branch, business_date=day,
                                        order_count=agg["n"] or 0, total_minor=agg["total"] or 0,
                                        closed_by=request.user)
        return Response({"id": str(close.id), "business_date": str(day), "order_count": close.order_count,
                         "total_minor": close.total_minor}, status=status.HTTP_201_CREATED)
