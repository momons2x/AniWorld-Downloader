"""Search, browse and the series/season/episode endpoints."""

import re
import time

from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from curl_cffi.requests.exceptions import RequestException as CurlRequestException
from flask import Response, jsonify, request
from niquests.exceptions import HTTPError, RequestException

from ...config import DEFAULT_USER_AGENT, GLOBAL_SESSION
from ...logger import get_logger
from ...models.miruro._api import fetch_latest_animes as fetch_miruro_latest
from ...models.nineanime._api import fetch_latest_animes as fetch_nineanime_latest
from ...models.nineanime._api import fetch_popular_animes as fetch_nineanime_popular
from ...models.reanime._api import fetch_latest_animes as fetch_reanime_latest
from ...models.reanime._api import fetch_trending_animes as fetch_reanime_trending
from ...providers import normalize_url, resolve_provider
from ...search import (
    GENRE_PAGE_SIZE,
    fetch_genre_animes,
    fetch_new_animes,
    fetch_popular_animes,
    random_anime,
)
from .. import media, sitesearch
from ..settings_store import english_sub_disabled

logger = get_logger(__name__)

# Browse rows change slowly, so cache them for an hour per process.
BROWSE_TTL = 3600
_browse_cache = {}


def register(bp):
    bp.add_url_rule("/search", view_func=search, methods=["POST"])
    bp.add_url_rule("/series", view_func=series)
    bp.add_url_rule("/seasons", view_func=seasons)
    bp.add_url_rule("/episodes", view_func=episodes)
    bp.add_url_rule("/providers", view_func=providers)
    bp.add_url_rule("/random", view_func=random_title)
    bp.add_url_rule("/proxy-image", view_func=proxy_image)
    bp.add_url_rule("/downloaded-folders", view_func=downloaded_folders)
    bp.add_url_rule("/genres", view_func=genres)
    bp.add_url_rule("/genre", view_func=genre)
    for path, key, fetch in _BROWSE_ROWS:
        bp.add_url_rule(
            path,
            endpoint=f"browse_{key}",
            view_func=_make_browse_view(key, fetch),
        )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    site = (data.get("site") or "aniworld").strip()
    if not keyword:
        return jsonify({"error": "keyword is required"}), 400

    try:
        results = [
            {
                "title": item["title"],
                "url": item["url"],
                "poster_url": media.proxy_image(item["poster"]),
            }
            for item in sitesearch.search(site, keyword, raise_errors=True)
        ]
    except Exception as exc:
        logger.warning("Search on %s failed (%s)", site, type(exc).__name__)
        return jsonify({"error": f"Search on {site} failed. Please try again."}), 502
    return jsonify({"results": results})


# ---------------------------------------------------------------------------
# Series / seasons / episodes
# ---------------------------------------------------------------------------
def _requested_url():
    url = request.args.get("url", "").strip()
    return url or None


def series():
    url = _requested_url()
    if not url:
        return jsonify({"error": "url is required"}), 400

    provider = None
    try:
        url = normalize_url(url)
        provider = resolve_provider(url)
        found = _series_model(provider, url)
        return jsonify(
            {
                "title": found.title,
                "poster_url": media.proxy_image(
                    media.absolute_poster(getattr(found, "poster_url", ""), url)
                ),
                "description": getattr(found, "description", ""),
                "genres": getattr(found, "genres", []),
                "release_year": getattr(found, "release_year", ""),
            }
        )
    except Exception as exc:
        logger.exception("Series fetch failed")
        return jsonify({"error": str(exc)}), 500


def _has_episode_row_languages(provider_name):
    return provider_name in ("9anime", "Miruro", "ReAnime")


def _series_model(provider, url):
    """Whatever the series endpoint reads the title, poster and details from."""
    return provider.series_cls(url=url)


def seasons():
    url = _requested_url()
    if not url:
        return jsonify({"error": "url is required"}), 400

    provider = None
    try:
        url = normalize_url(url)
        provider = resolve_provider(url)
        found = provider.series_cls(url=url)
        return jsonify(
            {
                "seasons": [
                    {
                        "url": season.url,
                        "season_number": season.season_number,
                        "episode_count": season.episode_count,
                        "are_movies": getattr(season, "are_movies", False),
                    }
                    for season in found.seasons
                ]
            }
        )
    except Exception as exc:
        logger.exception("Seasons fetch failed")
        return jsonify({"error": str(exc)}), 500


