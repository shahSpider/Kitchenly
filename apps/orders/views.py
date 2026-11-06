from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.core.api import TenantViewMixin
from apps.core.pagination import CreatedAtCursorPagination, PlacedAtCursorPagination

from . import services
from .models import Order, Refund
from .serializers import (
    NewPaymentSerializer,
    OrderCreateSerializer,
    OrderSerializer,
    OrderUpdateSerializer,
    PaymentSerializer,
    RefundSerializer,
    StatusSerializer,
)


class OrderViewSet(TenantViewMixin, viewsets.ModelViewSet):
    resource = "order"
    queryset = Order.all_objects.select_related("branch").prefetch_related("items", "payments")
    serializer_class = OrderSerializer
    pagination_class = PlacedAtCursorPagination
    filterset_fields = {"branch": ["exact"], "status": ["exact", "in"], "order_type": ["exact"],
                        "placed_at": ["gte", "lte"], "pos_connection": ["exact", "isnull"]}
    ordering_fields = ["placed_at", "total_minor"]
    search_fields = ["order_number", "external_id"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("mine"):  # "see their own shift"
            qs = qs.filter(created_by=self.request.user)
        return qs

    def get_serializer_class(self):
        return {"create": OrderCreateSerializer, "partial_update": OrderUpdateSerializer,
                "update": OrderUpdateSerializer}.get(self.action, OrderSerializer)

    def create(self, request, *args, **kwargs):
        s = OrderCreateSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        data = s.validated_data
        branch = data.get("branch")
        if branch is None:
            allowed = request.access.branches_for("order", "create")
            from apps.tenants.models import Branch

            candidates = list(Branch.objects.filter(id__in=allowed) if allowed is not None else Branch.objects.all()[:2])
            if len(candidates) != 1:
                raise ValidationError({"branch": "Required."})
            branch = candidates[0]
        self.check_branch_permission(branch, "create")
        if not branch.is_active:
            raise ValidationError({"branch": "This branch is inactive."})
        try:
            order = services.create_manual_order(
                organization=request.organization, branch=branch, user=request.user,
                order_type=data["order_type"], items=data["items"], discount_minor=data["discount_minor"],
                notes=data.get("notes", ""), payments=data.get("payments", []))
        except services.RuleViolation as exc:
            raise ValidationError({"detail": str(exc)})
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        if order.pos_connection_id:
            raise ValidationError({"detail": "This order is managed by the POS system."})
        s = OrderUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            for k, v in s.validated_data.items():
                setattr(order, k, v)
            if order.discount_minor > order.subtotal_minor:
                raise ValidationError({"discount_minor": "Exceeds the subtotal."})
            services.recompute_totals(order)
            order.save()
        return Response(OrderSerializer(order).data)

    def perform_destroy(self, order):
        if order.pos_connection_id or order.status != Order.Status.OPEN or order.payments.exists():
            raise ValidationError({"detail": "Only unpaid, open, manual orders can be deleted. Void it instead."})
        order.delete()

    @action(detail=True, methods=["post"], required_action="update_status")
    def status(self, request, pk=None):
        order = self.get_object()
        s = StatusSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            services.change_status(order, s.validated_data["status"])
        except services.RuleViolation as exc:
            raise ValidationError({"detail": str(exc)})
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"], required_action="update_status")
    def payments(self, request, pk=None):
        order = self.get_object()
        s = NewPaymentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            payment = services.add_payment(order, **s.validated_data)
        except services.RuleViolation as exc:
            raise ValidationError({"detail": str(exc)})
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class RefundViewSet(TenantViewMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin,
                    viewsets.GenericViewSet):
    resource = "refund"
    serializer_class = RefundSerializer
    queryset = Refund.all_objects.select_related("order")
    branch_attr = "order.branch_id"
    pagination_class = CreatedAtCursorPagination
    filterset_fields = ["status", "kind", "order"]

    def get_required_action(self):
        if self.action == "create":
            ctx = self.request.access
            return "request" if ctx.allows("refund", "request") else "approve"
        return super().get_required_action()

    def perform_create(self, serializer):
        order = serializer.validated_data["order"]
        if not self.request.access.allows("refund", self.get_required_action(), order.branch_id):
            raise PermissionDenied("You cannot request this at that branch.")
        serializer.save(organization=self.request.organization, requested_by=self.request.user)

    def _decide(self, request, approve):
        refund = self.get_object()
        try:
            refund = services.decide_refund(refund, request.user, approve)
        except services.RuleViolation as exc:
            raise ValidationError({"detail": str(exc)})
        return Response(self.get_serializer(refund).data)

    @action(detail=True, methods=["post"], required_action="approve")
    def approve(self, request, pk=None):
        return self._decide(request, True)

    @action(detail=True, methods=["post"], required_action="approve")
    def reject(self, request, pk=None):
        return self._decide(request, False)
