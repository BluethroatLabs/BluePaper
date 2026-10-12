"""Public pages and the files crawlers read: robots.txt, sitemap.xml, llms.txt."""

from __future__ import annotations

import re
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
)


def render(name: str, site_url: str) -> str:
    text = (PAGES_DIR / name).read_text(encoding="utf-8")
    if name.endswith(".html"):
        text = render_layout(text, name=name)
    return text.replace(SITE_URL_TOKEN, site_url)


def render_layout(page: str, *, name: str) -> str:
    """Compose a page's metadata, content, and scripts with the shared shell.

    Page fragments use CONTENT and SCRIPTS comment separators. These are trusted
    repository templates, not user-supplied HTML.
    """
    head, separator, remainder = page.partition("<!-- CONTENT -->")
    content, scripts_separator, scripts = remainder.partition("<!-- SCRIPTS -->")
    if not separator or not scripts_separator:
        raise ValueError(f"Missing layout separators in {name}")
    values = {
        "HEAD": head.strip(),
        "CONTENT": content.strip(),
        "SCRIPTS": scripts.strip(),
        "MAIN_ATTRIBUTES": ' class="scroll-page"' if name == "about.html" else "",
        "HOME_HREF": "#convert" if name == "index.html" else "/",
        "PRODUCT_TAG": "h1" if name in ("index.html", "legal.html") else "p",
        "ABOUT_CURRENT": ' aria-current="page"' if name == "about.html" else "",
        "HOW_CURRENT": ' aria-current="page"' if name == "how-it-works.html" else "",
        "AGENTS_CURRENT": ' aria-current="page"' if name == "for-agents.html" else "",
    }
    layout = (PAGES_DIR / "layout.html").read_text(encoding="utf-8")
    # One substitution pass keeps page content from being interpreted as tokens.
    return re.sub(r"{{([A-Z_]+)}}", lambda match: values[match[1]], layout)


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
