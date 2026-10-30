"""Smoke tests: the public site renders, security settings are in place, and staff login works.

Run with:  python -m pytest -q
"""
import re

import pytest

from app import create_app
from app.extensions import SessionLocal
from app.models import Organization, User
from app.models.user import Role

HTTPS = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7", "Host": "www.vetassistance.org"}

PUBLIC_PAGES = [
    "/",
    "/faq",
    "/guides",
    "/crsc",
    "/privacy",
    "/security",
    "/become-client",
    "/portal",
    "/auth/login",
    "/resources/benefits-finder",
    "/robots.txt",
    "/sitemap.xml",
]


@pytest.fixture(scope="module")
def app():
    application = create_app()
    application.config.update(TESTING=True)
    with application.app_context():
        db = SessionLocal()
        try:
            org = db.query(Organization).first()
            if not org:
                org = Organization(name="Veteran Benefits Assistance")
                db.add(org)
                db.commit()
            if not db.query(User).filter_by(email="director@test.local").first():
                u = User(org_id=org.id, email="director@test.local", full_name="Test Director", role=Role.DIRECTOR)
                u.set_password("Correct-Horse-9")
                db.add(u)
                db.commit()
        finally:
            db.close()
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_pages_render(client, path):
    resp = client.get(path, headers=HTTPS)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"


def test_homepage_content_and_seo(client):
    html = client.get("/", headers=HTTPS).get_data(as_text=True)
    assert "Build a VA claim with no missing evidence." in html
    assert 'id="crisis"' in html, "nav links to #crisis, so the homepage must have it"
    assert "988" in html
    canonical = re.search(r'rel="canonical" href="([^"]+)"', html).group(1)
    assert canonical.startswith("https://"), canonical
    assert "og-image.png" in html
    assert "/pricing" not in html.split("<main")[0], "pricing should not be in the public nav"


def test_no_ads_anywhere(client):
    html = client.get("/", headers=HTTPS).get_data(as_text=True)
    assert "googlesyndication" not in html
    assert "adsbygoogle" not in html


def test_site_font_is_not_times(client):
    css = client.get("/static/css/app.css").get_data(as_text=True)
    m = re.search(r"/\* Global typography[^*]*\*/\s*body, input, button, select, textarea\{([^}]*)\}", css)
    assert m, "global typography rule missing"
    assert "Times New Roman" not in m.group(1)


def test_security_headers(client):
    resp = client.get("/", headers=HTTPS)
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert resp.headers.get("Referrer-Policy")


def test_login_form_has_csrf_token(client):
    html = client.get("/auth/login", headers=HTTPS).get_data(as_text=True)
    assert 'name="csrf_token"' in html


def test_login_without_csrf_is_rejected(client):
    resp = client.post("/auth/login", data={"email": "director@test.local", "password": "Correct-Horse-9"}, headers=HTTPS)
    assert resp.status_code == 400


def test_staff_login_and_dashboard(client):
    html = client.get("/auth/login", headers=HTTPS).get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    resp = client.post(
        "/auth/login",
        data={"email": "director@test.local", "password": "Correct-Horse-9", "csrf_token": token},
        headers=HTTPS,
    )
    assert resp.status_code in (302, 303)
    # A logged-in staff page renders (exercises the unread-message counter) and isn't cached.
    page = client.get("/", headers=HTTPS)
    assert page.status_code == 200
    assert page.headers.get("Cache-Control") == "no-store"


def test_wrong_password_rejected(client):
    html = client.get("/auth/login", headers=HTTPS).get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    resp = client.post(
        "/auth/login",
        data={"email": "director@test.local", "password": "wrong", "csrf_token": token},
        headers=HTTPS,
        follow_redirects=True,
    )
    assert b"Invalid email" in resp.data


def test_pricing_page_redirects_home(client):
    resp = client.get("/pricing", headers=HTTPS)
    assert resp.status_code == 301