def episodes():
    url = _requested_url()
    if not url:
        return jsonify({"error": "url is required"}), 400

    series_url = request.args.get("series_url", "").strip() or None
    provider = None
    try:
        url = normalize_url(url)
        series_url = normalize_url(series_url)
        provider = resolve_provider(url)
        return jsonify({"episodes": _season_episodes(provider, url, series_url)})
    except Exception as exc:
        logger.exception("Episodes fetch failed")
        return jsonify({"error": str(exc)}), 500


def _resolve_series(provider, url, series_url):
    """Build the series a season belongs to, so the season model has its context."""
    if series_url is None:
        # Derive the series URL from the season page instead of assuming it.
        series_url = re.sub(r"/staffel-\d+/?$", "", url)
        series_url = re.sub(r"/filme/?$", "", series_url)
    try:
        return provider.series_cls(url=series_url)
    except Exception:
        return None


def _season_episodes(provider, url, series_url):
    found = _resolve_series(provider, url, series_url)

    season = provider.season_cls(url=url, series=found)

    downloaded = media.downloaded_episodes(found) if found else set()

    episode_languages = {}
    fallback_languages = None
    fallback_languages_resolved = False
    if _has_episode_row_languages(provider.name):
        episode_languages = dict(getattr(season, "episode_languages", {}) or {})

    results = []
    for episode in season.episodes:
        if _has_episode_row_languages(provider.name):
            row_languages = episode_languages.get(episode.episode_number)
            if row_languages is not None:
                languages = list(row_languages)
            else:
                # Older pages may omit the row flags. Probe one episode at
                # most and reuse its labels instead of requesting every
                # episode page in the season.
                if not fallback_languages_resolved:
                    try:
                        fallback_languages = media.language_labels(
                            episode.provider_data
                        )
                    except Exception as exc:
                        logger.warning(
                            "%s language detection failed: %s", provider.name, exc
                        )
                        fallback_languages = ["English Sub"]
                    fallback_languages_resolved = True
                languages = fallback_languages
        else:
            languages = media.language_labels(episode.provider_data)

        results.append(
            {
                "url": episode.url,
                "episode_number": episode.episode_number,
                "title_en": getattr(episode, "title_en", ""),
                "downloaded": (episode.season.season_number, episode.episode_number)
                in downloaded,
                "available_languages": languages,
                "page_count": 0,
            }
        )
    return results


def providers():
    url = _requested_url()
    if not url:
        return jsonify({"error": "url is required"}), 400

    try:
        url = normalize_url(url)
        provider = resolve_provider(url)

        episode = provider.episode_cls(url=url)
        return jsonify(
            {
                "providers": media.provider_map(
                    episode.provider_data, drop_english_sub=english_sub_disabled()
                )
            }
        )
    except Exception as exc:
        logger.exception("Providers fetch failed")
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def random_title():
    url = random_anime()
    if url:
        return jsonify({"url": url})
    return jsonify({"error": "Failed to fetch random anime"}), 500


