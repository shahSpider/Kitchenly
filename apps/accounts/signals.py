from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .access import invalidate_permission_cache
from .models import RolePermission


@receiver([post_save, post_delete], sender=RolePermission)
def _clear(sender, **kwargs):
    invalidate_permission_cache()
