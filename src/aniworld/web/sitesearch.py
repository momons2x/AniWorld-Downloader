"""Site search plumbing shared by the web search API and the Discord bot.

Each site's search returns a slightly different shape, this normalises them all
to {title, url} and filters out season/episode links so only series-level hits
come back.
"""

import re

from ..logger import get_logger
from ..models.miruro._api import query_animes as query_miruro
from ..models.nineanime._api import query_animes as query_nineanime
from ..models.reanime._api import query_animes as query_reanime
from ..search import (
    fetch_aniworld_genres,
    query_aniworld,
)

logger = get_logger(__name__)

# Each site searches through its own model package; the functions all return
# [{title, url, poster_url}] so the normaliser below can pass them straight on.
SITE_SEARCH = {
    "aniworld": query_aniworld,
    "nineanime": query_nineanime,
    "miruro": query_miruro,
    "reanime": query_reanime,
}

# Aniworld returns relative `/.../<slug>` links, everything else returns an
# absolute `url`. This one also needs series-only filtering.
_RELATIVE_SITES = {
    "aniworld": (
        "https://aniworld.to",
        re.compile(r"^/anime/stream/[a-zA-Z0-9\-]+/?$", re.IGNORECASE),
    ),
}

# Sites checked for a Discord request, in priority order.
SERIES_SITES = ("miruro", "reanime")
MOVIE_SITES = ()


def sites_for(media_type):
    return MOVIE_SITES if media_type == "movie" else SERIES_SITES


def _clean_title(value, fallback=""):
    title = value or fallback or "Unknown"
    return title.replace("<em>", "").replace("</em>", "").strip()


def _poster(item):
    for key in ("poster_url", "cover_url", "image", "poster"):
        value = item.get(key)
        if value:
            return value
    return ""


def _normalise(site, raw, fallback=""):
    """Turn one site's raw hits into [{title, url, poster}], dropping the rest."""
    if isinstance(raw, dict):
        raw = [raw]

    results = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _resolve_url(site, item)
        if not url:
            continue
        results.append(
            {
                "title": _clean_title(item.get("title") or item.get("name"), fallback),
                "url": url,
                "poster": _poster(item),
            }
        )
    return results


def search(site, keyword, *, raise_errors=False):
    """Search one site; callers can opt into errors instead of an empty list."""
    query = SITE_SEARCH.get(site)
    if not query:
        return []

    try:
        raw = query(keyword) or []
    except Exception as exc:
        logger.warning("Search on %s failed (%s)", site, type(exc).__name__)
        if raise_errors:
            raise
        return []
    return _normalise(site, raw, keyword)


def _resolve_url(site, item):
    url = item.get("url")
    if url:
        return url

    link = item.get("link")
    if not link:
        return None
    base, pattern = _RELATIVE_SITES.get(site, (None, None))
    if base is None:
        return link
    # Skip season/episode links, only series pages belong in results
    return base + link if pattern.match(link) else None


# Every site whose genre listing the Web UI can offer.
GENRE_LISTS = {
    "aniworld": fetch_aniworld_genres,
}

GENRE_SITES = tuple(GENRE_LISTS)

# AniWorld is missing here on purpose: the site pages its own genre listing, so
# it goes through fetch_genre_animes(). Everything else returns one flat list
# that limit cuts off, and the caller slices it into pages.
GENRE_QUERIES = {}


def genres(site):
    """The genre chips for a site, as [{name, slug}]."""
    fetch = GENRE_LISTS.get(site)
    return fetch() if fetch else []


def genre_results(site, genre, limit):
    """Browse one site's genre, normalised like a search result.

    Returns (results, has_more). The flag counts the site's own hits, before
    normalising drops the ones the app cannot open afterwards: a site listing
    the odd title under a slug no provider pattern accepts would otherwise
    read as the end of the listing.

    Failures propagate on purpose: an empty genre is worth telling apart from a
    site that would not answer, which a browse row cannot do.
    """
    query = GENRE_QUERIES.get(site)
    if not query:
        return [], False
    raw = query(genre, limit) or []
    if isinstance(raw, dict):
        raw = [raw]
    return _normalise(site, raw), len(raw) >= limit


def aggregate(title, media_type, per_site=8, limit=25):
    """Search every site of a media type and return combined, site-tagged hits."""
    combined = []
    for site in sites_for(media_type):
        for item in search(site, title)[:per_site]:
            combined.append({**item, "site": site})
            if len(combined) >= limit:
                return combined
    return combined
