"""SEO regression tests for public indexing, private pages and sitemap hygiene."""
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import pytest

from app import create_app

HTTPS_WWW = {"X-Forwarded-Proto": "https", "Host": "www.vetassistance.org"}
CANONICAL_ORIGIN = "https://vetassistance.org"


@pytest.fixture(scope="module")
def client():
    application = create_app()
    application.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    return application.test_client()


@pytest.mark.parametrize("path", [
    "/", "/become-client", "/resources/benefits-finder",
    "/tools/decision-letter", "/tools/intent-to-file", "/tools/cp-exam-prep",
    "/diy", "/crsc", "/guides",
])
def test_public_pages_are_indexable_with_stable_canonical(client, path):
    response = client.get(path, headers=HTTPS_WWW)
    assert response.status_code == 200, path
    html = response.get_data(as_text=True)
    assert '<meta name="robots" content="index,follow">' in html, path
    assert f'<link rel="canonical" href="{CANONICAL_ORIGIN}{path}">' in html, path
    assert f'<meta property="og:url" content="{CANONICAL_ORIGIN}{path}">' in html, path


@pytest.mark.parametrize("path", [
    "/auth/login", "/diy/login", "/diy/register", "/cfr/ratings-chart",
])
def test_private_or_login_pages_are_not_indexable(client, path):
    response = client.get(path, headers=HTTPS_WWW)
    assert response.status_code == 200, path
    html = response.get_data(as_text=True)
    assert '<meta name="robots" content="noindex,nofollow">' in html, path


def test_sitemap_only_contains_public_indexable_200_pages(client):
    response = client.get("/sitemap.xml", headers=HTTPS_WWW)
    assert response.status_code == 200
    root = ET.fromstring(response.data)
    locations = [n.text for n in root.findall(".//{*}loc")]
    assert locations
    assert len(locations) == len(set(locations))
    assert CANONICAL_ORIGIN + "/become-client" in locations
    assert CANONICAL_ORIGIN + "/resources/benefits-finder" in locations
    assert CANONICAL_ORIGIN + "/diy" in locations
    for removed in ("/diy/login", "/diy/register", "/cfr/ratings-chart", "/pricing", "/services-locator"):
        assert CANONICAL_ORIGIN + removed not in locations
    for loc in locations:
        assert loc.startswith(CANONICAL_ORIGIN + "/"), loc
        parsed = urlsplit(loc)
        page = client.get(parsed.path, headers=HTTPS_WWW)
        assert page.status_code == 200, (loc, page.status_code)
        assert '<meta name="robots" content="index,follow">' in page.get_data(as_text=True), loc


def test_robots_sitemap_uses_canonical_host(client):
    response = client.get("/robots.txt", headers=HTTPS_WWW)
    assert response.status_code == 200
    robots = response.get_data(as_text=True)
    assert "Sitemap: https://vetassistance.org/sitemap.xml" in robots
    assert "Disallow: /clients/" in robots
    assert "Disallow: /portal/" in robots
