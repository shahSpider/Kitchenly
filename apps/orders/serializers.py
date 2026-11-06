from rest_framework import serializers

from apps.core.serializers import TenantSerializer
from apps.menu.models import MenuItem
from apps.tenants.models import Branch

from .models import Order, OrderItem, Payment, Refund


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "menu_item", "name_snapshot", "sku_snapshot", "quantity", "unit_price_minor",
                  "line_total_minor"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "method", "amount_minor", "status", "reference", "paid_at"]
        read_only_fields = ["id", "status", "paid_at"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = ["id", "branch", "pos_connection", "external_id", "order_number", "status", "order_type",
                  "subtotal_minor", "tax_minor", "discount_minor", "total_minor", "currency", "placed_at",
                  "closed_at", "notes", "items", "payments"]
        read_only_fields = fields


class NewItemSerializer(serializers.Serializer):
    menu_item = serializers.PrimaryKeyRelatedField(queryset=MenuItem.objects)
    quantity = serializers.IntegerField(min_value=1, max_value=1000)


class NewPaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    amount_minor = serializers.IntegerField(min_value=1)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=128)


class OrderCreateSerializer(serializers.Serializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects, required=False)
    order_type = serializers.ChoiceField(choices=Order.Type.choices, default=Order.Type.DINE_IN)
    items = NewItemSerializer(many=True, allow_empty=False)
    discount_minor = serializers.IntegerField(min_value=0, required=False, default=0)
    notes = serializers.CharField(required=False, allow_blank=True)
    payments = NewPaymentSerializer(many=True, required=False)


class OrderUpdateSerializer(serializers.Serializer):
    order_type = serializers.ChoiceField(choices=Order.Type.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    discount_minor = serializers.IntegerField(min_value=0, required=False)


class StatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[s for s in Order.Status.choices if s[0] != "voided"])


class RefundSerializer(TenantSerializer):
    order = serializers.PrimaryKeyRelatedField(queryset=Order.objects)
    payment = serializers.PrimaryKeyRelatedField(queryset=Payment.objects, required=False, allow_null=True)

    class Meta:
        model = Refund
        fields = ["id", "order", "payment", "kind", "amount_minor", "reason", "status", "requested_by",
                  "approved_by", "decided_at", "created_at"]
        read_only_fields = ["id", "status", "requested_by", "approved_by", "decided_at", "created_at"]

    def validate(self, attrs):
        kind = attrs.get("kind", Refund.Kind.REFUND)
        order, payment = attrs["order"], attrs.get("payment")
        if kind == Refund.Kind.REFUND:
            if payment is None or payment.order_id != order.id:
                raise serializers.ValidationError({"payment": "A payment of this order is required."})
            if not 0 < attrs.get("amount_minor", 0) <= payment.amount_minor:
                raise serializers.ValidationError({"amount_minor": "Must be between 1 and the payment amount."})
        else:
            attrs["payment"] = None
            attrs["amount_minor"] = order.total_minor
        return attrs
