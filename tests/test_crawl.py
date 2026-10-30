"""Sign in as each role and open every page the app can reach; no page may error (HTTP 500).

Routes that need an id get the demo client's id. POST-only and download routes are skipped.
"""
import re

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import Client
from tests.seed_demo import seed, EMAILS, PASSWORD

HTTPS = {"X-Forwarded-Proto": "https", "Host": "localhost"}
SKIP_PARTS = ("download", "export", "logout", "/static", "/api/", "delete", "worker", "rq", "stripe", "webhook",
              "/pdf", ".pdf", ".docx", "/print", "/file/", "/raw", "/preview-file", "/zip")


def _login(client, email):
    html = client.get("/auth/login", headers=HTTPS).get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    resp = client.post("/auth/login", data={"email": email, "password": PASSWORD, "csrf_token": token}, headers=HTTPS)
    assert resp.status_code in (302, 303), f"login failed for {email}"


@pytest.fixture(scope="module")
def app():
    application = create_app()
    application.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    from app.extensions import limiter
    limiter.enabled = False
    with application.app_context():
        seed()
    return application


def _urls(app):
    db = SessionLocal()
    try:
        cid = db.query(Client).filter_by(first_name="Marcus").first().id
    finally:
        db.close()
    out = set()
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or any(p in rule.rule for p in SKIP_PARTS):
            continue
        args = set(rule.arguments)
        if not args:
            out.add(rule.rule)
        elif args <= {"client_id"}:
            out.add(rule.rule.replace("<int:client_id>", str(cid)).replace("<client_id>", str(cid)))
    return sorted(out)


@pytest.mark.parametrize("role", ["director", "employee", "client"])
def test_no_page_errors(app, role):
    c = app.test_client()
    _login(c, EMAILS[role])
    failures = []
    for url in _urls(app):
        try:
            r = c.get(url, headers=HTTPS)
            code = r.status_code
        except Exception as exc:  # pragma: no cover - surfaced in the failure list
            code = f"exception: {exc!r}"[:160]
        if code == 500 or isinstance(code, str):
            failures.append(f"{url} -> {code}")
    assert not failures, f"{role}: " + "; ".join(failures)


def test_staff_search_finds_clients(app):
    c = app.test_client()
    _login(c, EMAILS["director"])
    data = c.get("/api/search?q=reed", headers=HTTPS).get_json()
    assert any(r["name"] == "Marcus Reed" for r in data["clients"])


def test_client_cannot_use_staff_search(app):
    c = app.test_client()
    _login(c, EMAILS["client"])
    assert c.get("/api/search?q=reed", headers=HTTPS).status_code in (302, 403)


def test_stage_change_posts_client_update(app):
    from app.models.claim_update import ClaimUpdate
    c = app.test_client()
    _login(c, EMAILS["employee"])
    db = SessionLocal()
    cid = db.query(Client).filter_by(first_name="Tom").first().id
    db.close()
    html = c.get(f"/clients/{cid}", headers=HTTPS).get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    r = c.post(f"/clients/{cid}/stage", data={"stage": "filed", "csrf_token": token}, headers=HTTPS)
    assert r.status_code in (302, 303)
    db = SessionLocal()
    try:
        assert db.query(Client).get(cid).status == "Filed with VA"
        assert db.query(ClaimUpdate).filter_by(client_id=cid, status_key="filed").count() == 1
    finally:
        db.close()
