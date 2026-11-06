from django.db import migrations

from apps.core.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("menu", "0001_initial")]
    operations = [
        enable_rls("menu_menucategory"),
        enable_rls("menu_menuitem"),
        enable_rls("menu_branchmenuitem"),
    ]