def proxy_image():
    target = request.args.get("url", "").strip()
    if not target.startswith(("http://", "https://")):
        return "", 400
    try:
        # Without a brotli module installed the body would come back still
        # compressed and get passed on as a broken image
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
        response = GLOBAL_SESSION.get(target, headers=headers, timeout=10)
        response.raise_for_status()
        return Response(
            response.content,
            content_type=response.headers.get("Content-Type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception as exc:
        logger.warning("Image proxy failed for %s: %s", target, exc)
        return "", 502


def downloaded_folders():
    return jsonify({"folders": media.downloaded_folder_names()})


# ---------------------------------------------------------------------------
# Genres
# ---------------------------------------------------------------------------
def genres():
    """The genre list for the discover row of whichever site is open."""
    site = (request.args.get("site") or "aniworld").strip()
    if site not in sitesearch.GENRE_SITES:
        return jsonify({"genres": []})
    results = _cached(f"genres:{site}", lambda: sitesearch.genres(site))
    if not results:
        return jsonify({"error": f"Failed to fetch genres for {site}"}), 500
    return jsonify({"genres": results})


def _genre_page(site, slug, page):
    """One page worth of a genre listing, plus whether another one follows.

    AniWorld pages its own genre listing. The other sites hand back a single
    flat list, so ask for one title more than the page needs: that both fills
    the page and settles whether there is anything behind it.
    """
    if site == "aniworld":
        return fetch_genre_animes(slug, page)

    end = page * GENRE_PAGE_SIZE
    results, has_more = sitesearch.genre_results(site, slug, end + 1)
    return {
        "results": [
            {"title": item["title"], "url": item["url"], "poster_url": item["poster"]}
            for item in results[end - GENRE_PAGE_SIZE : end]
        ],
        "has_more": has_more,
    }


def genre():
    """One page of a genre listing, 30 titles per page."""
    site = (request.args.get("site") or "aniworld").strip()
    slug = (request.args.get("slug") or "").strip()
    if not slug or any(char in slug for char in "/\\") or slug in {".", ".."}:
        return jsonify({"error": "Invalid genre slug"}), 400
    if site not in sitesearch.GENRE_SITES:
        return jsonify({"error": f"No genre listing for {site}"}), 400

    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        return jsonify({"error": "page must be a number"}), 400

    try:
        data = _cached(
            f"genre:{site}:{slug}:{page}",
            lambda: _genre_page(site, slug, page),
            raise_errors=True,
        )
    except (HTTPError, CurlHTTPError) as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return jsonify({"error": f"Genre not available: {slug}"}), 404
        return jsonify({"error": f"Failed to fetch genre {slug}"}), 502
    except (RequestException, CurlRequestException):
        return jsonify({"error": f"Failed to fetch genre {slug}"}), 502
    except ValueError:
        # Sites that know their genres up front reject an unknown one outright
        return jsonify({"error": f"Genre not available: {slug}"}), 404
    if data is None:
        return jsonify({"error": f"Failed to fetch genre {slug}"}), 500

    return jsonify(
        {
            "results": [
                {**item, "poster_url": media.proxy_image(item.get("poster_url", ""))}
                for item in data["results"]
            ],
            "has_more": data["has_more"],
            "page": page,
        }
    )


# ---------------------------------------------------------------------------
# Browse rows
# ---------------------------------------------------------------------------
_BROWSE_ROWS = (
    ("/new-animes", "new_animes", fetch_new_animes),
    ("/popular-animes", "popular_animes", fetch_popular_animes),
    ("/nineanime-latest", "nineanime_latest", fetch_nineanime_latest),
    ("/nineanime-popular", "nineanime_popular", fetch_nineanime_popular),
    ("/miruro-latest", "miruro_latest", fetch_miruro_latest),
    ("/reanime-latest", "reanime_latest", fetch_reanime_latest),
    ("/reanime-trending", "reanime_trending", fetch_reanime_trending),
)


def _cached(key, fetch, *, raise_errors=False):
    now = time.time()
    entry = _browse_cache.get(key)
    if entry and now - entry[0] < BROWSE_TTL:
        return entry[1]
    try:
        results = fetch()
    except Exception as exc:
        if raise_errors:
            raise
        logger.warning("Browse fetch '%s' failed: %s", key, exc)
        return None
    if results is not None:
        _browse_cache[key] = (now, results)
    return results


def _make_browse_view(key, fetch):
    def view():
        try:
            results = _cached(key, fetch, raise_errors=True)
        except Exception as exc:
            logger.warning("Browse fetch '%s' failed (%s)", key, type(exc).__name__)
            results = None
        if results is None:
            return jsonify({"error": f"Failed to fetch {key.replace('_', ' ')}"}), 500
        return jsonify(
            {
                "results": [
                    {
                        **item,
                        "poster_url": media.proxy_image(item.get("poster_url", "")),
                    }
                    for item in results
                ]
            }
        )

    view.__name__ = f"browse_{key}"
    return view
