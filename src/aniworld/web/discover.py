"""AniList-backed discovery lists for the My Anime tab.

The public AniList GraphQL API powers the Discover mode of the My Anime tab:
trending / this-season / top-rated / random lists plus a details record per
anime. Data is fetched live (no cache) and kept small (<= 24 per page).
"""

from datetime import date

from ..logger import get_logger

logger = get_logger(__name__)

ANILIST_URL = "https://graphql.anilist.co"

# The official AniList genre list; the API requires exact casing.
DISCOVER_GENRES = (
    "Action",
    "Adventure",
    "Comedy",
    "Drama",
    "Ecchi",
    "Fantasy",
    "Horror",
    "Mahou Shoujo",
    "Mecha",
    "Music",
    "Mystery",
    "Psychological",
    "Romance",
    "Sci-Fi",
    "Slice of Life",
    "Sports",
    "Supernatural",
    "Thriller",
)

# Popular AniList tags offered as quick filters alongside the genres (the API
# exposes these as the `tag` argument, not as genres; casing must match).
DISCOVER_TAGS = (
    "Isekai",
    "Reincarnation",
    "Detective",
    "Vampire",
    "Zombie",
    "Cyberpunk",
    "Female Protagonist",
    "Male Protagonist",
    "Super Power",
    "Space",
    "Survival",
    "Dystopian",
    "Post-Apocalyptic",
    "Tragedy",
    "Military",
    "School",
    "Magic",
    "Historical",
    "Educational",
    "Found Family",
    "Gore",
    "Boys' Love",
    "Yuri",
    "Cute Girls Doing Cute Things",
    "Otaku Culture",
    "Cosplay",
    "Idol",
    "Food",
    "Travel",
    "Photography",
    "School Club",
    "Martial Arts",
    "Boxing",
    "Cycling",
    "Baseball",
    "Football",
    "Tennis",
    "Swimming",
    "Camping",
    "Fishing",
    "Astronomy",
    "Writing",
    "Fashion",
    "Robots",
    "Artificial Intelligence",
    "Gambling",
    "Shogi",
    "Poker",
    "Mafia",
    "Yakuza",
    "Religion",
    "Cult",
)

DISCOVER_KINDS = ("trending", "season", "top", "random")

_FIELDS = (
    "id title { english romaji native } genres coverImage { extraLarge large }"
    " averageScore seasonYear episodes status format duration"
)
_DETAILS_FIELDS = (
    "id title { english romaji native } genres coverImage { extraLarge large }"
    " bannerImage averageScore seasonYear episodes status format duration"
    " description externalLinks { id site url }"
)

_SORTS = {
    "trending": "TRENDING_DESC",
    "season": "POPULARITY_DESC",
    "top": "SCORE_DESC",
    "random": "POPULARITY_DESC",
}


def current_season(today=None):
    """Current AniList season window, e.g. ("FALL", 2026)."""
    today = today or date.today()
    month = today.month
    if month in (12, 1, 2):
        return "WINTER", today.year + (1 if month == 12 else 0)
    if month in (3, 4, 5):
        return "SPRING", today.year
    if month in (6, 7, 8):
        return "SUMMER", today.year
    return "FALL", today.year


def _post(query, variables):
    import niquests

    response = niquests.post(
        ANILIST_URL,
        json={"query": query, "variables": variables},
        headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _clean(text):
    import re

    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _summarize(media):
    title = media.get("title") or {}
    cover = media.get("coverImage") or {}
    return {
        "anilist_id": media.get("id"),
        "title": title.get("english") or title.get("romaji") or "",
        "title_romaji": title.get("romaji") or "",
        "cover": cover.get("extraLarge") or cover.get("large") or "",
        "year": media.get("seasonYear"),
        "genres": media.get("genres") or [],
        "score": media.get("averageScore"),
        "episodes": media.get("episodes"),
        "status": media.get("status") or "",
        "format": media.get("format") or "",
    }


def _summarize_details(media):
    item = _summarize(media)
    title = media.get("title") or {}
    item.update(
        {
            "title_native": title.get("native") or "",
            "description": _clean(media.get("description") or ""),
            "banner": media.get("bannerImage") or "",
            "duration": media.get("duration"),
            "external_links": media.get("externalLinks") or [],
        }
    )
    return item


def _media_args(kind, genre, search=None, tag=None):
    args = ['type: ANIME', f"sort: {_SORTS[kind]}", "isAdult: false"]
    if genre:
        args.append(f'genre: "{genre}"')
    if tag:
        args.append(f'tag: "{tag}"')
    if search:
        args.append(f'search: "{search}"')
    return ", ".join(args)


def list_anime(kind="trending", genre=None, page=1, per_page=24, search=None, tag=None):
    """One discovery page: {results, page, last_page, has_next}."""
    if kind not in DISCOVER_KINDS:
        kind = "trending"
    if genre and genre not in DISCOVER_GENRES:
        genre = None
    if tag and tag not in DISCOVER_TAGS:
        tag = None
    if search:
        search = str(search).strip()[:80] or None
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 24), 50))

    if kind == "random" and not search:
        return _random_page(genre, per_page, tag=tag)

    args = _media_args(kind, genre, search=search, tag=tag)
    if kind == "season":
        season_name, year = current_season()
        args += f", season: {season_name}, seasonYear: {year}"
    query = f"""query($page: Int, $perPage: Int) {{ Page(page: $page, perPage: $perPage) {{ pageInfo {{ total currentPage lastPage hasNextPage }} media({args}) {{ {_FIELDS} }} }} }}"""
    data = (_post(query, {"page": page, "perPage": per_page}) or {}).get("data") or {}
    page_data = data.get("Page") or {}
    info = page_data.get("pageInfo") or {}
    return {
        "results": [_summarize(m) for m in page_data.get("media") or []],
        "page": page,
        "last_page": info.get("lastPage") or page,
        "has_next": bool(info.get("hasNextPage")),
    }


def _random_page(genre, per_page, tag=None):
    import random

    args = _media_args("random", genre, tag=tag)
    probe_query = f"""query {{ Page(page: 1, perPage: 1) {{ pageInfo {{ lastPage }} media({args}) {{ id }} }} }}"""
    probe = (_post(probe_query, {}) or {}).get("data") or {}
    last_page = ((probe.get("Page") or {}).get("pageInfo") or {}).get("lastPage") or 1
    page = random.randint(1, max(1, last_page))
    query = f"""query($page: Int, $perPage: Int) {{ Page(page: $page, perPage: $perPage) {{ pageInfo {{ total currentPage lastPage hasNextPage }} media({args}) {{ {_FIELDS} }} }} }}"""
    data = (_post(query, {"page": page, "perPage": per_page}) or {}).get("data") or {}
    page_data = data.get("Page") or {}
    info = page_data.get("pageInfo") or {}
    return {
        "results": [_summarize(m) for m in page_data.get("media") or []],
        "page": page,
        "last_page": info.get("lastPage") or page,
        "has_next": bool(info.get("hasNextPage")),
    }


def get_anime(anilist_id):
    """Full details record for the details panel, or None."""
    if not anilist_id:
        return None
    query = f"""query($id: Int) {{ Media(id: $id, type: ANIME) {{ {_DETAILS_FIELDS} }} }}"""
    data = (_post(query, {"id": int(anilist_id)}) or {}).get("data") or {}
    media = data.get("Media")
    if not media:
        return None
    return _summarize_details(media)
