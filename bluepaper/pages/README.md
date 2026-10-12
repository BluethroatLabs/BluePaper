# Public page templates

Edit `layout.html` to update the shared navigation, product branding, footer,
icons, stylesheet, or theme scripts across all seven public site pages.

Each page fragment has three parts:

1. Page-specific head markup, including its title, metadata, and structured data.
2. `<!-- CONTENT -->` followed by the page content.
3. `<!-- SCRIPTS -->` followed by any page-specific scripts.

The renderer in `bluepaper/api/site.py` supplies the homepage product heading,
active navigation state, and About page's scroll class. Legal pages use the same
layout through `bluepaper/web/legal.html`, with content from `bluepaper/web/legal/`.

Restart the server after changing templates; the main site pages render at startup.
