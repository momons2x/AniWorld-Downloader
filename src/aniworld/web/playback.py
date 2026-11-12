"""On-demand playback preparation for library videos.

Every play request is probed, then played directly when the browser can decode
it, remuxed (video stream copied) when only the container/audio is in the way,
or transcoded to H.264/AAC. Remuxes and transcodes land in a fragmented MP4
cache that is served while ffmpeg is still writing it, so playback starts
within seconds. Each play is granted a short-lived random token; the stream
endpoint only serves the token's exact file, so the library path is never
exposed by URL.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..logger import get_logger
from . import library as lib
from . import paths
from . import playback_media as media

logger = get_logger(__name__)

# token -> session dict (path, source, expires, mime, duration, tracks...)
# Older entries may still be (path, source, expires) tuples; both are read.
_sessions = {}
_sessions_lock = threading.Lock()
SESSION_SECONDS = 3600

# One transcode at a time so a double-click cannot corrupt the cache file.
_transcode_lock = threading.Lock()

# Bump when the transcode settings change; it is part of the cache key so old
# files (e.g. plain-MP4 caches with a real duration) are never reused for the
# incremental fMP4 cache.
TRANSCODE_VERSION = 5

# How long the stream endpoint waits for a growing cache file to satisfy a
# range request before giving up and letting the player retry.
STREAM_WAIT_SECONDS = 240

# How long the header patcher waits for ffmpeg to write the moov box.
HEADER_WAIT_SECONDS = 30

# Subtitle extraction is fast (seconds); a stuck ffmpeg must not hang a request.
SUBTITLE_TIMEOUT = 180

# Async transcode jobs: id -> dict(status, stage, progress, token, error, ...)
_jobs = {}
_jobs_lock = threading.Lock()
JOB_RETENTION_SECONDS = 3600

# ffmpeg processes owned by jobs, so a cancel or shutdown can stop them.
_processes = {}
_processes_lock = threading.Lock()

# Jobs that stop reporting progress are cancelled instead of hanging forever.
JOB_STALL_SECONDS = 30 * 60
WATCHDOG_INTERVAL_SECONDS = 30
CANCEL_TERMINATE_SECONDS = 5
CANCEL_KILL_SECONDS = 30
CANCEL_WAIT_SECONDS = 10
SHUTDOWN_WAIT_SECONDS = 5

# How long clearing the cache waits for cancelled jobs to release their files.
CLEAR_WAIT_SECONDS = 2
_watchdog_started = False
_watchdog_lock = threading.Lock()


class PlaybackCancelled(Exception):
    """Raised inside a job worker after a cancel request."""

# Cache pruning: oldest cache files are dropped once .stream exceeds this.
CACHE_PRUNE_INTERVAL = 60
_last_prune = {"at": 0.0}
_cache_prune_lock = threading.Lock()

_subtitle_locks = {}
_subtitle_locks_lock = threading.Lock()

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_OUT_TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")

_SESSION_DEFAULTS = {
    "mime": "video/mp4",
    "duration": 0.0,
    "audio_tracks": [],
    "subtitle_tracks": [],
    "selected_audio": None,
    "mode": "transcode",
}


def _stream_cache_dir():
    return paths.default_download_path() / ".stream"


def _cache_paths(target_path, audio_ordinal=None, variant="auto"):
    target_path = Path(target_path)
    try:
        resolved = target_path.resolve()
        stat = target_path.stat()
        mtime_ns = stat.st_mtime_ns
        size = stat.st_size
    except OSError:
        resolved = target_path
        mtime_ns = 0
        size = 0
    audio_key = "default" if audio_ordinal is None else str(int(audio_ordinal))
    key = f"{TRANSCODE_VERSION}:{resolved}:{mtime_ns}:{size}:{audio_key}:{variant}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return _stream_cache_dir() / f"{digest}.mp4"


def _meta_path(cache):
    return Path(cache).with_suffix(".json")


def _subtitle_dir(cache):
    cache = Path(cache)
    return cache.parent / f"{cache.stem}.subs"


def _read_meta(cache):
    try:
        return json.loads(_meta_path(cache).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_meta(cache, source, audio_ordinal, variant, mode, duration):
    payload = {
        "version": TRANSCODE_VERSION,
        "source": str(Path(source).resolve()),
        "mtime_ns": Path(source).stat().st_mtime_ns,
        "size": Path(source).stat().st_size,
        "audio": None if audio_ordinal is None else int(audio_ordinal),
        "variant": variant,
        "mode": mode,
        "duration": round(float(duration or 0.0), 3),
        "created": time.time(),
    }
    target = _meta_path(cache)
    tmp = target.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.warning("Could not write playback metadata for %s: %s", cache, exc)


def _is_fresh(cache, source=None, audio_ordinal=None, variant=None):
    """A cache file is usable only when it was completed for this exact source."""
    cache = Path(cache)
    try:
        stat = cache.stat()
    except OSError:
        return False
    if stat.st_size <= 0:
        return False
    meta = _read_meta(cache)
    if meta is None:
        return False
    if meta.get("version") != TRANSCODE_VERSION:
        return False
    if source is None:
        return True
    try:
        source_stat = Path(source).stat()
    except OSError:
        return False
    return (
        meta.get("source") == str(Path(source).resolve())
        and meta.get("mtime_ns") == source_stat.st_mtime_ns
        and meta.get("size") == source_stat.st_size
        and meta.get("audio") == (None if audio_ordinal is None else int(audio_ordinal))
        and meta.get("variant") == variant
    )


def _ffmpeg_executable():
    return media.ffmpeg_executable()


def transcode_command(
    input_path,
    output_path,
    input_ext,
    audio_ordinal=None,
    force_transcode=False,
    info=None,
    plan=None,
    encoder=None,
):
    """ffmpeg argv for one direct/remux/transcode preparation.

    The video output is always a fragmented MP4 with an empty moov: ffmpeg
    writes the header immediately and then appends fragments as it encodes, so
    the stream endpoint can start serving the file while it is still growing.
    """
    if plan is None:
        plan = media.choose_plan(info, input_ext, audio_ordinal, force_transcode)
    return media.build_command(
        input_path,
        output_path,
        plan,
        info=info,
        ffmpeg=_ffmpeg_executable(),
        encoder=encoder,
    )


def _track_payload(info, plan):
    audio_tracks = []
    if info is not None:
        for audio in info.audios:
            audio_tracks.append(
                {
                    "ordinal": audio.ordinal,
                    "language": audio.language,
                    "label": media.track_label(
                        audio.language, audio.title, f"Track {audio.ordinal + 1}"
                    ),
                    "codec": audio.codec,
                    "channels": audio.channels,
                    "default": plan.audio is not None
                    and audio.ordinal == plan.audio.ordinal,
                }
            )
    subtitle_tracks = []
    if info is not None and plan.mode != "direct":
        for subtitle in info.subtitles:
            if not subtitle.text:
                continue
            subtitle_tracks.append(
                {
                    "index": subtitle.index,
                    "language": subtitle.language,
                    "label": media.track_label(
                        subtitle.language,
                        subtitle.title,
                        f"Subtitle {len(subtitle_tracks) + 1}",
                    ),
                    "codec": subtitle.codec,
                    "default": subtitle.default,
                    "forced": subtitle.forced,
                }
            )
    return audio_tracks, subtitle_tracks


def _make_session(target_path, source_path, info, plan, audio_ordinal):
    audio_tracks, subtitle_tracks = _track_payload(info, plan)
    return register(
        target_path,
        source_path=source_path,
        mime=plan.mime,
        duration=info.duration if info is not None else 0.0,
        audio_tracks=audio_tracks,
        subtitle_tracks=subtitle_tracks,
        selected_audio=plan.audio.ordinal if plan.audio is not None else None,
        mode=plan.mode,
    )


def _prune_sessions_locked():
    now = time.time()
    stale = [
        token
        for token, entry in _sessions.items()
        if _entry_expires(entry) <= now
    ]
    for token in stale:
        _sessions.pop(token, None)


def register(
    target_path,
    source_path=None,
    mime="video/mp4",
    duration=0.0,
    audio_tracks=None,
    subtitle_tracks=None,
    selected_audio=None,
    mode="transcode",
):
    """Mint a playback token for a file. Returns the token string."""
    token = secrets.token_urlsafe(24)
    entry = {
        "path": Path(target_path),
        "source": Path(source_path or target_path),
        "expires": time.time() + SESSION_SECONDS,
        "mime": mime or "video/mp4",
        "duration": float(duration or 0.0),
        "audio_tracks": list(audio_tracks or []),
        "subtitle_tracks": list(subtitle_tracks or []),
        "selected_audio": selected_audio,
        "mode": mode,
    }
    with _sessions_lock:
        _prune_sessions_locked()
        _sessions[token] = entry
    return token


def _entry_expires(entry):
    if isinstance(entry, dict):
        return entry.get("expires", 0)
    return entry[2]


def _entry_dict(entry):
    if isinstance(entry, dict):
        result = dict(entry)
    else:
        path, source, expires = entry
        result = {
            "path": Path(path),
            "source": Path(source),
            "expires": expires,
        }
    for key, value in _SESSION_DEFAULTS.items():
        if key not in result:
            result[key] = list(value) if isinstance(value, list) else value
    result["path"] = Path(result["path"])
    result["source"] = Path(result["source"])
    return result


def session(token):
    """Full session info for a live token, with sliding expiry."""
    now = time.time()
    with _sessions_lock:
        entry = _sessions.get(token)
        if entry is None:
            raise lib.LibraryError("Invalid playback session")
        expires = _entry_expires(entry)
        if now > expires:
            _sessions.pop(token, None)
            raise lib.LibraryError("Playback session expired")
        if expires - now < SESSION_SECONDS / 2:
            if isinstance(entry, dict):
                entry["expires"] = now + SESSION_SECONDS
            else:
                _sessions[token] = (entry[0], entry[1], now + SESSION_SECONDS)
        return _entry_dict(_sessions[token])


def resolve(token):
    """(playback_path, source_path, source_label) for a live token."""
    entry = session(token)
    return entry["path"], entry["source"], entry["source"].name


def _prune_jobs():
    now = time.time()
    with _jobs_lock:
        stale = [
            job_id
            for job_id, job in _jobs.items()
            if job.get("done_at") and now - job["done_at"] > JOB_RETENTION_SECONDS
        ]
        for job_id in stale:
            _jobs.pop(job_id, None)


def _set_job(job_id, **fields):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)
            job["last_activity"] = time.time()


def _touch_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["last_activity"] = time.time()


def _cancel_requested(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        return bool(job and job.get("cancel_requested"))


def _track_process(job_id, process):
    with _processes_lock:
        _processes[job_id] = process


def _untrack_process(job_id, process):
    with _processes_lock:
        if _processes.get(job_id) is process:
            _processes.pop(job_id, None)


def _terminate_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=CANCEL_TERMINATE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        _kill_process(process)


def _kill_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        process.kill()
        process.wait(timeout=CANCEL_TERMINATE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Could not stop ffmpeg process %s", process.pid)


def _remove_cache_files(cache):
    """Delete a partial cache and its sidecars after a failed/cancelled job."""
    cache = Path(cache)
    for path in (cache, _meta_path(cache)):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove partial cache %s: %s", path, exc)
    shutil.rmtree(_subtitle_dir(cache), ignore_errors=True)


def _request_cancel(job_id, reason=None, force=False):
    """Mark one preparing job as cancelling and stop its ffmpeg process."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("status") not in ("preparing", "cancelling"):
            return False
        job["cancel_requested"] = True
        job["status"] = "cancelling"
        job["stage"] = "cancelling"
        job["last_activity"] = time.time()
        if reason:
            job["cancel_reason"] = reason
        if not job.get("cancel_requested_at"):
            job["cancel_requested_at"] = time.time()
    with _processes_lock:
        process = _processes.get(job_id)
    if force:
        _kill_process(process)
    else:
        _terminate_process(process)
    return True


