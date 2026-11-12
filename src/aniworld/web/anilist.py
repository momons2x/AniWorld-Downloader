"""AniList metadata for library titles (posters, scores, synopsis).

The folder name on disk is what we search with: "Naruto (2002-2007)
[imdbid-tt0409591]" -> "Naruto". Results are cached in the app's SQLite
database (table `library_meta`, keyed by the folder name) so the library
page stays quick and only re-queries when a title is missing.
"""

import re

from ..logger import get_logger
from . import db

logger = get_logger(__name__)

ANILIST_URL = "https://graphql.anilist.co"

QUERY = """
query($t: String) { Media(search: $t, type: ANIME) {
  id title { english romaji }
  format status episodes seasonYear averageScore genres description
  coverImage { large } bannerImage
}}
"""


IMDB_ID_RE = re.compile(r"\s*\[imdbid-(?:tt)?\d+\]\s*$")


def clean_search_title(folder):
    """Turn a library folder name into an AniList search query."""
    name = str(folder or "")
    name = IMDB_ID_RE.sub("", name)
    name = re.sub(r"\s*\((?:19|20)\d{2}(?:-(?:19|20)\d{2})?\)\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or str(folder or "")


def _parse(media):
    if not media:
        return None
    title = media.get("title") or {}
    cover = (media.get("coverImage") or {}).get("large") or ""
    return {
        "anilist_id": media.get("id"),
        "title_en": title.get("english") or title.get("romaji") or "",
        "title_romaji": title.get("romaji") or "",
        "format": media.get("format") or "",
        "status": media.get("status") or "",
        "episodes": media.get("episodes"),
        "year": media.get("seasonYear"),
        "score": media.get("averageScore"),
        "genres": media.get("genres") or [],
        "description": re.sub(r"<[^>]+>", "", media.get("description") or "")[:600],
        "cover": cover,
        "banner": media.get("bannerImage") or "",
    }


def fetch_meta(folder):
    """Live AniList lookup for one folder. None when no match or offline."""
    query_title = clean_search_title(folder)
    if not query_title:
        return None
    # A fresh connection per call: a single wedged request must not block
    # every other thread sharing the global download session.
    import niquests

    try:
        resp = niquests.post(
            ANILIST_URL,
            json={"query": QUERY, "variables": {"t": query_title}},
            headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
            timeout=15,
        )
        resp.raise_for_status()
        media = ((resp.json() or {}).get("data") or {}).get("Media")
    except Exception as exc:
        logger.warning("AniList lookup failed for %r: %s", folder, exc)
        return None
    return _parse(media)


def get_meta(folder, *, refresh=False):
    """Cached metadata for a folder; fetches (and caches) on a miss."""
    folder = str(folder or "")
    if not folder:
        return None
    if not refresh:
        cached = db.get_library_meta(folder)
        if cached is not None:
            return cached
    meta = fetch_meta(folder)
    if meta is not None:
        db.set_library_meta(folder, meta)
    return meta
