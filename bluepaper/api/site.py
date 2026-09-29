"""Public pages and the files crawlers read: robots.txt, sitemap.xml, llms.txt."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

PAGES_DIR = Path(__file__).resolve().parent.parent / "pages"
SITE_URL_TOKEN = "{{SITE_URL}}"

# Route, template, and the date its content last changed (sitemap lastmod).
PAGES = (
    ("/", "index.html", "2026-09-29"),
    ("/about", "about.html", "2026-09-29"),
    ("/how-it-works", "how-it-works.html", "2026-09-29"),
    ("/for-agents", "for-agents.html", "2026-09-29"),
)


def render(name: str, site_url: str) -> str:
    text = (PAGES_DIR / name).read_text(encoding="utf-8")
    return text.replace(SITE_URL_TOKEN, site_url)


def robots_txt(site_url: str) -> str:
    return (
        f"User-agent: *\nAllow: /\nDisallow: /v1/\n\nSitemap: {site_url}/sitemap.xml\n"
    )


def sitemap_xml(site_url: str) -> str:
    urls = "".join(
        f"  <url>\n    <loc>{site_url}{path}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n  </url>\n"
        for path, _, lastmod in PAGES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )


def _page(html: str) -> Callable[[], HTMLResponse]:
    def endpoint() -> HTMLResponse:
        return HTMLResponse(html)

    return endpoint


def mount_site(application: FastAPI, site_url: str) -> None:
    for path, name, _ in PAGES:
        application.add_api_route(
            path,
            _page(render(name, site_url)),
            methods=["GET"],
            include_in_schema=False,
            response_class=HTMLResponse,
        )

    robots = robots_txt(site_url)
    sitemap = sitemap_xml(site_url)
    llms = render("llms.txt", site_url)

    @application.get("/robots.txt", include_in_schema=False)
    def robots_endpoint() -> PlainTextResponse:
        return PlainTextResponse(robots)

    @application.get("/sitemap.xml", include_in_schema=False)
    def sitemap_endpoint() -> Response:
        return Response(sitemap, media_type="application/xml")

    @application.get("/llms.txt", include_in_schema=False)
    def llms_endpoint() -> PlainTextResponse:
        return PlainTextResponse(llms)
