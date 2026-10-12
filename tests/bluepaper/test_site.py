from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bluepaper.api.app import create_app
from bluepaper.api.site import PAGES
from bluepaper.config import Settings
from bluepaper.storage.base import Stores

SITE = "https://paper.example.org"
LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


@pytest.fixture
def site(settings: Settings, stores: Stores) -> TestClient:
    settings.public_url = SITE
    return TestClient(create_app(settings, stores))


@pytest.mark.parametrize(
    "path",
    ["/", "/about", "/how-it-works", "/privacy", "/terms", "/support"],
)
def test_shared_layout_is_rendered_on_every_page(site: TestClient, path: str) -> None:
    html = site.get(path).text
    assert html.count('class="topbar"') == 1
    assert html.count('class="product-lockup"') == 1
    assert html.count('class="page-footer"') == 1
    assert html.count('src="/ui/theme.js"') == 1
    assert html.count("<h1") == 1
    assert "{{" not in html
    assert "<!-- CONTENT -->" not in html
    if path in ("/about", "/how-it-works"):
        assert f'href="{path}" aria-current="page"' in html


@pytest.mark.parametrize("path", [path for path, _, _ in PAGES])
def test_pages_carry_canonical_and_social_metadata(site: TestClient, path: str) -> None:
    response = site.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "{{SITE_URL}}" not in html
    url = f"{SITE}{path}"
    assert f'<link rel="canonical" href="{url}" />' in html
    assert f'<meta property="og:url" content="{url}" />' in html
    assert f'<meta property="og:image" content="{SITE}/ui/og-image.png" />' in html
    assert '<meta name="twitter:card" content="summary_large_image" />' in html
    assert re.search(r'<meta\s+name="description"\s+content="[^"]{50,}"', html)
    assert html.count("<h1") == 1
    blocks = LD_JSON.findall(html)
    assert blocks
    for block in blocks:
        json.loads(block)


def test_about_faq_matches_structured_data(site: TestClient) -> None:
    html = site.get("/about").text
    match = LD_JSON.search(html)
    assert match is not None
    graph = json.loads(match.group(1))["@graph"]
    faq = next(node for node in graph if node["@type"] == "FAQPage")
    questions = [entry["name"] for entry in faq["mainEntity"]]
    assert len(questions) == html.count('class="faq-item"')
    for question in questions:
        assert f"</span>{question}</summary>" in html
    assert "Delete does not currently remove it" in html


def test_og_image_is_public(site: TestClient) -> None:
    response = site.get("/ui/og-image.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_removed_agents_page_is_unavailable(site: TestClient) -> None:
    assert site.get("/for-agents").status_code == 404
    assert "/for-agents" not in site.get("/sitemap.xml").text
    assert "/for-agents" not in site.get("/llms.txt").text


def test_templates_are_not_served_raw(site: TestClient) -> None:
    assert site.get("/ui/index.html").status_code == 404
    assert site.get("/ui/about.html").status_code == 404


def test_robots_points_at_sitemap(site: TestClient) -> None:
    response = site.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Disallow: /v1/" in response.text
    assert f"Sitemap: {SITE}/sitemap.xml" in response.text


def test_sitemap_lists_every_page(site: TestClient) -> None:
    response = site.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(response.content)
    locs = [loc.text for loc in root.findall("s:url/s:loc", ns)]
    assert locs == [f"{SITE}{path}" for path, _, _ in PAGES]


def test_llms_txt_links_absolute_pages(site: TestClient) -> None:
    response = site.get("/llms.txt")
    assert response.status_code == 200
    assert response.text.startswith("# BluePaper\n")
    assert "{{SITE_URL}}" not in response.text
    for path, _, _ in PAGES:
        assert f"]({SITE}{path})" in response.text


def test_site_routes_are_not_in_openapi(site: TestClient) -> None:
    paths = site.get("/openapi.json").json()["paths"]
    for path in [
        "/about",
        "/how-it-works",
        "/for-agents",
        "/robots.txt",
        "/sitemap.xml",
        "/llms.txt",
    ]:
        assert path not in paths


def test_public_url_strips_trailing_slash() -> None:
    settings = Settings(api_key="k", public_url="https://paper.example.org/ ")
    assert settings.public_url == "https://paper.example.org"


@pytest.mark.parametrize(
    "value",
    [
        "paper.example.org",
        "javascript:alert(1)",
        'https://paper.example.org/"><script>',
        "https://paper.example.org/?a=1&b=2",
    ],
)
def test_public_url_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_key="k", public_url=value)
