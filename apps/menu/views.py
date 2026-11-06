from django.shortcuts import get_object_or_404
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api import TenantViewMixin
from apps.core.viewsets import TenantModelViewSet
from apps.tenants.models import Branch

from .models import BranchMenuItem, MenuCategory, MenuItem
from .serializers import AvailabilitySerializer, BranchMenuItemSerializer, MenuCategorySerializer, MenuItemSerializer
from .services import get_branch_menu


class MenuCategoryViewSet(TenantModelViewSet):
    resource = "menu"
    serializer_class = MenuCategorySerializer
    queryset = MenuCategory.all_objects.all()
    branch_attr = None
    search_fields = ["name"]


class MenuItemViewSet(TenantModelViewSet):
    resource = "menu"
    serializer_class = MenuItemSerializer
    queryset = MenuItem.all_objects.select_related("category")
    branch_attr = None
    filterset_fields = ["category", "is_active", "sku"]
    search_fields = ["name", "sku"]
    ordering_fields = ["name", "base_price_minor"]


class BranchMenuItemViewSet(TenantModelViewSet):
    resource = "branch_menu"
    serializer_class = BranchMenuItemSerializer
    queryset = BranchMenuItem.all_objects.select_related("menu_item")
    filterset_fields = ["branch", "menu_item", "is_available"]

    @action(detail=False, methods=["post"], required_action="toggle_availability")
    def availability(self, request):
        """Staff toggle an item on/off at their branch (creates the override row if needed)."""
        s = AvailabilitySerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        branch, item = s.validated_data["branch"], s.validated_data["menu_item"]
        self.check_branch_permission(branch, "toggle_availability")
        row, _ = BranchMenuItem.objects.get_or_create(
            branch=branch, menu_item=item, defaults={"organization": request.organization})
        row.is_available = s.validated_data["is_available"]
        row.save(update_fields=["is_available", "updated_at"])
        return Response(BranchMenuItemSerializer(row, context={"request": request}).data)


class BranchMenuView(TenantViewMixin, APIView):
    """Resolved menu for one branch (master menu + overrides), cached in Redis."""

    resource = "menu"
    branch_attr = None

    def get(self, request, branch_id):
        branch = get_object_or_404(Branch.objects.all(), pk=branch_id)  # scoped: 404 outside your branches
        return Response({"branch": str(branch.id), "currency": request.organization.currency,
                         "categories": get_branch_menu(request.organization.id, branch.id)})