def _finish_cancelled(job_id, cache):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("status") not in ("preparing", "cancelling"):
            return
    # Keep the job in "cancelling" until the partial file is gone: a new play
    # request must not create a replacement cache that we then delete.
    _remove_cache_files(cache)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("status") not in ("preparing", "cancelling"):
            return
        reason = job.get("cancel_reason")
        status = "error" if reason else "cancelled"
        job.update(
            status=status,
            stage=status,
            error=reason or "Playback preparation cancelled",
            done_at=time.time(),
            last_activity=time.time(),
        )


def _abandon_job(job_id, reason, status="error"):
    """Finalize a job whose worker did not stop after the cancel request."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("status") not in ("preparing", "cancelling"):
            return
        cache = job.get("cache")
    with _processes_lock:
        process = _processes.pop(job_id, None)
    _kill_process(process)
    if cache is not None:
        _remove_cache_files(cache)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("status") not in ("preparing", "cancelling"):
            return
        job.update(
            status=status,
            stage=status,
            error=reason,
            done_at=time.time(),
            last_activity=time.time(),
        )


def cancel_job(job_id, wait=True):
    """Cancel a preparing playback job and stop its ffmpeg process."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise lib.LibraryError("Unknown playback job")
        status = job.get("status")
    if status not in ("preparing", "cancelling"):
        return job_status(job_id)

    _request_cancel(job_id)
    if wait:
        deadline = time.time() + CANCEL_WAIT_SECONDS
        while time.time() < deadline:
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None or job.get("status") not in (
                    "preparing",
                    "cancelling",
                ):
                    break
            time.sleep(0.05)
    return job_status(job_id)


