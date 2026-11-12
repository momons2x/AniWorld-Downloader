"""AniList-backed discovery endpoints for the My Anime tab."""

from flask import jsonify, request

from ...logger import get_logger
from .. import discover
from ..settings_store import library_enabled

logger = get_logger(__name__)


def register(bp):
    bp.add_url_rule("/discover", view_func=discover_list)
    bp.add_url_rule("/discover/anime/<int:anilist_id>", view_func=discover_anime)


def discover_list():
    if not library_enabled():
        return jsonify({"results": [], "page": 1, "last_page": 1, "has_next": False})
    kind = (request.args.get("kind") or "trending").strip()
    genre = (request.args.get("genre") or "").strip() or None
    tag = (request.args.get("tag") or "").strip() or None
    search = (request.args.get("q") or "").strip() or None
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        result = discover.list_anime(
            kind=kind, genre=genre, page=page, search=search, tag=tag
        )
    except Exception as exc:
        logger.warning("Discover list failed: %s", exc)
        return jsonify({"error": f"Discovery failed: {exc}"}), 502
    return jsonify(result)


def discover_anime(anilist_id):
    if not library_enabled():
        return jsonify({"error": "Not found"}), 404
    try:
        item = discover.get_anime(int(anilist_id))
    except Exception as exc:
        logger.warning("Discover details failed: %s", exc)
        return jsonify({"error": f"Discovery failed: {exc}"}), 502
    if item is None:
        return jsonify({"error": "No metadata found"}), 404
    return jsonify(item)
