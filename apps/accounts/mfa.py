import pyotp
from django.conf import settings


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user, secret) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=settings.MFA_ISSUER)


def verify(secret: str, code: str) -> bool:
    return bool(secret and code) and pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)


def user_requires_mfa(user, memberships=None) -> bool:
    """Super admins and organization managers must use MFA."""
    if not settings.MFA_ENFORCED:
        return False
    if user.is_platform_admin:
        return True
    if memberships is None:
        from apps.core import rls

        from .models import Membership

        with rls.bypass():
            return Membership.all_objects.filter(user=user, is_active=True, role__code="org_manager").exists()
    return any(m.role.code == "org_manager" and m.is_active for m in memberships)
