from django.db import migrations

from apps.accounts.seed import sync


def forwards(apps, schema_editor):
    sync(apps.get_model("accounts", "Role"), apps.get_model("accounts", "RolePermission"))


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_rls")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
