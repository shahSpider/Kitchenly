"""PostgreSQL row-level security helpers.

Two session settings drive the policies:
  app.current_org  - the organization the current request/job acts for
  app.bypass_rls   - 'on' for audited cross-tenant access (support, system jobs)

They are set with is_local=true so they live and die with the transaction. That
is why every request runs in ATOMIC_REQUESTS, and why this works behind
PgBouncer transaction pooling.
"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager

from django.conf import settings
from django.db import connections, transaction
from django.db.migrations.operations import RunSQL

from . import tenancy

GUC_ORG = "app.current_org"
GUC_BYPASS = "app.bypass_rls"


def _aliases(using: Iterable[str] | None = None):
    return list(using) if using else ["default"]


def apply_scope_to_db(scope: tenancy.TenantScope | None, using: Iterable[str] | None = None) -> None:
    org = str(scope.organization_id) if scope and scope.organization_id else ""
    bypass = "on" if scope and scope.bypass else "off"
    for alias in _aliases(using):
        with connections[alias].cursor() as cur:
            cur.execute(
                "SELECT set_config(%s, %s, true), set_config(%s, %s, true)",
                [GUC_ORG, org, GUC_BYPASS, bypass],
            )


def activate(scope: tenancy.TenantScope):
    """Set the scope for app + DB. Returns a token for `deactivate`."""
    token = tenancy.set_scope(scope)
    apply_scope_to_db(scope)
    return token


def deactivate(token) -> None:
    tenancy.reset_scope(token)
    try:
        apply_scope_to_db(tenancy.get_scope())
    except Exception:  # connection already broken/closed; the txn end resets anyway
        pass


@contextmanager
def scope_context(scope: tenancy.TenantScope):
    with transaction.atomic():
        token = activate(scope)
        try:
            yield
        finally:
            deactivate(token)


def tenant_context(organization_id, branch_ids=None):
    """Run a block acting for one organization (and optionally some branches)."""
    return scope_context(
        tenancy.TenantScope(
            organization_id=organization_id,
            branch_ids=frozenset(branch_ids) if branch_ids is not None else None,
        )
    )


def bypass():
    """Cross-tenant access. Use ONLY for system work (auth lookup, workers that
    resolve a connection from an id, support access) and never with user input
    deciding which tenant data is touched."""
    return scope_context(tenancy.TenantScope(bypass=True))


@contextmanager
def reporting_session(scope: tenancy.TenantScope | None = None):
    """Yield the DB alias heavy reports should read from (read replica when
    configured) with the RLS context applied on that connection too."""
    alias = settings.REPORTS_DB_ALIAS
    if alias == "default":
        yield alias
        return
    with transaction.atomic(using=alias):
        apply_scope_to_db(scope or tenancy.get_scope(), using=[alias])
        yield alias


def enable_rls(table: str, column: str = "organization_id", allow_null_on_write: bool = False) -> RunSQL:
    """Migration operation: enable + force RLS with the tenant-isolation policy.

    FORCE makes the policy apply to the table owner too (the app role)."""
    match = (
        f"current_setting('{GUC_BYPASS}', true) = 'on' OR "
        f"\"{column}\" = NULLIF(current_setting('{GUC_ORG}', true), '')::uuid"
    )
    check = match + (f' OR "{column}" IS NULL' if allow_null_on_write else "")
    forward = f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;
        CREATE POLICY org_isolation ON "{table}" USING ({match}) WITH CHECK ({check});
    """
    reverse = f"""
        DROP POLICY IF EXISTS org_isolation ON "{table}";
        ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
    """
    return RunSQL(forward, reverse)
