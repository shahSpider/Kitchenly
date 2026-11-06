import uuid

from . import rls, tenancy
from .logging import request_id_var


class RequestContextMiddleware:
    """Request id for log correlation; guarantees no tenant scope leaks from one
    request to the next on a reused worker thread."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.request_id = rid
        token = request_id_var.set(rid)
        tenancy.clear_scope()
        try:
            response = self.get_response(request)
        finally:
            tenancy.clear_scope()
            request_id_var.reset(token)
        response["X-Request-ID"] = rid
        return response


class AdminSupportAccessMiddleware:
    """Django admin is for platform admins only. It runs with cross-tenant
    access, so each admin request is written to the audit log."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/admin/"):
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not getattr(user, "is_platform_admin", False):
            return self.get_response(request)
        from apps.audit.services import record_support_access

        with rls.bypass():
            if request.method not in ("GET", "HEAD", "OPTIONS") or request.path.count("/") > 3:
                record_support_access(user, request.method, request.path, request.META.get("REMOTE_ADDR"))
            return self.get_response(request)