def _watchdog_tick(now=None):
    now = time.time() if now is None else now
    stalled = []
    forced = []
    with _jobs_lock:
        for job_id, job in _jobs.items():
            status = job.get("status")
            if status == "preparing":
                last = job.get("last_activity") or job.get("created_at") or now
                if now - last >= JOB_STALL_SECONDS:
                    stalled.append(job_id)
            elif status == "cancelling":
                requested = job.get("cancel_requested_at") or now
                if now - requested >= CANCEL_KILL_SECONDS:
                    forced.append(job_id)
    for job_id in stalled:
        logger.warning("Playback job %s stalled; cancelling", job_id)
        _request_cancel(job_id, reason="Playback preparation timed out")
    for job_id in forced:
        logger.warning("Playback job %s did not stop; killing ffmpeg", job_id)
        _abandon_job(job_id, "Playback preparation timed out")


def _watchdog_loop():
    while True:
        time.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            _watchdog_tick()
        except Exception:
            logger.exception("Playback watchdog error")


def ensure_watchdog():
    global _watchdog_started
    with _watchdog_lock:
        if _watchdog_started:
            return
        _watchdog_started = True
    threading.Thread(
        target=_watchdog_loop, name="aniworld-playback-watchdog", daemon=True
    ).start()


def shutdown():
    """Cancel active playback jobs and stop every ffmpeg process we own."""
    with _jobs_lock:
        active = [
            job_id
            for job_id, job in _jobs.items()
            if job.get("status") in ("preparing", "cancelling")
        ]
    for job_id in active:
        _request_cancel(job_id)
    deadline = time.time() + SHUTDOWN_WAIT_SECONDS
    while time.time() < deadline:
        with _jobs_lock:
            active = [
                job_id
                for job_id, job in _jobs.items()
                if job.get("status") in ("preparing", "cancelling")
            ]
        if not active:
            break
        time.sleep(0.1)
    for job_id in active:
        _abandon_job(job_id, "Server shutting down", status="cancelled")
    with _processes_lock:
        processes = list(_processes.values())
        _processes.clear()
    for process in processes:
        _kill_process(process)


