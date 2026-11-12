"""Miruro (miruro.tv) API client.

The site is a SvelteKit app whose JSON API sits behind Cloudflare's
interstitial, so every call impersonates a Safari browser (the same trick the
VOE extractor uses). Pages themselves (`__data.json`) carry the series data
in SvelteKit's devalue format.
"""

import json
import re
import time

from curl_cffi import requests

BASE_URL = "https://miruro.tv"
IMPERSONATE = "safari"


def _session():
    session = requests.Session(impersonate=IMPERSONATE)
    return session


def api_get(path, params=None):
    """GET a Miruro JSON endpoint, returning the decoded body."""
    headers = {"Accept": "application/json", "Referer": f"{BASE_URL}/"}
    last_error = None
    for attempt in range(2):
        session = _session()
        try:
            response = session.get(
                f"{BASE_URL}{path}", params=params, headers=headers, timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                # An immediate repeat can draw a bare "Unsupported browse
                # request" from the CDN; a fresh session a second later clears
                # it, so one retry is cheap insurance.
                time.sleep(1.0)
    raise last_error


def slug_for(media_id, title):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title or "").strip("-").lower()
    return f"{media_id}/{slug}" if slug else str(media_id)


def series_url_for(media_id, title=""):
    return f"{BASE_URL}/info/{slug_for(media_id, title)}"


def episode_url_for(media_id, title, number):
    return f"{BASE_URL}/watch/{slug_for(media_id, title)}/episode/{number}"


def media_id_from_url(url):
    match = re.search(r"/(?:info|watch)/([A-Za-z0-9\-_]+)", url or "")
    return match.group(1) if match else None


def search_animes(keyword, limit=8):
    """Search the catalogue, returning [{id, title, english_title, cover_url, ...}].

    The browse endpoint only ever answers with a single page of eight hits and
    rejects every other limit, so that is the page requested and `limit` just
    trims how many of them the caller sees.
    """
    data = api_get(
        "/api/search/browse",
        params={"q": keyword.strip(), "limit": 8, "type": "ANIME"},
    )
    return (data.get("data") or [])[:limit]


def _card(item):
    """One catalogue record as {title, url, poster_url}, or None if unusable."""
    if not isinstance(item, dict):
        return None
    title_block = item.get("title") or {}
    title = (
        title_block.get("english")
        or title_block.get("romaji")
        or title_block.get("native")
        or ""
    )
    media_id = item.get("id")
    if not title or not media_id:
        return None
    return {
        "title": title,
        "url": series_url_for(media_id, title),
        "poster_url": item.get("cover_url")
        or item.get("coverUrl")
        or item.get("logo_url")
        or "",
    }


def query_animes(keyword, limit=24):
    """Search the catalogue, returning [{title, url, poster_url}]."""
    results = []
    for item in search_animes(keyword, limit=limit):
        card = _card(item)
        if card:
            results.append(card)
    return results


def search_exact(keyword, media_id):
    """The search hit whose id matches, with reliable episode counts."""
    for item in search_animes(keyword):
        if item.get("id") == media_id:
            return item
    return None


def _resolve_devalue(array, value, depth=0):
    """Follow devalue index references inside one node's data array."""
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


def _home_data_nodes():
    """The SvelteKit data nodes behind the homepage, layout page included.

    The body arrives newline delimited: a `data` line carrying the nodes plus
    one `chunk` line per deferred promise.
    """
    session = _session()
    response = session.get(
        f"{BASE_URL}/__data.json",
        headers={"Accept": "application/json", "Referer": f"{BASE_URL}/"},
        timeout=30,
    )
    response.raise_for_status()
    nodes = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "data":
            nodes.extend(payload.get("nodes") or [])
    return nodes


def fetch_latest_animes(limit=24):
    """The homepage's front rail: the series Miruro is putting forward.

    The homepage splits its records into a hero rail, which spells every title
    out, and side rails that only carry logo artwork. Cards need a readable
    name, so the hero is the one that can be shown.
    """
    results = []
    for node in _home_data_nodes():
        data = node.get("data")
        if not isinstance(data, list) or not data:
            continue
        root = _resolve_devalue(data, data[0])
        if not isinstance(root, dict):
            continue
        hero = root.get("hero")
        if not isinstance(hero, list):
            continue
        for item in hero:
            card = _card(item)
            if card:
                results.append(card)
            if len(results) >= limit:
                return results
    return results


def fetch_info(media_id):
    """The full series record from the info page's SvelteKit data endpoint."""
    session = _session()
    url = f"{BASE_URL}/info/{media_id}/__data.json"
    response = session.get(
        url,
        headers={"Accept": "application/json", "Referer": f"{BASE_URL}/"},
        timeout=30,
    )
    response.raise_for_status()
    try:
        first = json.loads(response.text.splitlines()[0]) if response.text else {}
    except ValueError:
        first = {}
    if isinstance(first, dict) and first.get("type") == "redirect":
        location = first.get("location") or ""
        url = f"{BASE_URL}{location}/__data.json" if location.startswith("/") else location
        response = session.get(
            url,
            headers={"Accept": "application/json", "Referer": f"{BASE_URL}/"},
            timeout=30,
        )
        response.raise_for_status()

    nodes = []
    chunks = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("type") == "data":
            nodes.extend(payload.get("nodes") or [])
        elif payload.get("type") == "chunk":
            chunks.append(payload)
    if len(nodes) < 2:
        raise ValueError(f"No series data found for {media_id}")

    array = list(nodes[1].get("data") or [])
    for chunk in sorted(chunks, key=lambda item: item.get("id") or 0):
        array.extend(chunk.get("data") or [])

    for value in array:
        if not isinstance(value, dict):
            continue
        if "episode_count" not in value or "external_ids" not in value:
            continue
        media = _resolve_devalue(array, value)
        if isinstance(media, dict) and media.get("id") == media_id:
            return media
    # Fall back to the first record with the media shape.
    for value in array:
        if not isinstance(value, dict):
            continue
        if "episode_count" in value and "external_ids" in value:
            media = _resolve_devalue(array, value)
            if isinstance(media, dict) and media.get("id") == media_id:
                return media
    raise ValueError(f"No series data found for {media_id}")


def fetch_sources(media_id, episode_number):
    """Episode sources: tracks of providers, each with servers and HLS streams."""
    return api_get("/api/sources", params={"id": media_id, "n": str(episode_number)})


def track_language(track_name):
    name = str(track_name or "").lower()
    if "dub" in name:
        return "English Dub"
    if "sub" in name:
        return "English Sub"
    return None



