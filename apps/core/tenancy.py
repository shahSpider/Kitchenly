"""Request/task scoped tenant context.

Layer 1 of tenant isolation (app level) reads this context in `TenantManager`;
layer 2 (database level) is PostgreSQL row-level security, driven by the same
values via `set_config` (see rls.py). Both fail closed: with no scope active,
tenant tables return nothing.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantScope:
    organization_id: uuid.UUID | None = None
    # None = every branch of the organization.
    branch_ids: frozenset | None = None
    # Cross-tenant access: system jobs, platform admin without a chosen org.
    bypass: bool = False


_scope: ContextVar[TenantScope | None] = ContextVar("tenant_scope", default=None)


def get_scope() -> TenantScope | None:
    return _scope.get()


def set_scope(scope: TenantScope | None):
    return _scope.set(scope)


def reset_scope(token) -> None:
    _scope.reset(token)


def clear_scope() -> None:
    _scope.set(None)
