from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.tenants.models import Branch

from . import mfa
from .models import Membership, Role, User


class LoginSerializer(TokenObtainPairSerializer):
    username_field = "email"
    otp = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        otp = attrs.pop("otp", "")
        attrs["email"] = (attrs.get("email") or "").strip().lower()
        data = super().validate(attrs)  # checks credentials, sets self.user
        user = self.user
        required = mfa.user_requires_mfa(user)
        if user.totp_enabled:
            if not mfa.verify(user.totp_secret, otp):
                raise serializers.ValidationError({"otp": "A valid one-time code is required."})
            satisfied = True
        else:
            satisfied = not required
        refresh = self.get_token(user)
        refresh["mfa"] = satisfied
        data["refresh"] = str(refresh)
        data["access"] = str(refresh.access_token)
        data["mfa_enrollment_required"] = required and not user.totp_enabled
        return data


class MembershipReadSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="role.code")
    organization_slug = serializers.CharField(source="organization.slug")

    class Meta:
        model = Membership
        fields = ["id", "organization", "organization_slug", "branch", "role", "is_active"]


class MeSerializer(serializers.ModelSerializer):
    memberships = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "is_platform_admin", "totp_enabled", "memberships"]
        read_only_fields = ["id", "email", "is_platform_admin", "totp_enabled"]

    def get_memberships(self, user):
        ms = getattr(self.context["request"], "all_memberships", [])
        return MembershipReadSerializer(ms, many=True).data


class PasswordResetSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value, self.context["request"].user)
        return value


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["code", "name", "permissions"]

    def get_permissions(self, role):
        out = {}
        for p in role.permissions.all():
            out.setdefault(p.resource, []).append(p.action)
        return {k: sorted(v) for k, v in out.items()}


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    memberships = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "is_active", "is_platform_admin", "password", "memberships"]
        read_only_fields = ["id", "is_platform_admin", "is_active"]

    def get_memberships(self, user):
        request = self.context["request"]
        qs = Membership.objects.filter(user=user, is_active=True).select_related("role", "organization")
        branches = request.access.branches_for("membership", "read")
        if branches is not None:
            qs = qs.filter(branch_id__in=branches)
        return MembershipReadSerializer(qs, many=True).data

    def create(self, validated):
        pw = validated.pop("password", None)
        if pw:
            validate_password(pw)
        return User.objects.create_user(validated.pop("email"), pw, **validated)


class MembershipSerializer(serializers.ModelSerializer):
    """Create = invite: provide `email` (+ `full_name` for a new person)."""

    email = serializers.EmailField(write_only=True, required=False)
    full_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    role = serializers.SlugRelatedField(slug_field="code", queryset=Role.objects.all())
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects, required=False, allow_null=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "user_email", "email", "full_name", "branch", "role", "is_active", "created_at"]
        read_only_fields = ["id", "user", "created_at"]

    def validate(self, attrs):
        request = self.context["request"]
        ctx = request.access
        role = attrs.get("role", getattr(self.instance, "role", None))
        branch = attrs.get("branch", getattr(self.instance, "branch", None))
        if self.instance is None and not attrs.get("email"):
            raise serializers.ValidationError({"email": "Required."})
        if role.code not in ctx.assignable_role_codes():
            raise serializers.ValidationError({"role": "You cannot assign this role."})
        if role.code == "org_manager" and branch is not None:
            raise serializers.ValidationError({"branch": "Organization managers cover the whole organization."})
        if role.code != "org_manager" and branch is None:
            raise serializers.ValidationError({"branch": "Required for this role."})
        if branch is not None:
            allowed = ctx.branches_for("membership", "create" if self.instance is None else "update")
            if allowed is not None and branch.id not in allowed:
                raise serializers.ValidationError({"branch": "You cannot manage this branch."})
        return attrs

    @transaction.atomic
    def create(self, validated):
        from .invitations import send_invite

        email = validated.pop("email").strip().lower()
        full_name = validated.pop("full_name", "")
        user = User.objects.filter(email=email).first()
        is_new = user is None
        if is_new:
            user = User.objects.create_user(email, None, full_name=full_name)
        org = self.context["request"].organization
        membership, created = Membership.objects.get_or_create(
            user=user, organization=org, branch=validated.get("branch"), role=validated["role"],
            defaults={"is_active": validated.get("is_active", True)},
        )
        if not created and not membership.is_active:
            membership.is_active = True
            membership.save(update_fields=["is_active", "updated_at"])
        if not user.has_usable_password():
            transaction.on_commit(lambda: send_invite(user, org))
        return membership
