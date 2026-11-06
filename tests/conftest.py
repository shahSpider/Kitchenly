import pytest
from rest_framework.test import APIClient

from tests import factories as f


class Api:
    """APIClient that logs in through the real login endpoint."""

    def __init__(self):
        self.client = APIClient()

    def login(self, user, org=None, otp=None):
        c = APIClient()
        body = {"email": user.email, "password": f.PASSWORD}
        if otp:
            body["otp"] = otp
        r = c.post("/api/v1/auth/login/", body)
        assert r.status_code == 200, r.content
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        c.default_org = org
        c._tokens = r.data
        return c


def call(client, method, url, data=None, org="__default__", **extra):
    org = client.default_org if org == "__default__" else org
    if org is not None:
        extra["HTTP_X_ORGANIZATION"] = str(getattr(org, "id", org))
    return getattr(client, method)(url, data, **extra) if data is not None else getattr(client, method)(url, **extra)


@pytest.fixture(autouse=True)
def _clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield


@pytest.fixture
def api():
    return Api()


@pytest.fixture
def world(db):
    """Two organizations with two branches each and one user per role."""
    a, b = f.org("alpha"), f.org("bravo")
    w = type("World", (), {})()
    w.a, w.b = a, b
    w.a1, w.a2 = f.branch(a, "a1"), f.branch(a, "a2")
    w.b1, w.b2 = f.branch(b, "b1"), f.branch(b, "b2")
    w.admin = f.user("root@example.com", platform_admin=True)
    w.a_org_mgr = f.person(a, "org_manager", None, "a.org@example.com")
    w.a_br_mgr = f.person(a, "branch_manager", w.a1, "a.bm@example.com")
    w.a_staff = f.person(a, "branch_staff", w.a1, "a.staff@example.com")
    w.a2_staff = f.person(a, "branch_staff", w.a2, "a2.staff@example.com")
    w.b_org_mgr = f.person(b, "org_manager", None, "b.org@example.com")
    w.b_staff = f.person(b, "branch_staff", w.b1, "b.staff@example.com")
    return w
