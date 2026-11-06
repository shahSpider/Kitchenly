from django.db import migrations

from apps.core.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("tenants", "0001_initial")]
    operations = [
        enable_rls("tenants_organization", "id"),
        enable_rls("tenants_branch"),
    ]
