"""9anime search and browse helpers.

The site is a WordPress theme, so search is a plain `?s=` scrape: every hit is
an `<article class="bs">` card whose link is the series page. The browse pages
list the same cards, so the homepage rows reuse one scrape.
"""

import re
from html import unescape
from urllib.parse import quote

try:
    from ...config import NINEANIME_SERIES_PATTERN
    from ..common.http import get_html
except ImportError:
    from aniworld.config import NINEANIME_SERIES_PATTERN
    from aniworld.models.common.http import get_html

BASE_URL = "https://9anime.org.lv"

# The two front-page listings: newest uploads first, then the most-watched.
LATEST_URL = f"{BASE_URL}/anime/?order=update"
POPULAR_URL = f"{BASE_URL}/most-popular/"

_ARTICLE_RE = re.compile(r'<article class="bs".*?</article>', re.DOTALL)
_LINK_RE = re.compile(r'<a href="([^"]+)"[^>]*title="([^"]*)"')
_IMAGE_RE = re.compile(r'<img[^>]*src="([^"]+)"')


def _clean_title(value):
    return unescape(re.sub(r"\s+", " ", value or "")).strip()


def _parse_articles(html, limit):
    """Series cards out of any listing page, in document order."""
    results = []
    for article in _ARTICLE_RE.findall(html or ""):
        link = _LINK_RE.search(article)
        if not link:
            continue
        url = link.group(1)
        if not NINEANIME_SERIES_PATTERN.match(url):
            continue
        image = _IMAGE_RE.search(article)
        results.append(
            {
                "title": _clean_title(link.group(2)),
                "url": url,
                "poster_url": image.group(1) if image else "",
            }
        )
        if len(results) >= limit:
            break
    return results


def query_animes(keyword, limit=24):
    """Search 9anime, returning [{title, url, poster_url}] for series hits."""
    html = get_html(f"{BASE_URL}/?s={quote(keyword.strip())}", timeout=25)
    return _parse_articles(html, limit)


def fetch_latest_animes(limit=30):
    """The upload feed, straight off the listing sorted by recency."""
    return _parse_articles(get_html(LATEST_URL, timeout=25), limit)


def fetch_popular_animes(limit=30):
    """The site's own most-watched page."""
    return _parse_articles(get_html(POPULAR_URL, timeout=25), limit)
