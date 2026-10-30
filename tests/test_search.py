"""Search engine plumbing: robots, sitemap, IndexNow key file, Bing verification, notify button."""
import pytest

from app import create_app
from app.services import indexnow
from tests.seed_demo import seed
from tests.test_workflows import login, token, H


@pytest.fixture(scope="module")
def app():
    a = create_app()
    a.config.update(TESTING=True)
    from app.extensions import limiter
    limiter.enabled = False
    with a.app_context():
        seed()
    return a


def test_public_search_files(app, monkeypatch):
    c = app.test_client()
    robots = c.get("/robots.txt").get_data(as_text=True)
    assert "Sitemap:" in robots and "Disallow: /claims/" in robots
    sm = c.get("/sitemap.xml").get_data(as_text=True)
    assert "/become-client" in sm and "/resources/benefits-finder" in sm
    assert c.get(f"/{indexnow.key()}.txt").get_data(as_text=True) == indexnow.key()
    assert c.get("/notthekey.txt").status_code == 404
    assert c.get("/BingSiteAuth.xml").status_code == 404
    monkeypatch.setenv("BING_SITE_VERIFICATION", "ABC123")
    assert "ABC123" in c.get("/BingSiteAuth.xml").get_data(as_text=True)
    assert "resumebuilder.shop/military-to-civilian-resume" in c.get("/").get_data(as_text=True)


def test_notify_search_engines(app, monkeypatch):
    sent = {}
    monkeypatch.setattr(indexnow, "submit", lambda urls, host: (sent.update(urls=urls, host=host) or (True, "Search engines answered 202")))
    d, _ = login(app, "director")
    tok = token(d, "/director/traffic")
    r = d.post("/director/search/ping", data={"csrf_token": tok}, headers=H, follow_redirects=True)
    assert "Sent" in r.get_data(as_text=True) and len(sent["urls"]) > 10
    assert all(u.startswith("https://") for u in sent["urls"])
