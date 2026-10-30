"""Isolated end-to-end onboarding journey: no production records or real emails."""
import io
import re
from urllib.parse import urlparse

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import Client, User, Document, Alert, InviteLink
from app.models.user import Role
from tests.seed_demo import seed, EMAILS, PASSWORD

H = {"X-Forwarded-Proto": "https", "Host": "localhost"}


def csrf(client, path):
    html = client.get(path, headers=H).get_data(as_text=True)
    found = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert found, f"Missing CSRF input at {path}"
    return found.group(1)


def login(client, email, password):
    t = csrf(client, "/auth/login")
    return client.post("/auth/login", data={"email": email, "password": password,
                                            "csrf_token": t}, headers=H)


@pytest.fixture(scope="module")
def app():
    application = create_app()
    application.config.update(TESTING=True)
    from app.extensions import limiter
    limiter.enabled = False
    with application.app_context():
        seed()
    return application


def test_staff_portal_invite_login_reset_upload_and_notification(app, monkeypatch):
    """A staff-created account receives an invite; uploads are linked and notify staff."""
    sent = []
    def fake_send(to, subject, text, html, **kwargs):
        sent.append((to, subject, text))
        return True, "sent"

    monkeypatch.setattr("app.routes.clients.send_email_html", fake_send)
    monkeypatch.setattr("app.services.email_service.send_email_html", fake_send)
    director = app.test_client()
    assert login(director, EMAILS["director"], PASSWORD).status_code in (302, 303)

    t = csrf(director, "/clients/new")
    r = director.post("/clients/new", data={"csrf_token": t, "first_name": "Portal",
                       "last_name": "Onboarding", "email": "portal-onboard@demo.test"},
                      headers=H)
    assert r.status_code in (302, 303)
    db = SessionLocal()
    c = db.query(Client).filter_by(email="portal-onboard@demo.test").first()
    assert c is not None
    cid = c.id
    db.close()

    t = csrf(director, f"/clients/{cid}")
    r = director.post(f"/clients/{cid}/create-portal", data={"csrf_token": t}, headers=H)
    assert r.status_code in (302, 303)
    invite = next((txt for to, sub, txt in sent if to == "portal-onboard@demo.test"), None)
    assert invite and "Temporary Password: " in invite
    temp_password = invite.split("Temporary Password: ", 1)[1].splitlines()[0].strip()
    db = SessionLocal()
    c = db.get(Client, cid)
    u = db.get(User, c.portal_user_id)
    assert u and u.must_reset_password and c.portal_enabled
    db.close()

    # The login must not give temporary credentials access to the case.
    portal = app.test_client()
    r = login(portal, "portal-onboard@demo.test", temp_password)
    assert r.status_code in (302, 303)
    assert "/auth/change-password" in r.headers["Location"]
    r = portal.get("/portal/dashboard", headers=H)
    assert r.status_code == 302 and "/auth/change-password" in r.headers["Location"]

    t = csrf(portal, "/auth/change-password")
    r = portal.post("/auth/change-password", data={"csrf_token": t,
                    "old_password": temp_password, "new_password": "Onboard-Strong-Pass-2026",
                    "confirm_password": "Onboard-Strong-Pass-2026"}, headers=H)
    assert r.status_code in (302, 303)
    assert "/portal/dashboard" in r.headers["Location"]
    page = portal.get("/portal/dashboard", headers=H)
    assert page.status_code == 200

    t = csrf(portal, "/portal/dashboard")
    r = portal.post("/portal/upload", data={"csrf_token": t,
                    "file": (io.BytesIO(b"Example non-sensitive onboarding QA document."), "onboarding_qa.txt")},
                    headers=H, content_type="multipart/form-data")
    assert r.status_code in (302, 303)
    db = SessionLocal()
    try:
        doc = db.query(Document).filter_by(client_id=cid).first()
        assert doc and doc.uploaded_by_user_id == u.id
        assert db.query(Alert).filter_by(user_id=db.query(User).filter_by(email=EMAILS["director"]).first().id,
                                         type="upload").count() >= 1
        own_doc_id = doc.id
    finally:
        db.close()
    assert portal.get(f"/portal/documents/{own_doc_id}/download", headers=H).status_code == 200
    html = director.get(f"/clients/{cid}", headers=H).get_data(as_text=True)
    assert "/resend-portal-invite" in html


def test_director_one_time_client_invite_can_be_accepted(app, monkeypatch):
    sent = []
    def fake_send(to, subject, text, html, **kwargs):
        sent.append(text)
        return True, "sent"
    monkeypatch.setattr("app.services.email_service.send_email_html", fake_send)
    director = app.test_client()
    assert login(director, EMAILS["director"], PASSWORD).status_code in (302, 303)
    t = csrf(director, "/director/invites/")
    r = director.post("/director/invites/create", data={"csrf_token": t,
                      "email": "invited-vet@demo.test", "role": "CLIENT"}, headers=H)
    assert r.status_code in (302, 303) and sent
    link = re.search(r"https?://[^\s]+", sent[-1]).group(0)
    path = urlparse(link).path
    visitor = app.test_client()
    t = csrf(visitor, path)
    r = visitor.post(path, data={"csrf_token": t, "full_name": "Invited Veteran",
                   "password": "InvitedVetPass-2026", "confirm_password": "InvitedVetPass-2026"},
                   headers=H)
    assert r.status_code in (302, 303)
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(email="invited-vet@demo.test").one()
        c = db.query(Client).filter_by(portal_user_id=u.id).one()
        assert c.portal_enabled and c.email_verified
        inv = db.query(InviteLink).filter_by(email="invited-vet@demo.test").one()
        assert inv.is_used
    finally:
        db.close()
    assert visitor.get("/portal/dashboard", headers=H).status_code == 200
    unused = app.test_client()
    assert unused.get(path, headers=H).status_code in (302, 303)
