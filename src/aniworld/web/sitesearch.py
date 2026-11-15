"""Site search plumbing shared by the web search API and the Discord bot.

Each site's search returns a slightly different shape, this normalises them all
to {title, url, poster} so callers never have to care which site answered.
"""

from ..logger import get_logger
from ..models.miruro._api import query_animes as query_miruro
from ..models.nineanime._api import fetch_genres
from ..models.nineanime._api import query_animes as query_nineanime
from ..models.reanime._api import query_animes as query_reanime

logger = get_logger(__name__)

# Each site searches through its own model package; the functions all return
# [{title, url, poster_url}] so the normaliser below can pass them straight on.
SITE_SEARCH = {
    "nineanime": query_nineanime,
    "miruro": query_miruro,
    "reanime": query_reanime,
}

# Sites checked for a Discord request, in priority order.
SERIES_SITES = ("nineanime", "miruro", "reanime")
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


def _normalise(raw, fallback=""):
    """Turn one site's raw hits into [{title, url, poster}], dropping the rest."""
    if isinstance(raw, dict):
        raw = [raw]

    results = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _resolve_url(item)
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
    return _normalise(raw, keyword)


def _resolve_url(item):
    """A hit's series URL, or None when the site only offered a relative link."""
    return item.get("url") or item.get("link")


# Every site whose genre listing the Web UI can offer. 9anime pages its own
# genre URLs; the other two have no genre backend of their own.
GENRE_LISTS = {
    "nineanime": fetch_genres,
}

GENRE_SITES = tuple(GENRE_LISTS)


def genres(site):
    """The genre chips for a site, as [{name, slug}]."""
    fetch = GENRE_LISTS.get(site)
    return fetch() if fetch else []


def aggregate(title, media_type, per_site=8, limit=25):
    """Search every site of a media type and return combined, site-tagged hits."""
    combined = []
    for site in sites_for(media_type):
        for item in search(site, title)[:per_site]:
            combined.append({**item, "site": site})
            if len(combined) >= limit:
                return combined
    return combined
