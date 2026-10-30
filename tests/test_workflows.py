"""End-to-end checks for the staff/client workflows: document requests, notifications, to-dos, deadlines, activity."""
import io
import re

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import Client, ClientTask, Alert, StaffTask, User
from tests.seed_demo import seed, EMAILS, PASSWORD

H = {"X-Forwarded-Proto": "https", "Host": "localhost"}


def login(app, role):
    c = app.test_client()
    html = c.get("/auth/login", headers=H).get_data(as_text=True)
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    r = c.post("/auth/login", data={"email": EMAILS[role], "password": PASSWORD, "csrf_token": tok}, headers=H)
    assert r.status_code in (302, 303)
    return c, tok


def token(c, url):
    html = c.get(url, headers=H).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config.update(TESTING=True)
    from app.extensions import limiter
    limiter.enabled = False
    with a.app_context():
        seed()
    return a


def _marcus():
    db = SessionLocal()
    try:
        return db.query(Client).filter_by(first_name="Marcus").first().id
    finally:
        db.close()


def test_document_request_round_trip(app):
    cid = _marcus()
    staff, _ = login(app, "employee")
    tok = token(staff, f"/tasks/client/{cid}")
    r = staff.post(f"/tasks/client/{cid}/add", data={"title": "Upload your hearing test", "kind": "document", "csrf_token": tok}, headers=H)
    assert r.status_code in (302, 303)
    db = SessionLocal()
    t = db.query(ClientTask).filter_by(client_id=cid, title="Upload your hearing test").first()
    assert t and t.kind == "document" and not t.is_completed
    client_user = db.query(User).filter_by(email=EMAILS["client"]).first()
    assert db.query(Alert).filter(Alert.user_id == client_user.id, Alert.type == "request").count() >= 1
    tid = t.id
    db.close()

    client, _ = login(app, "client")
    page = client.get("/portal/dashboard", headers=H).get_data(as_text=True)
    assert "Upload your hearing test" in page and f'value="{tid}"' in page
    tok = token(client, "/portal/dashboard")
    r = client.post("/portal/upload", data={"csrf_token": tok, "task_id": str(tid),
                                            "file": (io.BytesIO(b"%PDF-1.4 test"), "audiogram.pdf")},
                    headers=H, content_type="multipart/form-data")
    assert r.status_code in (302, 303)
    db = SessionLocal()
    try:
        assert db.get(ClientTask, tid).is_completed
        emp = db.query(User).filter_by(email=EMAILS["employee"]).first()
        assert db.query(Alert).filter(Alert.user_id == emp.id, Alert.type == "upload").count() >= 1
    finally:
        db.close()


def test_notifications_page_and_open(app):
    client, _ = login(app, "client")
    html = client.get("/notifications", headers=H).get_data(as_text=True)
    assert "Your team requested a document" in html
    m = re.search(r'href="(/notifications/\d+/open)"', html)
    r = client.get(m.group(1), headers=H)
    assert r.status_code in (302, 303)


def test_todo_add_toggle_and_decision_deadline(app):
    cid = _marcus()
    d, _ = login(app, "director")
    tok = token(d, "/employee")
    r = d.post("/employee/todos/add", data={"title": "Call VA about exam", "client_id": str(cid), "due_date": "2026-11-05", "csrf_token": tok}, headers=H)
    assert r.status_code in (302, 303)
    db = SessionLocal()
    t = db.query(StaffTask).filter_by(title="Call VA about exam").first()
    assert t and t.client_id == cid
    tid = t.id
    db.close()
    assert "Call VA about exam" in d.get("/employee", headers=H).get_data(as_text=True)
    d.post(f"/employee/todos/{tid}/toggle", data={"csrf_token": tok}, headers=H)
    db = SessionLocal()
    assert db.get(StaffTask, tid).is_done
    db.close()

    tok = token(d, f"/clients/{cid}")
    d.post(f"/clients/{cid}/stage", data={"stage": "decision", "decision_date": "2026-10-01", "csrf_token": tok}, headers=H)
    db = SessionLocal()
    try:
        dl = db.query(StaffTask).filter_by(client_id=cid, kind="deadline").first()
        assert dl and dl.due_date.strftime("%Y-%m-%d") == "2027-10-01"
    finally:
        db.close()


def test_activity_feed(app):
    cid = _marcus()
    d, _ = login(app, "director")
    html = d.get(f"/clients/{cid}/activity", headers=H).get_data(as_text=True)
    assert "uploaded a document" in html and "requested a document" in html and "set a deadline" in html
    assert d.get(f"/clients/{cid}/activity?only=message", headers=H).status_code == 200
