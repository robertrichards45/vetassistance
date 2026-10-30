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


def test_traffic_ignores_scanners(app):
    import re
    from datetime import datetime
    from app.extensions import SessionLocal
    from app.models import PageView
    d, _ = login(app, "director")

    def counts():
        html = d.get("/director/traffic", headers=H).get_data(as_text=True)
        v = int(re.search(r"Page views</span><strong>([\d,]+)", html).group(1).replace(",", ""))
        u = int(re.search(r"Unique visitors</span><strong>([\d,]+)", html).group(1).replace(",", ""))
        return v, u
    v0, u0 = counts()
    db = SessionLocal()
    ua = "Mozilla/5.0 (Windows NT 10.0) Chrome/120"
    for path in ["/.env", "/", "/contact"]:   # scanner: probes .env, then loads normal pages
        db.add(PageView(path=path, ip_hash="scan1", user_agent=ua, created_at=datetime.utcnow()))
    for path in ["/", "/faq", "/robots.txt"]:  # a real visitor
        db.add(PageView(path=path, ip_hash="real1", user_agent=ua, created_at=datetime.utcnow()))
    db.commit(); db.close()
    v1, u1 = counts()
    assert (v1 - v0, u1 - u0) == (2, 1)
