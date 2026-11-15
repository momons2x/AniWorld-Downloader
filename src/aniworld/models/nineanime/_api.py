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


# Every listing page tags its titles with their genres, so one scrape of the
# front page is enough to read off the whole vocabulary the site uses.
_GENRE_LINK_RE = re.compile(
    r'<a[^>]+href="[^"]*/genres/([^"/]+)/?"[^>]*>(.*?)</a>', re.DOTALL
)


def fetch_genres(limit=200):
    """Genre names and slugs, read off the front page's genre tags.

    Returns [{name, slug}] in the order the page lists them, deduplicated.
    """
    html = get_html(f"{BASE_URL}/", timeout=25)
    genres = []
    seen = set()
    for slug, label in _GENRE_LINK_RE.findall(html):
        name = _clean_title(re.sub(r"<[^>]+>", "", unescape(label)))
        if not name or slug in seen:
            continue
        seen.add(slug)
        genres.append({"name": name, "slug": slug})
        if len(genres) >= limit:
            break
    return genres


def fetch_genre_animes(slug, page=1, limit=50):
    """One page of a genre listing as {"results": [...], "has_more": bool}.

    The site serves a fixed number of cards per page and links the next one
    only while there is one, so the pager link is what settles has_more.
    """
    safe_slug = quote(str(slug), safe="")
    url = f"{BASE_URL}/genres/{safe_slug}/"
    if page > 1:
        url = f"{url}page/{page}/"

    html = get_html(url, timeout=25)
    return {
        "results": _parse_articles(html, limit),
        "has_more": f"/genres/{safe_slug}/page/{page + 1}/" in html,
    }
