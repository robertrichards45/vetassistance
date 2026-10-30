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


def test_traffic_counts_only_people(app):
    import re
    from app.extensions import SessionLocal
    from app.models import HumanVisit
    from app.routes.visits import page_token
    ua_h = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Mobile Safari/604.1"}
    c = app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "/v" in html and "sendBeacon" in html, "anonymous pages carry the people counter"
    with app.test_request_context():
        tok = page_token("/faq")
    before = SessionLocal().query(HumanVisit).count()
    # No interaction signal, a bad token, and a bot user agent are all ignored.
    c.post("/v", data={"t": tok, "s": "", "d": "5"}, headers=ua_h)
    c.post("/v", data={"t": "forged", "s": "scroll", "d": "5"}, headers=ua_h)
    c.post("/v", data={"t": tok, "s": "scroll", "d": "5"}, headers={"User-Agent": "Googlebot/2.1"})
    assert SessionLocal().query(HumanVisit).count() == before
    r = c.post("/v", data={"t": tok, "s": "scroll", "d": "12", "r": "https://www.google.com/"}, headers=ua_h)
    assert r.status_code == 204
    c.post("/v", data={"t": tok, "s": "scroll", "d": "12"}, headers=ua_h)  # duplicate within 30s
    db = SessionLocal()
    rows = db.query(HumanVisit).all()
    assert len(rows) == before + 1 and rows[-1].path == "/faq" and rows[-1].device == "mobile" and rows[-1].referrer_host == "www.google.com"
    db.close()
    d, _ = login(app, "director")
    page = d.get("/director/traffic", headers=H).get_data(as_text=True)
    assert re.search(r"People, last 30 days</span><strong>\s*1\s*<", page) and "Google" in page
    assert "sendBeacon" not in page, "staff pages never count"


def test_traffic_ignores_scanners(app):
    import re
    from datetime import datetime
    from app.extensions import SessionLocal
    from app.models import PageView
    d, _ = login(app, "director")

    def counts():
        html = d.get("/director/traffic", headers=H).get_data(as_text=True)
        v = int(re.search(r"<dt>Page loads</dt><dd>([\d,]+)", html).group(1).replace(",", ""))
        u = int(re.search(r"<dt>Different devices</dt><dd>([\d,]+)", html).group(1).replace(",", ""))
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


@pytest.mark.parametrize("slug", ["va-rating-for-tinnitus", "va-rating-for-sleep-apnea", "va-rating-for-ptsd", "va-rating-for-back-pain", "va-rating-for-knee-pain"])
def test_condition_guides_publish(slug):
    from app import create_app
    app = create_app(); app.config.update(TESTING=True)
    c = app.test_client(); h = {"X-Forwarded-Proto": "https", "Host": "www.vetassistance.org"}
    page = c.get(f"/blog/{slug}", headers=h)
    assert page.status_code == 200 and "not accredited by VA" in page.get_data(as_text=True)
    assert slug in c.get("/sitemap.xml", headers=h).get_data(as_text=True)