def _acquire_transcode_slot(job_id):
    """Wait for the transcode lock, but stop waiting when the job is cancelled."""
    while True:
        if _cancel_requested(job_id):
            return False
        if _transcode_lock.acquire(timeout=0.5):
            return True


def _cache_limit_bytes():
    raw = os.environ.get("ANIWORLD_STREAM_CACHE_GB", "10").strip()
    try:
        limit_gb = float(raw)
    except ValueError:
        limit_gb = 10.0
    if limit_gb <= 0:
        return 0
    return int(limit_gb * 1024**3)


def _active_cache_paths():
    with _jobs_lock:
        return {
            Path(job["cache"])
            for job in _jobs.values()
            if job.get("cache") and job.get("status") in ("preparing", "cancelling")
        }


def _prune_cache(keep=None):
    """Drop the oldest caches once the .stream directory exceeds its budget."""
    now = time.time()
    with _cache_prune_lock:
        if now - _last_prune["at"] < CACHE_PRUNE_INTERVAL:
            return
        _last_prune["at"] = now
        limit = _cache_limit_bytes()
        if limit <= 0:
            return
        directory = _stream_cache_dir()
        if not directory.is_dir():
            return
        active = _active_cache_paths()
        if keep is not None:
            active.add(Path(keep))
        entries = []
        total = 0
        for cache in directory.glob("*.mp4"):
            try:
                size = cache.stat().st_size
            except OSError:
                continue
            total += size
            entries.append((cache.stat().st_mtime, cache, size))
        if total <= limit:
            return
        entries.sort(key=lambda item: item[0])
        for _mtime, cache, size in entries:
            if total <= limit:
                break
            if cache in active:
                continue
            try:
                cache.unlink(missing_ok=True)
                _meta_path(cache).unlink(missing_ok=True)
                shutil.rmtree(_subtitle_dir(cache), ignore_errors=True)
                total -= size
                logger.info("Pruned playback cache %s", cache.name)
            except OSError as exc:
                logger.warning("Could not prune %s: %s", cache, exc)


def _active_job_ids():
    with _jobs_lock:
        return [
            job_id
            for job_id, job in _jobs.items()
            if job.get("status") in ("preparing", "cancelling")
        ]


def _path_size(path):
    """Bytes a cache entry occupies, a sidecar directory counted as a whole."""
    path = Path(path)
    if path.is_dir():
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
        return total
    try:
        return path.stat().st_size
    except OSError:
        return 0


def cache_stats():
    """What the .stream cache holds right now, for the administration page."""
    directory = _stream_cache_dir()
    total = 0
    files = 0
    if directory.is_dir():
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
            files += 1
    return {
        "path": str(directory),
        "bytes": total,
        "files": files,
        "limit_bytes": _cache_limit_bytes(),
    }


def clear_cache(wait=True):
    """Empty the .stream cache and report what was removed.

    Jobs that are still preparing are cancelled first: their ffmpeg is stopped
    and the job normally removes its own partial file, so the sweep only deals
    with finished caches and leftovers. Anything a job that would not stop
    still holds is skipped and counted, never reported as removed.
    """
    deadline = time.time() + (CLEAR_WAIT_SECONDS if wait else 0)
    for job_id in _active_job_ids():
        _request_cancel(job_id, reason="Playback cache cleared")
    while True:
        if not _active_job_ids() or time.time() >= deadline:
            break
        time.sleep(0.05)

    removed_bytes = 0
    removed_files = 0
    kept = 0
    directory = _stream_cache_dir()
    if directory.is_dir():
        active = _active_cache_paths()
        with _cache_prune_lock:
            for path in list(directory.iterdir()):
                if path in active:
                    kept += 1
                    continue
                try:
                    removed_bytes += _path_size(path)
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
                    # rmtree ignores its errors, so the entry itself says
                    # whether the removal really happened
                    if path.exists():
                        kept += 1
                        continue
                    removed_files += 1
                except OSError as exc:
                    kept += 1
                    logger.warning("Could not remove %s: %s", path, exc)
    logger.info(
        "Cleared playback cache: %d files, %d bytes removed, %d kept",
        removed_files,
        removed_bytes,
        kept,
    )
    return {
        "removed_bytes": removed_bytes,
        "removed_files": removed_files,
        "kept": kept,
    }


