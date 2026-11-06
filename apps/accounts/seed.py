"""Role -> permission matrix from the design document (shared by the data
migration and the `sync_roles` management command)."""

CRUD = ["create", "read", "update", "delete"]

ROLES = {
    "org_manager": "Organization manager",
    "branch_manager": "Branch manager",
    "branch_staff": "Branch staff",
}

MATRIX = {
    "org_manager": {
        "organization": ["read", "update"],
        "branch": CRUD,
        "user": CRUD,
        "membership": CRUD,
        "menu": CRUD,
        "branch_menu": CRUD,
        "order": ["read"],
        "refund": ["read"],
        "report": ["read"],
        "pos_connection": CRUD,
        "sync_event": ["read", "update"],  # dead-letter review / retry
        "audit_log": ["read"],
        "pos_provider": ["read"],
        "role": ["read"],
    },
    "branch_manager": {
        "organization": ["read"],
        "branch": ["read", "update", "close_day"],
        "user": CRUD,  # staff in own branches (enforced by scope + assignable roles)
        "membership": CRUD,
        "menu": ["read"],
        "branch_menu": ["create", "read", "update", "toggle_availability"],
        "order": CRUD + ["update_status"],
        "refund": ["read", "approve"],
        "report": ["read"],
        "pos_connection": ["read"],
        "sync_event": ["read"],
        "pos_provider": ["read"],
        "role": ["read"],
    },
    "branch_staff": {
        "branch": ["read"],
        "user": ["read_self"],
        "menu": ["read"],
        "branch_menu": ["read", "toggle_availability"],
        "order": ["create", "read", "update_status"],
        "refund": ["request"],
    },
}


def sync(Role, RolePermission):
    for code, name in ROLES.items():
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        wanted = {(r, a) for r, actions in MATRIX[code].items() for a in actions}
        existing = {(p.resource, p.action): p for p in RolePermission.objects.filter(role=role)}
        for key in wanted - existing.keys():
            RolePermission.objects.create(role=role, resource=key[0], action=key[1])
        for key in existing.keys() - wanted:
            existing[key].delete()
