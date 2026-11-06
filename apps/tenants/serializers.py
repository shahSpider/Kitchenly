from django.db import transaction
from rest_framework import serializers

from apps.accounts.models import Membership, Role
from apps.core.serializers import TenantSerializer

from .models import Branch, Organization

PLATFORM_ONLY = ["slug", "status", "plan", "max_branches"]


class OrganizationSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(write_only=True, required=False)
    owner_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "status", "currency", "timezone", "plan", "max_branches", "tax_rates",
                  "created_at", "owner_email", "owner_name"]
        read_only_fields = ["id", "created_at"]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        access = getattr(request, "access", None)
        if access is not None and not access.is_platform_admin:
            for name in PLATFORM_ONLY + ["owner_email", "owner_name"]:
                fields[name].read_only = True
        return fields

    def validate_currency(self, value):
        value = value.upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError("Use a 3-letter ISO 4217 code.")
        return value

    def validate_tax_rates(self, value):
        if not isinstance(value, dict) or not all(isinstance(v, int) and 0 <= v <= 10000 for v in value.values()):
            raise serializers.ValidationError("Map tax codes to basis points (0-10000).")
        return value

    @transaction.atomic
    def create(self, validated):
        from apps.accounts.invitations import send_invite
        from apps.accounts.models import User

        email = validated.pop("owner_email", None)
        name = validated.pop("owner_name", "")
        org = Organization.objects.create(**validated)
        if email:
            email = email.strip().lower()
            user = User.objects.filter(email=email).first() or User.objects.create_user(email, None, full_name=name)
            Membership.objects.create(user=user, organization=org, branch=None,
                                      role=Role.objects.get(code="org_manager"))
            if not user.has_usable_password():
                transaction.on_commit(lambda: send_invite(user, org))
        return org


class BranchSerializer(TenantSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name", "code", "address", "timezone", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        org = self.organization
        code = attrs.get("code", getattr(self.instance, "code", None))
        qs = Branch.objects.filter(organization=org, code=code)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({"code": "A branch with this code already exists."})
        if self.instance is None and Branch.objects.filter(organization=org).count() >= org.max_branches:
            raise serializers.ValidationError("Branch limit for this plan reached.")
        return attrs
