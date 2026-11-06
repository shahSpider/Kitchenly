from django.db import migrations


def forwards(apps, schema_editor):
    Provider = apps.get_model("pos", "PosProvider")
    Provider.objects.get_or_create(
        code="generic",
        defaults=dict(name="Generic REST/CSV POS", supports_webhook=True, supports_pull=True,
                      supports_file=True, supports_push=True),
    )


class Migration(migrations.Migration):
    dependencies = [("pos", "0002_rls")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
