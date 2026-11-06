from rest_framework import serializers

from apps.core.serializers import TenantSerializer
from apps.tenants.models import Branch

from .models import BranchMenuItem, MenuCategory, MenuItem


class MenuCategorySerializer(TenantSerializer):
    class Meta:
        model = MenuCategory
        fields = ["id", "name", "sort_order"]
        read_only_fields = ["id"]


class MenuItemSerializer(TenantSerializer):
    category = serializers.PrimaryKeyRelatedField(queryset=MenuCategory.objects)

    class Meta:
        model = MenuItem
        fields = ["id", "category", "name", "description", "sku", "base_price_minor", "tax_code", "is_active"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        sku = attrs.get("sku", getattr(self.instance, "sku", None))
        qs = MenuItem.objects.filter(organization=self.organization, sku=sku)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({"sku": "This SKU already exists."})
        return attrs


class BranchMenuItemSerializer(TenantSerializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects)
    menu_item = serializers.PrimaryKeyRelatedField(queryset=MenuItem.objects)
    effective_price_minor = serializers.IntegerField(read_only=True)

    class Meta:
        model = BranchMenuItem
        fields = ["id", "branch", "menu_item", "price_minor", "is_available", "effective_price_minor"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if self.instance is None:
            if BranchMenuItem.objects.filter(branch=attrs["branch"], menu_item=attrs["menu_item"]).exists():
                raise serializers.ValidationError("This item already has a branch entry; update it instead.")
        elif ("branch" in attrs and attrs["branch"] != self.instance.branch) or \
                ("menu_item" in attrs and attrs["menu_item"] != self.instance.menu_item):
            raise serializers.ValidationError("Branch and item cannot be changed.")
        return attrs


class AvailabilitySerializer(serializers.Serializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects)
    menu_item = serializers.PrimaryKeyRelatedField(queryset=MenuItem.objects)
    is_available = serializers.BooleanField()