def _patch_header_worker(job_id, cache, duration, info, attempt):
    offset, moov = media.read_moov(cache, timeout=HEADER_WAIT_SECONDS)
    if not moov or _cancel_requested(job_id):
        return
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("attempt") != attempt:
            return
        if job.get("cancel_requested"):
            return
    video_duration = info.video.duration if info is not None and info.video else None
    audio_duration = (
        info.audios[0].duration if info is not None and info.audios else None
    )
    header = media.patch_moov_duration(
        moov, duration, video_duration=video_duration, audio_duration=audio_duration
    )
    if _cancel_requested(job_id):
        return
    media.write_header(cache, header, offset)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None and job.get("attempt") == attempt:
            job.update(header_ready=True, header_offset=offset, header=header)
            job["last_activity"] = time.time()


def _run_ffmpeg(job_id, source, cache, plan, info, encoder, known_duration):
    """Run one ffmpeg attempt; returns (returncode, parsed_duration)."""
    with _jobs_lock:
        job = _jobs.get(job_id) or {}
        attempt = job.get("attempt", 0)
    cmd = transcode_command(
        source,
        cache,
        Path(source).suffix,
        info=info,
        plan=plan,
        encoder=encoder,
    )
    logger.info(
        "Preparing %s for playback (%s)", Path(source).name, plan.mode
    )
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
    )
    _track_process(job_id, process)
    if _cancel_requested(job_id):
        _terminate_process(process)
    duration = known_duration or 0.0
    header_started = False
    try:
        for line in process.stdout:
            line = line.strip()
            match = _DURATION_RE.search(line)
            if match:
                hours, minutes, seconds = match.groups()
                if duration <= 0:
                    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                _touch_job(job_id)
                continue
            match = _OUT_TIME_RE.search(line)
            if match and duration > 0:
                seconds = int(match.group(1)) / 1_000_000
                progress = min(99.0, seconds / duration * 100)
                _set_job(job_id, progress=round(progress, 1))
            if not header_started and duration > 0:
                header_started = True
                threading.Thread(
                    target=_patch_header_worker,
                    args=(job_id, cache, duration, info, attempt),
                    daemon=True,
                ).start()
        return process.wait(), duration
    finally:
        _untrack_process(job_id, process)
        if process.stdout is not None:
            process.stdout.close()


def _attempts_for(plan, info, source, audio_ordinal):
    """Plan attempts in order: as chosen, then cheaper fallbacks."""
    attempts = [(plan, None)]
    if plan.mode == "remux":
        fallback = media.choose_plan(
            info, Path(source).suffix, audio_ordinal, force_transcode=True
        )
        attempts.append((fallback, None))
    detected = media.detect_encoder()
    if detected != "libx264":
        for attempt_plan, _encoder in list(attempts):
            if not attempt_plan.video_copy:
                attempts.append((attempt_plan, "libx264"))
    return attempts


def _finalize_cache(job_id, cache, source, audio_ordinal, variant, mode, duration, info):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None and (
            job.get("status") not in ("preparing", "cancelling")
            or job.get("cancel_requested")
        ):
            return
    offset, moov = media.read_moov(cache, timeout=5)
    if moov:
        video_duration = info.video.duration if info is not None and info.video else None
        audio_duration = (
            info.audios[0].duration if info is not None and info.audios else None
        )
        header = media.patch_moov_duration(
            moov, duration, video_duration=video_duration, audio_duration=audio_duration
        )
        media.write_header(cache, header, offset)
    _write_meta(cache, source, audio_ordinal, variant, mode, duration)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None and job.get("status") in ("preparing", "cancelling"):
            if job.get("cancel_requested"):
                return
            job.update(
                status="ready",
                stage="ready",
                progress=100.0,
                duration=duration,
                mode=mode,
                header_ready=True,
                done_at=time.time(),
                last_activity=time.time(),
            )
            token = job.get("token")
        else:
            token = None
    if token:
        with _sessions_lock:
            entry = _sessions.get(token)
            if isinstance(entry, dict) and not entry.get("duration"):
                entry["duration"] = duration


