"""Library endpoints. Each one loads exactly one level of the tree."""

import re

from flask import Response, abort, jsonify, request, send_file

from ...logger import get_logger
from .. import anilist, db, library, playback
from ..settings_store import library_enabled

logger = get_logger(__name__)


def register(bp):
    bp.add_url_rule("/library/locations", view_func=library_locations)
    bp.add_url_rule("/library/titles", view_func=library_titles)
    bp.add_url_rule("/library/title", view_func=library_title)
    bp.add_url_rule("/library/delete", view_func=delete_library_item, methods=["POST"])
    bp.add_url_rule("/library/hide", view_func=hide_library_item, methods=["POST"])
    bp.add_url_rule("/library/meta", view_func=library_meta)
    bp.add_url_rule("/library/play", view_func=library_play, methods=["POST"])
    bp.add_url_rule("/library/play/status", view_func=library_play_status)
    bp.add_url_rule(
        "/library/play/cancel", view_func=library_play_cancel, methods=["POST"]
    )
    bp.add_url_rule("/library/stream", view_func=library_stream)
    bp.add_url_rule("/library/subtitles", view_func=library_subtitles)
    bp.add_url_rule("/library/progress", view_func=library_progress, methods=["POST"])


def _guard():
    if not library_enabled():
        abort(404)


def _location_args():
    path_id = request.args.get("path_id", "").strip()
    lang_folder = request.args.get("lang_folder", "").strip() or None
    return (int(path_id) if path_id.isdigit() else None), lang_folder


def library_locations():
    _guard()
    return jsonify(library.list_locations())


def library_titles():
    _guard()
    path_id, lang_folder = _location_args()
    include_hidden = (request.args.get("include_hidden") or "").strip() == "1"
    try:
        return jsonify(
            {
                "titles": library.list_titles_with_meta(
                    path_id, lang_folder, include_hidden=include_hidden
                )
            }
        )
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400


def library_title():
    _guard()
    path_id, lang_folder = _location_args()
    folder = request.args.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "folder is required"}), 400
    try:
        return jsonify(library.read_title(folder, path_id, lang_folder))
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400


def hide_library_item():
    _guard()
    data = request.get_json(silent=True) or {}
    folder = data.get("folder", "")
    if not folder:
        return jsonify({"error": "folder is required"}), 400
    try:
        hidden = library.set_hidden(
            folder,
            data.get("hidden", True),
            custom_path_id=data.get("custom_path_id"),
            lang_folder=data.get("lang_folder"),
        )
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "hidden": hidden})


def delete_library_item():
    _guard()
    data = request.get_json(silent=True) or {}
    folder = data.get("folder", "")
    if not folder:
        return jsonify({"error": "folder is required"}), 400

    try:
        deleted = library.delete(
            folder,
            season=data.get("season"),
            episode=data.get("episode"),
            custom_path_id=data.get("custom_path_id"),
            lang_folder=data.get("lang_folder"),
        )
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "deleted": deleted})


def library_meta():
    _guard()
    folder = request.args.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "folder is required"}), 400
    refresh = (request.args.get("refresh") or "").strip() == "1"
    meta = anilist.get_meta(folder, refresh=refresh)
    if meta is None:
        return jsonify({"error": "No metadata found"}), 404
    return jsonify(meta)


def _subtitle_url(token, track_index):
    return (
        f"/api/library/subtitles?token={token}&track={int(track_index)}"
    )


def _playback_payload(token, status):
    entry = playback.session(token)
    source = entry["source"]
    payload = {
        "status": status,
        "token": token,
        "label": source.name,
        "source": str(source),
        "mime": entry["mime"],
        "duration": entry["duration"],
        "mode": entry["mode"],
        "selected_audio": entry["selected_audio"],
        "audio_tracks": entry["audio_tracks"],
        "subtitle_tracks": [
            {**track, "url": _subtitle_url(token, track["index"])}
            for track in entry["subtitle_tracks"]
        ],
    }
    return payload


