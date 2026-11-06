from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models

from apps.core.fields import EncryptedTextField
from apps.core.models import TenantModel, TimestampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password) if password else user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_platform_admin", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, TimestampedModel):
    """Global identity. Not tenant-scoped: access comes from memberships."""

    email = models.EmailField(max_length=254, unique=True)  # always stored lower-case
    full_name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    is_platform_admin = models.BooleanField(default=False)
    totp_secret = EncryptedTextField(blank=True, default="")
    totp_enabled = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    # Django admin integration: only platform admins may use it.
    @property
    def is_staff(self):
        return self.is_platform_admin

    @property
    def is_superuser(self):
        return self.is_platform_admin

    def has_perm(self, perm, obj=None):
        return self.is_active and self.is_platform_admin

    def has_module_perms(self, app_label):
        return self.is_active and self.is_platform_admin

    def __str__(self):
        return self.email


class Role(TimestampedModel):
    """The same three rows for everyone (super admin is a user flag)."""

    class Code(models.TextChoices):
        ORG_MANAGER = "org_manager"
        BRANCH_MANAGER = "branch_manager"
        BRANCH_STAFF = "branch_staff"

    code = models.CharField(max_length=40, unique=True, choices=Code.choices)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.code


class RolePermission(models.Model):
    id = models.BigAutoField(primary_key=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    resource = models.CharField(max_length=50)
    action = models.CharField(max_length=50)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "resource", "action"], name="uniq_role_perm")]


class Membership(TenantModel):
    """Where a person may act, and as which role. branch NULL = whole organization."""

    tenant_branch_lookup = "branch_id"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    branch = models.ForeignKey("tenants.Branch", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="memberships")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="memberships")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization", "branch", "role"], name="uniq_membership"),
            models.UniqueConstraint(fields=["user", "organization", "role"], condition=models.Q(branch__isnull=True),
                                    name="uniq_membership_org_wide"),
        ]
        indexes = [models.Index(fields=["user"], name="membership_user_idx"),
                   models.Index(fields=["organization", "branch"], name="membership_org_branch_idx")]
