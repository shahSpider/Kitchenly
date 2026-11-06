from django.db import migrations

from apps.core.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]
    operations = [
        enable_rls("orders_order"),
        enable_rls("orders_orderitem"),
        enable_rls("orders_payment"),
        enable_rls("orders_refund"),
        enable_rls("orders_dayclose"),
    ]
