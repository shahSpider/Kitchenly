from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Liveness: the process is up."""
    return JsonResponse({"status": "ok"})


def readyz(request):
    """Readiness: dependencies reachable."""
    checks = {}
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        cache.set("readyz", "1", 5)
        checks["cache"] = "ok" if cache.get("readyz") == "1" else "degraded"
    except Exception:
        checks["cache"] = "degraded"
    ok = checks["database"] == "ok"  # cache outage degrades but does not fail readiness
    return JsonResponse({"status": "ok" if ok else "error", "checks": checks}, status=200 if ok else 503)