def _run_transcode(job_id, target_path, cache):
    """Worker thread: prepare the cache file, then mark the job ready.

    ffmpeg writes directly to the cache path so the stream endpoint can serve
    the growing file; on failure every attempt is tried and the partial file
    is removed.
    """
    acquired = False
    try:
        with _jobs_lock:
            job = dict(_jobs.get(job_id) or {})
        if job.get("cancel_requested"):
            raise PlaybackCancelled()
        plan = job.get("plan")
        info = job.get("info")
        audio_ordinal = job.get("audio_ordinal")
        variant = job.get("variant", "auto")
        if plan is None:
            plan = media.choose_plan(
                info, Path(target_path).suffix, audio_ordinal, False
            )

        if not _acquire_transcode_slot(job_id):
            raise PlaybackCancelled()
        acquired = True
        if _cancel_requested(job_id):
            raise PlaybackCancelled()

        if _is_fresh(cache, target_path, audio_ordinal, variant):
            _finalize_cache(
                job_id,
                cache,
                target_path,
                audio_ordinal,
                variant,
                plan.mode,
                info.duration if info is not None else 0.0,
                info,
            )
            if _cancel_requested(job_id):
                raise PlaybackCancelled()
            return

        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.unlink(missing_ok=True)
        _set_job(job_id, stage="transcoding", progress=0.0)

        duration = info.duration if info is not None else 0.0
        mode = plan.mode
        last_error = None
        success = False
        for index, (attempt_plan, encoder) in enumerate(
            _attempts_for(plan, info, target_path, audio_ordinal), start=1
        ):
            if _cancel_requested(job_id):
                raise PlaybackCancelled()
            _set_job(job_id, attempt=index, stage="transcoding", progress=0.0)
            cache.unlink(missing_ok=True)
            try:
                code, duration = _run_ffmpeg(
                    job_id,
                    target_path,
                    cache,
                    attempt_plan,
                    info,
                    encoder,
                    duration,
                )
            except Exception as exc:  # ffmpeg could not even start
                if _cancel_requested(job_id):
                    raise PlaybackCancelled() from exc
                last_error = exc
                logger.error("Playback ffmpeg failed to start: %s", exc)
                continue
            if _cancel_requested(job_id):
                raise PlaybackCancelled()
            if code == 0 and cache.exists() and cache.stat().st_size > 0:
                success = True
                mode = attempt_plan.mode
                break
            last_error = f"ffmpeg exited with code {code}"
            logger.warning(
                "Playback attempt %s (%s) failed for %s: %s",
                index,
                attempt_plan.mode,
                Path(target_path).name,
                last_error,
            )
        if not success:
            raise lib.LibraryError("Transcoding failed for this video")

        if _cancel_requested(job_id):
            raise PlaybackCancelled()
        _set_job(job_id, stage="finalizing")
        _finalize_cache(
            job_id,
            cache,
            target_path,
            audio_ordinal,
            variant,
            mode,
            duration,
            info,
        )
        if _cancel_requested(job_id):
            raise PlaybackCancelled()
    except PlaybackCancelled:
        logger.info("Playback preparation cancelled for %s", target_path)
        _finish_cancelled(job_id, cache)
    except Exception as exc:  # every failure has to land in the job
        if _cancel_requested(job_id):
            _finish_cancelled(job_id, cache)
            return
        logger.error("Playback preparation failed for %s: %s", target_path, exc)
        _remove_cache_files(cache)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None and job.get("status") in ("preparing", "cancelling"):
                job.update(
                    status="error",
                    stage="error",
                    error=str(exc) or "Transcoding failed",
                    done_at=time.time(),
                    last_activity=time.time(),
                )
    finally:
        if acquired:
            _transcode_lock.release()


def start_async(
    folder,
    season,
    episode,
    custom_path_id=None,
    lang_folder=None,
    audio_ordinal=None,
    force_transcode=False,
):
    """Kick off (or find) a playback preparation; never blocks on transcoding.

    Returns {"status": "ready", "token": ...} when the file can be streamed
    right away, or {"status": "streaming", "job": id, "token": ...} while
    ffmpeg writes the fragmented cache — the token resolves to the cache path,
    which the stream endpoint serves as it grows.
    """
    path, ext = lib.find_video(folder, season, episode, custom_path_id, lang_folder)
    info = media.probe(path)
    plan = media.choose_plan(info, ext, audio_ordinal, force_transcode)

    if plan.mode == "direct":
        token = _make_session(path, path, info, plan, audio_ordinal)
        return {"status": "ready", "token": token}

    variant = "transcode" if force_transcode else "auto"
    cache = _cache_paths(path, audio_ordinal, variant)
    if _is_fresh(cache, path, audio_ordinal, variant):
        token = _make_session(cache, path, info, plan, audio_ordinal)
        return {"status": "ready", "token": token}

    _prune_jobs()
    _prune_cache(keep=cache)
    ensure_watchdog()
    token = _make_session(cache, path, info, plan, audio_ordinal)
    now = time.time()
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job.get("cache") != cache:
                continue
            if job.get("status") == "preparing":
                return {"status": "streaming", "job": job_id, "token": token}
            if job.get("status") == "cancelling":
                raise lib.LibraryError("Playback is stopping — try again in a moment")
        job_id = secrets.token_urlsafe(12)
        _jobs[job_id] = {
            "status": "preparing",
            "stage": "transcoding",
            "progress": 0.0,
            "cache": cache,
            "source": path,
            "token": token,
            "plan": plan,
            "info": info,
            "audio_ordinal": audio_ordinal,
            "variant": variant,
            "attempt": 0,
            "header_ready": False,
            "done_at": None,
            "created_at": now,
            "last_activity": now,
            "cancel_requested": False,
            "cancel_requested_at": None,
            "cancel_reason": None,
        }
    threading.Thread(
        target=_run_transcode, args=(job_id, path, cache), daemon=True
    ).start()
    return {"status": "streaming", "job": job_id, "token": token}


