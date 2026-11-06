from django.db import migrations

from apps.core.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("pos", "0001_initial")]
    operations = [
        enable_rls("pos_posconnection"),
        enable_rls("pos_syncevent"),
        enable_rls("pos_externalref"),
        enable_rls("pos_importjob"),
        enable_rls("pos_reconciliationreport"),
    ]
