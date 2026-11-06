from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core import rls
from apps.core.api import TenantViewMixin
from apps.core.viewsets import TenantModelViewSet

from . import mfa
from .invitations import send_password_reset, user_from_uid_token
from .models import Membership, Role, User
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    MembershipSerializer,
    MeSerializer,
    PasswordResetSerializer,
    RoleSerializer,
    UserSerializer,
)


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    throttle_scope = "login"
    throttle_classes = [ScopedRateThrottle]


class RefreshView(TokenRefreshView):
    throttle_scope = "login"
    throttle_classes = [ScopedRateThrottle]


class LogoutView(APIView):
    def post(self, request):
        try:
            RefreshToken(request.data.get("refresh", "")).blacklist()
        except TokenError:
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(TenantViewMixin, generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    requires_organization = False
    mfa_exempt = True

    def get_object(self):
        return self.request.user


class MfaSetupView(TenantViewMixin, APIView):
    requires_organization = False
    mfa_exempt = True

    def post(self, request):
        user = request.user
        if user.totp_enabled:
            raise ValidationError("MFA is already enabled.")
        user.totp_secret = mfa.new_secret()
        user.save(update_fields=["totp_secret", "updated_at"])
        return Response({"secret": user.totp_secret, "otpauth_uri": mfa.provisioning_uri(user, user.totp_secret)})


class MfaConfirmView(TenantViewMixin, APIView):
    requires_organization = False
    mfa_exempt = True

    def post(self, request):
        user = request.user
        if not user.totp_secret or user.totp_enabled:
            raise ValidationError("Start MFA setup first.")
        if not mfa.verify(user.totp_secret, request.data.get("otp", "")):
            raise ValidationError({"otp": "Invalid code."})
        user.totp_enabled = True
        user.save(update_fields=["totp_enabled", "updated_at"])
        return Response({"enabled": True, "detail": "Sign in again with your one-time code."})


class PasswordForgotView(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_scope = "login"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
        user = User.objects.filter(email=str(request.data.get("email", "")).strip().lower(), is_active=True).first()
        if user:
            send_password_reset(user)
        return Response({"detail": "If the account exists, an email was sent."})  # no account enumeration


class PasswordResetView(APIView):
    """Also completes invitations (new users have no password yet)."""

    authentication_classes = []
    permission_classes = []
    throttle_scope = "login"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
        s = PasswordResetSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = user_from_uid_token(s.validated_data["uid"], s.validated_data["token"])
        if user is None:
            raise ValidationError({"token": "Invalid or expired link."})
        try:
            validate_password(s.validated_data["password"], user)
        except DjangoValidationError as exc:
            raise ValidationError({"password": exc.messages})
        user.set_password(s.validated_data["password"])
        user.save(update_fields=["password", "updated_at"])
        return Response({"detail": "Password updated."})


class PasswordChangeView(TenantViewMixin, APIView):
    requires_organization = False
    mfa_exempt = True

    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        if not request.user.check_password(s.validated_data["old_password"]):
            raise ValidationError({"old_password": "Incorrect."})
        request.user.set_password(s.validated_data["new_password"])
        request.user.save(update_fields=["password", "updated_at"])
        return Response({"detail": "Password changed."})


class RoleViewSet(TenantViewMixin, generics.ListAPIView):
    resource = None  # the role catalogue is readable by any signed-in person
    requires_organization = False
    serializer_class = RoleSerializer
    pagination_class = None
    queryset = Role.objects.prefetch_related("permissions").order_by("code")


class UserViewSet(TenantModelViewSet):
    resource = "user"
    serializer_class = UserSerializer
    queryset = User.objects.order_by("email")
    branch_attr = None
    filterset_fields = ["is_active"]
    search_fields = ["email", "full_name"]

    def get_required_action(self):
        action = super().get_required_action()
        if action == "read" and not self.request.access.allows("user", "read") and \
                self.request.access.allows("user", "read_self"):
            return "read_self"
        return action

    def get_queryset(self):
        ctx = self.request.access
        base = User.objects.order_by("email")
        if ctx.is_platform_admin and ctx.organization is None:
            return base
        if ctx.allows("user", "read"):
            members = Membership.objects.all()
            branches = ctx.branches_for("user", "read")
            if branches is not None:
                members = members.filter(branch_id__in=branches)
            return base.filter(id__in=members.values("user_id"))
        return base.filter(id=self.request.user.id)

    def perform_create(self, serializer):
        if not self.request.access.is_platform_admin:
            raise PermissionDenied("Invite people through /memberships/.")
        serializer.save()

    def perform_update(self, serializer):
        ctx = self.request.access
        user = serializer.instance
        if not ctx.is_platform_admin:
            with rls.bypass():  # does this person belong anywhere else?
                elsewhere = Membership.all_objects.filter(user=user).exclude(organization=ctx.organization).exists()
            if elsewhere:
                raise PermissionDenied("This person belongs to other organizations; they edit their own profile.")
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, user):
        """Removes the person from the caller's scope; the global identity stays."""
        ctx = self.request.access
        if user.pk == self.request.user.pk:
            raise PermissionDenied("You cannot remove yourself.")
        if ctx.is_platform_admin and ctx.organization is None:
            user.is_active = False
            user.save(update_fields=["is_active", "updated_at"])
            return
        members = Membership.objects.filter(user=user)
        branches = ctx.branches_for("user", "delete")
        if branches is not None:
            members = members.filter(branch_id__in=branches)
        for m in members.filter(role__code__in=ctx.assignable_role_codes()):
            m.delete()


class MembershipViewSet(TenantModelViewSet):
    resource = "membership"
    serializer_class = MembershipSerializer
    queryset = Membership.all_objects.select_related("user", "role").order_by("-created_at")
    filterset_fields = ["branch", "role__code", "is_active", "user"]

    def get_object_branch_id(self, obj):
        return obj.branch_id

    def perform_create(self, serializer):
        serializer.save()  # organization comes from the request inside the serializer

    def check_object_permissions(self, request, obj):
        super().check_object_permissions(request, obj)
        # Cannot touch memberships whose role you may not grant.
        if obj.role.code not in request.access.assignable_role_codes():
            raise PermissionDenied("You cannot manage this membership.")
