from django.test import Client

from tests.conftest import call


def test_health_endpoints(db):
    c = Client()
    assert c.get("/healthz").json() == {"status": "ok"}
    r = c.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"


def test_openapi_schema_builds(db):
    r = Client().get("/api/schema/")
    assert r.status_code == 200
    assert b"/api/v1/orders/" in r.content


def test_request_id_is_echoed(db):
    r = Client().get("/healthz", HTTP_X_REQUEST_ID="abc-123")
    assert r["X-Request-ID"] == "abc-123"


def test_scope_does_not_leak_between_requests(api, world):
    from apps.core import tenancy

    c = api.login(world.a_org_mgr, world.a)
    call(c, "get", "/api/v1/orders/")
    assert tenancy.get_scope() is None


def test_misconfigured_class_level_queryset_is_detected():
    import pytest
    from django.core.exceptions import ImproperlyConfigured
    from rest_framework import viewsets
    from rest_framework.test import APIRequestFactory

    from apps.core.api import TenantViewMixin
    from apps.orders.models import Order

    class Bad(TenantViewMixin, viewsets.ReadOnlyModelViewSet):
        queryset = Order.objects.all()  # evaluated at import: permanently empty

    view = Bad()
    view.request = APIRequestFactory().get("/")
    view.request.access = None
    view.format_kwarg = None
    with pytest.raises(ImproperlyConfigured):
        view.get_queryset()
