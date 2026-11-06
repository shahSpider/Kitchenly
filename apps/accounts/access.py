"""Authorization: role (what) x membership (where). A request is allowed only
when both agree. See the roles table in the design document."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

CRUD = {"create", "read", "update", "delete"}

# Super admin is a flag on the user (not a membership). Full control of platform
# objects, read-only "support" access to everything else (every use is audit-logged).
PLATFORM_ADMIN_MATRIX = {
    "organization": CRUD, "branch": CRUD, "user": CRUD, "membership": CRUD,
    "pos_connection": CRUD | {"update_status"}, "pos_provider": CRUD, "role": {"read"},
    "menu": {"read"}, "branch_menu": {"read"}, "order": {"read"}, "refund": {"read"},
    "report": {"read"}, "sync_event": {"read", "update"}, "audit_log": {"read"},
}

_CACHE = {"ts": 0.0, "data": {}}
_TTL = 30.0


def invalidate_permission_cache():
    _CACHE["ts"] = 0.0


def role_permissions() -> dict:
    """role code -> {(resource, action)}; cached briefly (global, tiny table)."""
    if time.monotonic() - _CACHE["ts"] > _TTL:
        from .models import RolePermission

        data: dict = {}
        for rp in RolePermission.objects.select_related("role"):
            data.setdefault(rp.role.code, set()).add((rp.resource, rp.action))
        _CACHE.update(ts=time.monotonic(), data=data)
    return _CACHE["data"]


@dataclass
class AccessContext:
    user: object
    organization: object | None
    memberships: list = field(default_factory=list)  # active memberships in `organization`

    @property
    def is_platform_admin(self) -> bool:
        return bool(getattr(self.user, "is_platform_admin", False))

    @property
    def role_codes(self) -> set:
        return {m.role.code for m in self.memberships}

    def _grants(self, membership, resource, action) -> bool:
        return (resource, action) in role_permissions().get(membership.role.code, ())

    def allows(self, resource: str, action: str, branch_id=None) -> bool:
        if self.is_platform_admin:
            return action in PLATFORM_ADMIN_MATRIX.get(resource, ())
        for m in self.memberships:
            if not self._grants(m, resource, action):
                continue
            if branch_id is None or m.branch_id is None or m.branch_id == branch_id:
                return True
        return False

    def branches_for(self, resource: str, action: str):
        """None = all branches; otherwise the (possibly empty) set of branch ids."""
        if self.is_platform_admin:
            return None if action in PLATFORM_ADMIN_MATRIX.get(resource, ()) else frozenset()
        ids = set()
        for m in self.memberships:
            if self._grants(m, resource, action):
                if m.branch_id is None:
                    return None
                ids.add(m.branch_id)
        return frozenset(ids)

    def assignable_role_codes(self) -> set:
        """Nobody can grant more than they hold: branch managers only add staff."""
        if self.is_platform_admin or "org_manager" in self.role_codes:
            return {"org_manager", "branch_manager", "branch_staff"}
        if "branch_manager" in self.role_codes:
            return {"branch_staff"}
        return set()
