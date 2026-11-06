from django.db import migrations

from apps.core.rls import enable_rls

APPEND_ONLY = """
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_auditlog
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_auditlog
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
"""
REVERSE = """
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_auditlog;
DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_auditlog;
DROP FUNCTION IF EXISTS audit_log_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [
        # NULL organization = platform-level event (e.g. support access to the platform).
        enable_rls("audit_auditlog", allow_null_on_write=True),
        migrations.RunSQL(APPEND_ONLY, REVERSE),
    ]
