import re

import pyotp
from django.core import mail
from rest_framework.test import APIClient

from tests import factories as f
from tests.conftest import call


def test_login_refresh_logout(api, world):
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": "A.Staff@example.com", "password": f.PASSWORD})
    assert r.status_code == 200 and {"access", "refresh"} <= set(r.data)
    assert c.post("/api/v1/auth/login/", {"email": "a.staff@example.com", "password": "nope"}).status_code == 401
    new = c.post("/api/v1/auth/refresh/", {"refresh": r.data["refresh"]})
    assert new.status_code == 200
    # refresh tokens rotate: the old one is dead
    assert c.post("/api/v1/auth/refresh/", {"refresh": r.data["refresh"]}).status_code == 401
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {new.data['access']}")
    assert c.post("/api/v1/auth/logout/", {"refresh": new.data["refresh"]}).status_code == 204
    assert c.post("/api/v1/auth/refresh/", {"refresh": new.data["refresh"]}).status_code == 401


def test_inactive_user_cannot_login(world):
    from apps.core import rls

    with rls.bypass():
        world.a_staff.is_active = False
        world.a_staff.save()
    assert APIClient().post("/api/v1/auth/login/", {"email": world.a_staff.email, "password": f.PASSWORD}).status_code == 401


def test_me_lists_memberships(api, world):
    c = api.login(world.a_br_mgr, None)
    r = call(c, "get", "/api/v1/auth/me/", org=None)
    assert r.data["email"] == world.a_br_mgr.email
    assert [(m["role"], m["branch"]) for m in r.data["memberships"]] == [("branch_manager", world.a1.pk)] or \
           [(m["role"], str(m["branch"])) for m in r.data["memberships"]] == [("branch_manager", str(world.a1.pk))]


def test_mfa_required_for_org_managers_and_super_admins(api, world, settings):
    settings.MFA_ENFORCED = True
    for person, org in ((world.a_org_mgr, world.a), (world.admin, world.a)):
        c = api.login(person, org)
        assert c._tokens["mfa_enrollment_required"] is True
        r = call(c, "get", "/api/v1/orders/")
        assert r.status_code == 403 and "Multi-factor" in r.data["detail"]       # blocked until enrolled
        assert call(c, "get", "/api/v1/auth/me/", org=None).status_code == 200    # but can enroll
        secret = call(c, "post", "/api/v1/auth/mfa/setup/", org=None).data["secret"]
        assert call(c, "post", "/api/v1/auth/mfa/confirm/", {"otp": "000000"}, org=None).status_code == 400
        assert call(c, "post", "/api/v1/auth/mfa/confirm/", {"otp": pyotp.TOTP(secret).now()}, org=None).status_code == 200

        body = {"email": person.email, "password": f.PASSWORD}
        assert APIClient().post("/api/v1/auth/login/", body).status_code == 400                   # OTP now mandatory
        assert APIClient().post("/api/v1/auth/login/", {**body, "otp": "123456"}).status_code == 400
        c2 = api.login(person, org, otp=pyotp.TOTP(secret).now())
        assert call(c2, "get", "/api/v1/orders/").status_code == 200


def test_staff_do_not_need_mfa(api, world, settings):
    settings.MFA_ENFORCED = True
    c = api.login(world.a_staff, world.a)
    assert c._tokens["mfa_enrollment_required"] is False
    assert call(c, "get", "/api/v1/orders/").status_code == 200


def test_invite_then_set_password_then_login(api, world, django_capture_on_commit_callbacks):
    c = api.login(world.a_org_mgr, world.a)
    with django_capture_on_commit_callbacks(execute=True):
        r = call(c, "post", "/api/v1/memberships/", {"email": "Cook@Example.com", "full_name": "Cook",
                                                   "role": "branch_staff", "branch": str(world.a1.pk)})
    assert r.status_code == 201 and r.data["user_email"] == "cook@example.com"
    assert len(mail.outbox) == 1
    uid, token = re.search(r"uid=([^&]+)&token=(\S+)", mail.outbox[0].body).groups()
    anon = APIClient()
    assert anon.post("/api/v1/auth/password/reset/", {"uid": uid, "token": token, "password": "123"}).status_code == 400
    assert anon.post("/api/v1/auth/password/reset/", {"uid": uid, "token": "bad", "password": "Gr3at-new-pass!"}).status_code == 400
    assert anon.post("/api/v1/auth/password/reset/", {"uid": uid, "token": token, "password": "Gr3at-new-pass!"}).status_code == 200
    assert anon.post("/api/v1/auth/password/reset/", {"uid": uid, "token": token, "password": "An0ther-pass!!"}).status_code == 400  # single use
    login = anon.post("/api/v1/auth/login/", {"email": "cook@example.com", "password": "Gr3at-new-pass!"})
    assert login.status_code == 200
    # inviting the same person again does not send a second email nor duplicate
    with django_capture_on_commit_callbacks(execute=True):
        again = call(c, "post", "/api/v1/memberships/", {"email": "cook@example.com", "role": "branch_staff", "branch": str(world.a1.pk)})
    assert again.status_code == 201 and again.data["id"] == r.data["id"] and len(mail.outbox) == 1


def test_forgot_password_does_not_reveal_accounts(world):
    c = APIClient()
    a = c.post("/api/v1/auth/password/forgot/", {"email": world.a_staff.email})
    b = c.post("/api/v1/auth/password/forgot/", {"email": "ghost@example.com"})
    assert a.status_code == b.status_code == 200 and a.data == b.data
    assert len(mail.outbox) == 1


def test_password_change(api, world):
    c = api.login(world.a_staff, world.a)
    assert call(c, "post", "/api/v1/auth/password/change/", {"old_password": "bad", "new_password": "Gr3at-new-pass!"}).status_code == 400
    assert call(c, "post", "/api/v1/auth/password/change/", {"old_password": f.PASSWORD, "new_password": "short"}).status_code == 400
    assert call(c, "post", "/api/v1/auth/password/change/", {"old_password": f.PASSWORD, "new_password": "Gr3at-new-pass!"}).status_code == 200


def test_removing_a_user_only_removes_their_access_here(api, world):
    from apps.accounts.models import Membership
    from apps.core import rls

    p = f.person(world.a, "branch_staff", world.a1)
    f.grant(p, world.b, "branch_staff", world.b1)
    c = api.login(world.a_org_mgr, world.a)
    assert call(c, "delete", f"/api/v1/users/{p.pk}/").status_code == 204
    with rls.bypass():
        assert not Membership.all_objects.filter(user=p, organization=world.a).exists()
        assert Membership.all_objects.filter(user=p, organization=world.b).exists()   # other org untouched
    assert call(c, "delete", f"/api/v1/users/{world.a_org_mgr.pk}/").status_code == 403  # not yourself


def test_org_manager_cannot_edit_a_person_who_belongs_elsewhere(api, world):
    p = f.person(world.a, "branch_staff", world.a1)
    f.grant(p, world.b, "branch_staff", world.b1)
    c = api.login(world.a_org_mgr, world.a)
    assert call(c, "patch", f"/api/v1/users/{p.pk}/", {"full_name": "Hacked"}).status_code == 403
    q = f.person(world.a, "branch_staff", world.a1)
    assert call(c, "patch", f"/api/v1/users/{q.pk}/", {"full_name": "Renamed"}).status_code == 200