def library_play():
    _guard()
    data = request.get_json(silent=True) or {}
    folder = data.get("folder", "")
    if not folder or data.get("season") is None or data.get("episode") is None:
        return jsonify({"error": "folder, season and episode are required"}), 400
    extra = {}
    if data.get("audio") is not None:
        try:
            extra["audio_ordinal"] = int(data["audio"])
        except (TypeError, ValueError):
            return jsonify({"error": "invalid audio track"}), 400
    if data.get("force_transcode"):
        extra["force_transcode"] = True
    try:
        result = playback.start_async(
            folder,
            data["season"],
            data["episode"],
            custom_path_id=data.get("custom_path_id"),
            lang_folder=data.get("lang_folder"),
            **extra,
        )
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Playback start failed")
        return jsonify({"error": str(exc) or "Playback failed"}), 500

    if result["status"] in ("ready", "streaming"):
        payload = _playback_payload(result["token"], result["status"])
        if result["status"] == "streaming":
            payload["job"] = result["job"]
        return jsonify(payload)
    return jsonify({"status": "preparing", "job": result["job"]}), 202


def library_play_status():
    _guard()
    job_id = (request.args.get("job") or "").strip()
    if not job_id:
        return jsonify({"error": "job is required"}), 400
    try:
        result = playback.job_status(job_id)
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400
    if result["status"] == "ready":
        payload = _playback_payload(result["token"], "ready")
        payload["job"] = job_id
        payload["duration"] = result.get("duration", payload["duration"])
        return jsonify(payload)
    return jsonify(result)


def library_play_cancel():
    _guard()
    data = request.get_json(silent=True) or {}
    job_id = (data.get("job") or "").strip()
    if not job_id:
        return jsonify({"error": "job is required"}), 400
    try:
        return jsonify(playback.cancel_job(job_id))
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400


def library_stream():
    _guard()
    token = (request.args.get("token") or "").strip()
    if not token:
        return jsonify({"error": "token is required"}), 400
    try:
        entry = playback.session(token)
        path = entry["path"]
        ranges = playback.growing_ranges(path)
        start_byte = _range_start()
        if ranges is not None and start_byte == 0:
            playback.prepare_range(path, 0)
        elif ranges is None:
            playback.prepare_range(path, start_byte)
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400
    if not path.exists() or not path.is_file():
        return jsonify({"error": "Video file is gone"}), 404
    label = entry["source"].name.replace('"', "")
    if ranges is not None:
        return _growing_stream(path, entry, ranges, label)
    response = send_file(
        str(path),
        conditional=True,
        mimetype=entry["mime"] or "video/mp4",
        as_attachment=False,
    )
    response.headers["Content-Disposition"] = f'inline; filename="{label}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Accept-Ranges"] = "bytes"
    return response


def _range_start():
    raw = request.headers.get("Range") or ""
    match = re.match(r"bytes=(\d+)-", raw)
    return int(match.group(1)) if match else 0


def _growing_stream(path, entry, ranges, label):
    """Serve a cache file whose global sidx is still a placeholder.

    The response has no Content-Length and follows the file as ffmpeg writes,
    so the browser keeps reading instead of treating the current prefix as the
    whole video. Range requests are disabled while the file is incomplete.
    """
    size = playback.virtual_size(path, ranges)
    raw = request.headers.get("Range") or ""
    match = re.match(r"bytes=(\d+)-", raw)
    start = int(match.group(1)) if match else 0
    if start > 0 or start >= size:
        response = jsonify({"error": "Range not satisfiable"})
        response.status_code = 416
        response.headers["Content-Range"] = f"bytes */{size}"
        return response
    response = Response(
        playback.iter_growing_range(path, ranges, 0),
        status=200,
        mimetype=entry["mime"] or "video/mp4",
        direct_passthrough=True,
    )
    response.headers["Content-Disposition"] = f'inline; filename="{label}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Accept-Ranges"] = "none"
    response.headers["Cache-Control"] = "no-store"
    return response


def library_subtitles():
    _guard()
    token = (request.args.get("token") or "").strip()
    track = (request.args.get("track") or "").strip()
    if not token or not track:
        return jsonify({"error": "token and track are required"}), 400
    try:
        entry = playback.session(token)
        path = playback.ensure_subtitle(entry, track)
    except library.LibraryError as exc:
        return jsonify({"error": str(exc)}), 400
    response = send_file(
        str(path),
        conditional=True,
        mimetype="text/vtt",
        as_attachment=False,
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


def library_progress():
    """Save the last watched position of one library episode."""
    _guard()
    data = request.get_json(silent=True) or {}
    folder = data.get("folder")
    season = data.get("season")
    episode = data.get("episode")
    position = data.get("position")
    if not folder or season is None or episode is None or position is None:
        return jsonify({"error": "folder, season, episode and position are required"}), 400
    try:
        episode_number = int(episode)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid episode"}), 400
    db.set_playback_position(folder, season, episode_number, position)
    return jsonify({"ok": True})
