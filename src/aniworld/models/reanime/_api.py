"""Re:Anime (reanime.to) API client.

Series metadata comes from the SvelteKit `__data.json` endpoint and the
public search API; episode streams come from `/api/flix/{anilist}/{episode}`,
which hands out flixcloud.cc embed links. The embed pages encrypt their
playback URLs with a WebAssembly helper, so `extractors/provider/reanime.py`
runs the bundled Node.js sidecar to decrypt them.
"""

import json
import re

from aniworld.config import GLOBAL_SESSION


def _resolve_devalue(array, value, depth=0):
    """Follow devalue references inside one node's data array."""
    if depth > 10:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(array):
            return _resolve_devalue(array, array[value], depth + 1)
        return value
    if isinstance(value, list):
        return [_resolve_devalue(array, item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {
            key: _resolve_devalue(array, item, depth + 1)
            for key, item in value.items()
        }
    return value


BASE_URL = "https://reanime.to"
FLIXCLOUD_BASE = "https://flixcloud.cc"


def slug_for(anime_id, title=""):
    anime_id = str(anime_id or "")
    if anime_id and not anime_id.isdigit():
        return anime_id  # already a slug (Re:Anime ids are slugs)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title or "").strip("-").lower()
    return f"{anime_id}-{slug}" if slug else anime_id


def series_url_for(anime_id, title=""):
    return f"{BASE_URL}/anime/{slug_for(anime_id, title)}"


def episode_url_for(anime_id, title, number):
    return f"{BASE_URL}/watch/{slug_for(anime_id, title)}?ep={number}"


def anime_id_from_url(url):
    match = re.search(r"/(?:anime|watch)/([A-Za-z0-9\-_]+)", url or "")
    return match.group(1) if match else None


def episode_from_url(url):
    match = re.search(r"[?&]ep=(\d+)", url or "")
    return int(match.group(1)) if match else None


def api_get(path, params=None):
    response = GLOBAL_SESSION.get(
        f"{BASE_URL}{path}",
        params=params,
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def search_animes(keyword, limit=5):
    data = api_get("/api/v1/search", params={"q": keyword.strip(), "limit": limit})
    return data.get("results") or []


def _card(item):
    """One catalogue record as {title, url, poster_url}, or None if unusable."""
    if not isinstance(item, dict):
        return None
    title_block = item.get("title") or {}
    title = (
        title_block.get("english")
        or title_block.get("romaji")
        or title_block.get("native")
        or title_block.get("user_preferred")
        or ""
    )
    anime_id = item.get("anime_id")
    if not title or not anime_id:
        return None
    cover = item.get("cover_image") or {}
    return {
        "title": title,
        "url": series_url_for(anime_id, title),
        "poster_url": cover.get("extra_large") or cover.get("large") or "",
    }


def _cards(items, limit):
    results = []
    for item in items:
        card = _card(item)
        if card:
            results.append(card)
        if len(results) >= limit:
            break
    return results


def query_animes(keyword, limit=24):
    """Search the catalogue, returning [{title, url, poster_url}]."""
    return _cards(search_animes(keyword, limit=limit), limit)


def _home_list(key, limit=24):
    """One front-page list, read off the root SvelteKit data payload."""
    payload = api_get("/__data.json")
    if not isinstance(payload, dict):
        return []
    for node in payload.get("nodes") or []:
        data = node.get("data")
        if not isinstance(data, list) or not data:
            continue
        root = _resolve_devalue(data, data[0])
        if not isinstance(root, dict):
            continue
        items = root.get(key)
        if isinstance(items, list) and items:
            return _cards(items, limit)
    return []


def fetch_latest_animes(limit=24):
    """Titles whose newest episode has just aired, as the homepage orders them."""
    return _home_list("latestAired", limit)


def fetch_trending_animes(limit=24):
    """The titles the site is pushing hardest right now."""
    return _home_list("trending", limit)


def search_exact(keyword, anime_id):
    for item in search_animes(keyword):
        if item.get("anime_id") == anime_id:
            return item
    return None


def fetch_meta(anime_id):
    return api_get(f"/api/v1/anime/{anime_id}/meta")


def flix_servers(anilist_id, episode_number, mal_id=None):
    """Embed servers for one episode, e.g. [{serverName, dataLink, dataType}]."""
    params = {}
    if mal_id:
        params["mal"] = mal_id
    data = api_get(f"/api/flix/{anilist_id}/{episode_number}", params=params or None)
    servers = data.get("servers") or []
    return [server for server in servers if server.get("dataLink")]


def parse_subtitles(html):
    """Subtitle entries from an embed page: [{url, language, default}]."""
    entries = []
    for url, language, is_default in re.findall(
        r'\{\s*url:\s*"([^"]+)",\s*language:\s*"([^"]+)",\s*format:\s*"[^"]*",\s*default:\s*(true|false)\s*\}',
        html or "",
    ):
        entries.append(
            {
                "url": url,
                "language": language,
                "default": is_default == "true",
            }
        )
    return entries


def fetch_anime_record(anime_id):
    """The full anime record from the series page's SvelteKit data endpoint."""
    response = GLOBAL_SESSION.get(
        f"{BASE_URL}/anime/{anime_id}/__data.json",
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "x-sveltekit-invalidated": "001",
        },
        timeout=30,
    )
    response.raise_for_status()
    payloads = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payloads.append(json.loads(line))
        except ValueError:
            continue
    for payload in payloads:
        for node in payload.get("nodes") or []:
            data = node.get("data") if isinstance(node, dict) else None
            if not data:
                continue
            for value in data:
                if not isinstance(value, dict) or "anime_id" not in value:
                    continue
                record = _resolve_devalue(data, value)
                if isinstance(record, dict) and str(record.get("anime_id")) == anime_id:
                    return record
    return {}


def fetch_embed_page(embed_url):
    response = GLOBAL_SESSION.get(
        embed_url,
        headers={"Referer": f"{BASE_URL}/", "Accept-Encoding": "identity"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text
