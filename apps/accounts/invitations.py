from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .models import User


def make_token(user):
    return urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)


def send_invite(user, organization):
    uid, token = make_token(user)
    link = f"{settings.FRONTEND_URL}/set-password?uid={uid}&token={token}"
    send_mail(
        f"You've been invited to {organization.name} on Kitchenly",
        f"Hello {user.full_name or user.email},\n\nSet your password to get started:\n{link}\n",
        settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True,
    )


def send_password_reset(user):
    uid, token = make_token(user)
    link = f"{settings.FRONTEND_URL}/set-password?uid={uid}&token={token}"
    send_mail("Reset your Kitchenly password", f"Reset your password:\n{link}\n",
              settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)


def user_from_uid_token(uid, token):
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uid).decode())
    except (User.DoesNotExist, ValueError, TypeError, UnicodeDecodeError):
        return None
    return user if default_token_generator.check_token(user, token) else None