def job_status(job_id):
    """Current state of one playback job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise lib.LibraryError("Unknown playback job")
        result = {
            "status": job["status"],
            "stage": job.get("stage", ""),
            "progress": job.get("progress", 0.0),
        }
        if job["status"] == "ready":
            result["token"] = job.get("token")
            result["duration"] = job.get("duration", 0.0)
            result["mode"] = job.get("mode", "")
        elif job["status"] in ("error", "cancelled"):
            result["error"] = job.get("error") or "Transcoding failed"
        return result


def _job_for_cache(cache):
    """Live job writing into this cache file, if any."""
    cache = Path(cache)
    with _jobs_lock:
        for job in _jobs.values():
            if job.get("cache") == cache and job.get("status") in (
                "preparing",
                "cancelling",
            ):
                return job
    return None


SIDX_WAIT_SECONDS = 15


def _scan_leading_boxes(path):
    """Top-level boxes before the first media fragment.

    Returns (sidx_ranges, media_started). While ffmpeg writes the file the
    global sidx is still a placeholder; once a moof/mdat appears the leading
    boxes (and their real sizes) are stable.
    """
    path = Path(path)
    ranges = []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            offset = 0
            while offset < size:
                handle.seek(offset)
                header = handle.read(8)
                if len(header) < 8:
                    break
                box_size = int.from_bytes(header[:4], "big")
                kind = header[4:8]
                header_size = 8
                if box_size == 1:
                    extra = handle.read(8)
                    if len(extra) < 8:
                        break
                    box_size = int.from_bytes(extra, "big")
                    header_size = 16
                elif box_size == 0:
                    break
                if box_size < header_size:
                    break
                if kind in (b"moof", b"mdat"):
                    return ranges, True
                if kind == b"sidx":
                    end = offset + box_size
                    if end > size:
                        break
                    ranges.append((offset, end))
                offset += box_size
    except OSError:
        return [], False
    return ranges, False


def _merge_ranges(ranges):
    if not ranges:
        return []
    merged = [list(ranges[0])]
    for start, end in ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(entry) for entry in merged]


def growing_ranges(path):
    """sidx ranges to hide while a job is still writing this cache file.

    ffmpeg patches the global sidx only when it finishes. While the file grows,
    Chrome trusts the placeholder index and jumps to the end, so the stream
    endpoint serves the file without its leading sidx boxes. Completed files
    keep the index and start instantly. Returns None when no hiding is needed.
    """
    path = Path(path)
    job = _job_for_cache(path)
    if job is None:
        return None
    with _jobs_lock:
        status = job.get("status")
        if status in ("error", "cancelled"):
            raise lib.LibraryError(job.get("error") or "Transcoding failed")
        if status == "cancelling":
            raise lib.LibraryError("Playback preparation cancelled")
        cached = job.get("growing_ranges")
        if cached is not None:
            return cached
    deadline = time.time() + SIDX_WAIT_SECONDS
    while time.time() < deadline:
        status = job.get("status")
        if status in ("error", "cancelled"):
            raise lib.LibraryError(job.get("error") or "Transcoding failed")
        if status == "cancelling":
            raise lib.LibraryError("Playback preparation cancelled")
        ranges, media_started = _scan_leading_boxes(path)
        if media_started:
            merged = _merge_ranges(ranges)
            with _jobs_lock:
                job["growing_ranges"] = merged
            return merged
        if status == "ready":
            return None
        time.sleep(0.1)
    return []


def virtual_size(path, ranges):
    size = Path(path).stat().st_size
    return max(0, size - sum(end - start for start, end in ranges))


def iter_virtual_range(path, ranges, start, length, chunk=262144):
    """Yield bytes of the virtual file (leading sidx boxes hidden)."""
    path = Path(path)
    if not ranges:
        with path.open("rb") as handle:
            handle.seek(start)
            left = length
            while left > 0:
                data = handle.read(min(chunk, left))
                if not data:
                    break
                left -= len(data)
                yield data
        return
    skip_start = ranges[0][0]
    skip_end = ranges[-1][1]
    skip_len = skip_end - skip_start
    with path.open("rb") as handle:
        pos = start
        left = length
        while left > 0:
            if pos < skip_start:
                take = min(chunk, left, skip_start - pos)
                real = pos
            else:
                take = min(chunk, left)
                real = pos + skip_len
            handle.seek(real)
            data = handle.read(take)
            if not data:
                break
            pos += len(data)
            left -= len(data)
            yield data


def iter_growing_range(path, ranges, start, chunk=262144, poll=0.2):
    """Yield from the virtual file, following it while ffmpeg still writes."""
    path = Path(path)
    pos = start
    while True:
        size = virtual_size(path, ranges)
        if size > pos:
            length = min(chunk, size - pos)
            yield from iter_virtual_range(path, ranges, pos, length)
            pos += length
            continue
        job = _job_for_cache(path)
        if job is None:
            if virtual_size(path, ranges) > pos:
                continue
            return
        time.sleep(poll)


def prepare_range(path, start_byte):
    """Wait until `path` has bytes at `start_byte` (for growing cache files).

    Files already complete are returned immediately; when a job owns the file
    this blocks until the fragment containing the requested range lands (or
    the job errors / the wait budget runs out). Requests from byte 0 also wait
    for the patched moov header, so the player gets a real duration.
    """
    path = Path(path)
    job = _job_for_cache(path)
    if job is None:
        return
    deadline = time.time() + STREAM_WAIT_SECONDS
    while time.time() < deadline:
        if job.get("status") in ("error", "cancelled"):
            raise lib.LibraryError(job.get("error") or "Transcoding failed")
        if job.get("status") == "cancelling":
            raise lib.LibraryError("Playback preparation cancelled")
        size = path.stat().st_size if path.exists() else 0
        if job.get("status") == "ready" and size > start_byte:
            return
        if size > start_byte and (
            start_byte > 0 or job.get("header_ready")
        ):
            return
        time.sleep(0.2)
    raise lib.LibraryError("Still preparing — please retry in a moment")


def _subtitle_lock(target):
    with _subtitle_locks_lock:
        return _subtitle_locks.setdefault(str(target), threading.Lock())


def ensure_subtitle(entry, stream_index):
    """Extract one text subtitle track to a sidecar WebVTT file."""
    try:
        stream_index = int(stream_index)
    except (TypeError, ValueError):
        raise lib.LibraryError("Invalid subtitle track")
    allowed = {
        track["index"]
        for track in (entry.get("subtitle_tracks") or [])
        if "index" in track
    }
    if stream_index not in allowed:
        raise lib.LibraryError("Unknown subtitle track")
    source = Path(entry["source"])
    cache = Path(entry["path"])
    target = _subtitle_dir(cache) / f"{stream_index}.vtt"

    def fresh():
        try:
            return (
                target.exists()
                and target.stat().st_size > 0
                and target.stat().st_mtime >= source.stat().st_mtime
            )
        except OSError:
            return False

    if fresh():
        return target
    lock = _subtitle_lock(target)
    with lock:
        if fresh():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp.vtt")
        tmp.unlink(missing_ok=True)
        try:
            result = subprocess.run(
                media.subtitle_command(source, stream_index, tmp),
                capture_output=True,
                text=True,
                timeout=SUBTITLE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            tmp.unlink(missing_ok=True)
            logger.warning("Subtitle extraction failed for %s: %s", source.name, exc)
            raise lib.LibraryError("Could not extract this subtitle track")
        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            logger.warning(
                "Subtitle extraction failed for %s: %s",
                source.name,
                (result.stderr or "")[-400:],
            )
            raise lib.LibraryError("Could not extract this subtitle track")
        tmp.replace(target)
    return target


def ensure_playable(target_path, input_ext):
    """Return the file the browser should play for `target_path` (synchronous)."""
    info = media.probe(target_path)
    plan = media.choose_plan(info, input_ext)
    if plan.mode == "direct":
        return Path(target_path)
    audio_ordinal = None
    cache = _cache_paths(target_path, audio_ordinal)
    with _transcode_lock:
        if _is_fresh(cache, target_path, audio_ordinal, "auto"):
            return cache
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.unlink(missing_ok=True)
        duration = info.duration if info is not None else 0.0
        mode = plan.mode
        for attempt_plan, encoder in _attempts_for(
            plan, info, target_path, audio_ordinal
        ):
            code, duration = _run_ffmpeg(
                "sync", target_path, cache, attempt_plan, info, encoder, duration
            )
            if code == 0 and cache.exists() and cache.stat().st_size > 0:
                mode = attempt_plan.mode
                break
        else:
            cache.unlink(missing_ok=True)
            raise lib.LibraryError("Transcoding failed for this video")
        _finalize_cache(
            "sync",
            cache,
            target_path,
            audio_ordinal,
            "auto",
            mode,
            duration,
            info,
        )
    return cache


def start(folder, season, episode, custom_path_id=None, lang_folder=None):
    """Resolve, make playable, and mint a token for one library video."""
    path, ext = lib.find_video(folder, season, episode, custom_path_id, lang_folder)
    info = media.probe(path)
    plan = media.choose_plan(info, ext)
    if plan.mode == "direct":
        token = _make_session(path, path, info, plan, None)
    else:
        playable = ensure_playable(path, ext)
        token = _make_session(playable, path, info, plan, None)
    return token
