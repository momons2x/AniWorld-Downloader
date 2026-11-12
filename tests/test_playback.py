"""Instant playback: streaming transcodes + saved resume positions."""

import threading
import time
from pathlib import Path

import pytest

from aniworld.web import library, playback
from aniworld.web import playback_media as media


def test_growing_view_hides_leading_sidx(tmp_path):
    data = (
        _box("ftyp", b"isom")
        + _box("moov", b"m" * 20)
        + _box("sidx", b"s" * 10)
        + _box("sidx", b"t" * 5)
        + _box("moof", b"f" * 7)
        + _box("mdat", b"d" * 30)
    )
    path = tmp_path / "growing.mp4"
    path.write_bytes(data)

    ranges, media_started = playback._scan_leading_boxes(path)
    assert media_started is True
    merged = playback._merge_ranges(ranges)
    assert len(merged) == 1
    skip_start, skip_end = merged[0]
    expected = data[:skip_start] + data[skip_end:]
    assert playback.virtual_size(path, merged) == len(expected)
    assert b"".join(
        playback.iter_virtual_range(path, merged, 0, len(expected))
    ) == expected
    assert b"".join(
        playback.iter_virtual_range(path, merged, 5, 40)
    ) == expected[5:45]


def test_growing_ranges_cached_for_streaming_job(tmp_path):
    data = (
        _box("ftyp", b"isom")
        + _box("moov", b"m" * 20)
        + _box("sidx", b"s" * 10)
        + _box("moof", b"f" * 7)
        + _box("mdat", b"d" * 30)
    )
    path = tmp_path / "growing.mp4"
    path.write_bytes(data)
    with playback._jobs_lock:
        playback._jobs["growing-test"] = {"cache": path, "status": "preparing"}
    try:
        ranges = playback.growing_ranges(path)
        assert ranges
        assert playback.growing_ranges(path) == ranges
    finally:
        with playback._jobs_lock:
            playback._jobs.pop("growing-test", None)


def test_growing_ranges_without_sidx_returns_empty_list(tmp_path):
    data = (
        _box("ftyp", b"isom")
        + _box("moov", b"m" * 20)
        + _box("moof", b"f" * 7)
        + _box("mdat", b"d" * 30)
    )
    path = tmp_path / "growing.mp4"
    path.write_bytes(data)
    with playback._jobs_lock:
        playback._jobs["no-sidx-test"] = {"cache": path, "status": "preparing"}
    try:
        assert playback.growing_ranges(path) == []
    finally:
        with playback._jobs_lock:
            playback._jobs.pop("no-sidx-test", None)


def test_iter_growing_range_tails_file(tmp_path):
    prefix = (
        _box("ftyp", b"isom")
        + _box("moov", b"m" * 20)
        + _box("sidx", b"s" * 10)
        + _box("moof", b"f" * 7)
        + _box("mdat", b"d" * 30)
    )
    extra = _box("moof", b"g" * 7) + _box("mdat", b"e" * 20)
    path = tmp_path / "growing.mp4"
    path.write_bytes(prefix)
    ranges, _ = playback._scan_leading_boxes(path)
    ranges = playback._merge_ranges(ranges)
    expected = prefix[: ranges[0][0]] + prefix[ranges[-1][1] :]
    with playback._jobs_lock:
        playback._jobs["tail-test"] = {"cache": path, "status": "preparing"}
    generator = playback.iter_growing_range(path, ranges, 0, poll=0.01)
    try:
        first = b""
        while len(first) < len(expected):
            first += next(generator)
        assert first == expected
        with path.open("ab") as handle:
            handle.write(extra)
        assert next(generator) == extra
    finally:
        generator.close()
        with playback._jobs_lock:
            playback._jobs.pop("tail-test", None)


def test_iter_growing_range_complete_file(tmp_path):
    data = (
        _box("ftyp", b"isom")
        + _box("moov", b"m" * 20)
        + _box("sidx", b"s" * 10)
        + _box("moof", b"f" * 7)
        + _box("mdat", b"d" * 30)
    )
    path = tmp_path / "complete.mp4"
    path.write_bytes(data)
    ranges, _ = playback._scan_leading_boxes(path)
    ranges = playback._merge_ranges(ranges)
    expected = data[: ranges[0][0]] + data[ranges[-1][1] :]
    assert b"".join(playback.iter_growing_range(path, ranges, 0)) == expected


def test_transcode_command_uses_fragmented_mp4(monkeypatch):
    monkeypatch.setattr(playback, "_ffmpeg_executable", lambda: "ffmpeg")
    cmd = playback.transcode_command("in.mkv", "out.mp4", ".mkv")
    flags = cmd[cmd.index("-movflags") + 1]
    assert "empty_moov" in flags
    assert "frag_keyframe" in flags
    assert "global_sidx" in flags


def test_start_async_streams_while_transcoding(episode_file, monkeypatch):
    source = episode_file("Naruto", 1, 1, suffix=".mkv")

    def fake_transcode(job_id, target_path, cache):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"x" * 64)
        token = playback.register(cache, source_path=target_path)
        with playback._jobs_lock:
            job = playback._jobs.get(job_id)
            if job is not None:
                job.update(status="ready", progress=100.0, token=token, done_at=0)

    monkeypatch.setattr(playback, "_run_transcode", fake_transcode)
    result = playback.start_async("Naruto", 1, 1)
    assert result["status"] == "streaming"
    assert result["token"]
    path, resolved_source, _label = playback.resolve(result["token"])
    assert path == playback._cache_paths(source)
    assert Path(resolved_source) == source


def test_prepare_range_waits_for_growing_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache.mp4"
    job_id = "job"
    playback.register(cache)
    with playback._jobs_lock:
        playback._jobs[job_id] = {
            "status": "preparing",
            "progress": 0.0,
            "cache": cache,
            "done_at": None,
        }
    monkeypatch.setattr(playback, "STREAM_WAIT_SECONDS", 30)

    def writer():
        time.sleep(0.2)
        cache.write_bytes(b"x" * 8)

    threading.Thread(target=writer, daemon=True).start()
    playback.prepare_range(cache, 4)
    assert cache.stat().st_size >= 8


def test_prepare_range_raises_when_job_fails(tmp_path, monkeypatch):
    cache = tmp_path / "cache.mp4"
    monkeypatch.setattr(playback, "STREAM_WAIT_SECONDS", 30)
    job = {"status": "preparing", "error": None, "progress": 0.0, "cache": cache, "done_at": None}
    monkeypatch.setattr(playback, "_jobs", {"job": job})

    def failer():
        time.sleep(0.2)
        job.update(status="error", error="boom")

    threading.Thread(target=failer, daemon=True).start()
    with pytest.raises(library.LibraryError):
        playback.prepare_range(cache, 0)


def test_playback_progress_roundtrip(client, episode_file):
    episode_file("Naruto", 1, 1)
    response = client.post(
        "/api/library/progress",
        json={"folder": "Naruto", "season": 1, "episode": 1, "position": 123},
    )
    assert response.status_code == 200
    body = client.get("/api/library/title?folder=Naruto").get_json()
    assert body["positions"] == {"1": {"1": 123}}


def _video(codec="h264", profile="High", pix_fmt="yuv420p"):
    return media.VideoStream(
        index=0, codec=codec, profile=profile, pix_fmt=pix_fmt, duration=60.0
    )


def _audio(index, ordinal, codec="aac", default=False):
    return media.AudioStream(
        index=index, ordinal=ordinal, codec=codec, default=default, duration=60.0
    )


def _info(video=None, audios=None, subtitles=None):
    return media.MediaInfo(
        path=Path("episode.mkv"),
        container="matroska",
        duration=60.0,
        video=video or _video(),
        audios=audios if audios is not None else [_audio(1, 0)],
        subtitles=subtitles or [],
    )


def test_choose_plan_direct_for_browser_mp4():
    plan = media.choose_plan(_info(), ".mp4")
    assert plan.mode == "direct"
    assert plan.mime == "video/mp4"


def test_choose_plan_remuxes_h264_mkv():
    plan = media.choose_plan(_info(), ".mkv")
    assert plan.mode == "remux"
    assert plan.video_copy is True
    assert plan.audio_copy is True


def test_choose_plan_transcodes_hevc_10bit():
    info = _info(video=_video(codec="hevc", profile="Main 10", pix_fmt="yuv420p10le"))
    plan = media.choose_plan(info, ".mkv")
    assert plan.mode == "transcode"
    assert plan.video_copy is False


def test_choose_plan_remuxes_requested_second_audio_track():
    info = _info(
        audios=[_audio(1, 0, default=True), _audio(2, 1, codec="ac3")]
    )
    plan = media.choose_plan(info, ".mp4", audio_ordinal=1)
    assert plan.mode == "remux"
    assert plan.audio.ordinal == 1
    assert plan.audio_copy is False


def test_choose_plan_remuxes_mp4_with_text_subtitles():
    subtitle = media.SubtitleStream(index=2, codec="ass", language="en")
    plan = media.choose_plan(_info(subtitles=[subtitle]), ".mp4")
    assert plan.mode == "remux"


def test_build_command_maps_audio_and_skips_subtitles(monkeypatch):
    monkeypatch.setattr(media, "detect_encoder", lambda *a, **k: "libx264")
    info = _info(audios=[_audio(1, 0), _audio(2, 1)])
    plan = media.choose_plan(info, ".mkv", audio_ordinal=1)
    cmd = media.build_command("in.mkv", "out.mp4", plan, info=info, ffmpeg="ffmpeg")
    assert cmd[cmd.index("-map") + 1] == "0:V:0"
    assert "0:2" in cmd
    assert "-sn" in cmd
    assert "mov_text" not in cmd
    assert cmd[-1] == "out.mp4"


def _box(kind, payload):
    return (8 + len(payload)).to_bytes(4, "big") + kind.encode("latin1") + payload


def _fullbox(version, payload):
    return bytes([version, 0, 0, 0]) + payload


def _mvhd(duration, timescale=1000):
    payload = b"\0" * 8 + timescale.to_bytes(4, "big") + duration.to_bytes(4, "big")
    return _box("mvhd", _fullbox(0, payload + b"\0" * 80))


def _tkhd(duration):
    payload = b"\0" * 8 + (1).to_bytes(4, "big") + b"\0" * 4
    payload += duration.to_bytes(4, "big") + b"\0" * 60
    return _box("tkhd", _fullbox(0, payload))


def _mdhd(duration, timescale=1000):
    payload = b"\0" * 8 + timescale.to_bytes(4, "big") + duration.to_bytes(4, "big")
    return _box("mdhd", _fullbox(0, payload + b"\0" * 4))


def _trak(handler, duration):
    mdia = _box("mdia", _mdhd(duration) + _box("hdlr", b"\0" * 8 + handler.encode() + b"\0" * 12))
    return _box("trak", _tkhd(duration) + mdia)


def _uint(buffer, offset, width=4):
    return int.from_bytes(buffer[offset : offset + width], "big")


def test_patch_moov_duration_fills_mvhd_tkhd_and_mdhd():
    moov = _box(
        "moov",
        _mvhd(0) + _trak("vide", 0) + _trak("soun", 0),
    )
    patched = media.patch_moov_duration(moov, 120.5)
    assert len(patched) == len(moov)

    moov_box = media._find_box(patched, "moov")
    mvhd = media._find_box(patched, "mvhd", moov_box[1], moov_box[2])
    assert _uint(patched, mvhd[1] + 16) == 120500
    traks = [
        box
        for box in media._iter_boxes(patched, moov_box[1], moov_box[2])
        if box[0] == "trak"
    ]
    for _kind, _start, content, end in traks:
        trak_children = list(media._iter_boxes(patched, content, end))
        tkhd = next(box for box in trak_children if box[0] == "tkhd")
        mdia = next(box for box in trak_children if box[0] == "mdia")
        mdhd = next(
            box for box in media._iter_boxes(patched, mdia[2], mdia[3]) if box[0] == "mdhd"
        )
        assert _uint(patched, tkhd[2] + 20) == 120500
        assert _uint(patched, mdhd[2] + 16) == 120500


def test_read_and_write_moov_header(tmp_path):
    ftyp = _box("ftyp", b"isom\0\0\0\0")
    moov = _box("moov", _mvhd(0) + _trak("vide", 0))
    path = tmp_path / "cache.mp4"
    path.write_bytes(ftyp + moov + b"\0" * 32)

    offset, found = media.read_moov(path, timeout=1)
    assert offset == len(ftyp)
    assert found == moov

    header = media.patch_moov_duration(found, 30.0)
    assert media.write_header(path, header, offset)
    reread = path.read_bytes()[offset : offset + len(moov)]
    assert reread == header


def test_stream_serves_growing_cache(client, episode_file, monkeypatch):
    episode_file("Naruto", 1, 1, suffix=".mkv")

    def fake_transcode(job_id, target_path, cache):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"y" * 32)
        token = playback.register(cache, source_path=target_path)
        with playback._jobs_lock:
            job = playback._jobs.get(job_id)
            if job is not None:
                job.update(status="ready", progress=100.0, token=token, done_at=0)

    monkeypatch.setattr(playback, "_run_transcode", fake_transcode)
    result = client.post(
        "/api/library/play", json={"folder": "Naruto", "season": 1, "episode": 1}
    ).get_json()
    assert result["status"] == "streaming"
    response = client.get(
        f"/api/library/stream?token={result['token']}", headers={"Range": "bytes=0-7"}
    )
    assert response.status_code in (200, 206)
    assert len(response.data) >= 8


class _FakeProcess:
    pid = 4242

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _job(cache, status="preparing", **extra):
    job = {
        "status": status,
        "stage": status,
        "progress": 0.0,
        "cache": Path(cache),
        "token": "token",
        "done_at": None,
        "created_at": time.time(),
        "last_activity": time.time(),
        "cancel_requested": False,
        "cancel_requested_at": None,
        "cancel_reason": None,
    }
    job.update(extra)
    return job


def test_cancel_job_terminates_ffmpeg(tmp_path, monkeypatch):
    cache = tmp_path / "partial.mp4"
    cache.write_bytes(b"partial")
    process = _FakeProcess()
    job_id = "cancel-test"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache)
    with playback._processes_lock:
        playback._processes[job_id] = process
    try:
        result = playback.cancel_job(job_id, wait=False)
        assert result["status"] == "cancelling"
        assert process.terminated is True
        assert playback._cancel_requested(job_id) is True

        playback._finish_cancelled(job_id, cache)
        result = playback.job_status(job_id)
        assert result["status"] == "cancelled"
        assert result["error"]
        assert not cache.exists()
    finally:
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)
        with playback._processes_lock:
            playback._processes.pop(job_id, None)


def test_cancel_job_waits_for_worker_cleanup(tmp_path):
    cache = tmp_path / "partial.mp4"
    cache.write_bytes(b"partial")
    job_id = "cancel-wait-test"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache)

    def worker():
        while not playback._cancel_requested(job_id):
            time.sleep(0.01)
        playback._finish_cancelled(job_id, cache)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        result = playback.cancel_job(job_id)
        assert result["status"] == "cancelled"
        assert not cache.exists()
    finally:
        thread.join(timeout=2)
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)


def test_watchdog_cancels_stalled_then_abandons_job(tmp_path):
    cache = tmp_path / "stalled.mp4"
    cache.write_bytes(b"partial")
    job_id = "watchdog-test"
    now = time.time()
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(
            cache,
            last_activity=now - playback.JOB_STALL_SECONDS - 1,
        )
    try:
        playback._watchdog_tick(now=now)
        result = playback.job_status(job_id)
        assert result["status"] == "cancelling"

        playback._watchdog_tick(now=time.time() + playback.CANCEL_KILL_SECONDS + 1)
        result = playback.job_status(job_id)
        assert result["status"] == "error"
        assert "timed out" in result["error"]
        assert not cache.exists()
    finally:
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)


def test_shutdown_abandons_active_jobs(tmp_path, monkeypatch):
    cache = tmp_path / "shutdown.mp4"
    cache.write_bytes(b"partial")
    process = _FakeProcess()
    job_id = "shutdown-test"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache)
    with playback._processes_lock:
        playback._processes[job_id] = process
    monkeypatch.setattr(playback, "SHUTDOWN_WAIT_SECONDS", 0.1)
    try:
        playback.shutdown()
        result = playback.job_status(job_id)
        assert result["status"] == "cancelled"
        assert process.terminated is True
        assert not cache.exists()
    finally:
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)
        with playback._processes_lock:
            playback._processes.pop(job_id, None)


def test_growing_ranges_rejects_cancelling_job(tmp_path):
    cache = tmp_path / "cancelling.mp4"
    cache.write_bytes(b"ftyp" + b"\0" * 4)
    job_id = "cancelling-test"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache, status="cancelling")
    try:
        with pytest.raises(library.LibraryError):
            playback.growing_ranges(cache)
    finally:
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)


def test_start_async_rejects_cancelling_cache(episode_file, monkeypatch):
    source = episode_file("Naruto", 1, 1, suffix=".mkv")
    cache = playback._cache_paths(source)
    job_id = "existing-cancel"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache, status="cancelling")
    try:
        with pytest.raises(library.LibraryError):
            playback.start_async("Naruto", 1, 1)
    finally:
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)


def test_cancel_api(client, tmp_path, monkeypatch):
    cache = tmp_path / "api.mp4"
    cache.write_bytes(b"partial")
    job_id = "api-cancel-test"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache)
    monkeypatch.setattr(playback, "CANCEL_WAIT_SECONDS", 0.1)
    try:
        response = client.post("/api/library/play/cancel", json={"job": job_id})
        assert response.status_code == 200
        assert response.get_json()["status"] == "cancelling"
    finally:
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)


def test_cancel_api_requires_job(client):
    response = client.post("/api/library/play/cancel", json={})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Cache clearing
# ---------------------------------------------------------------------------
def test_cache_stats_counts_files_and_sidecars(downloads):
    directory = playback._stream_cache_dir()
    directory.mkdir(parents=True)
    (directory / "abc.mp4").write_bytes(b"x" * 100)
    (directory / "abc.json").write_text("{}")
    (directory / "abc.subs").mkdir()
    (directory / "abc.subs" / "1.vtt").write_text("vtt")

    stats = playback.cache_stats()
    assert stats["path"] == str(directory)
    assert stats["files"] == 3
    assert stats["bytes"] == 100 + 2 + 3


def test_clear_cache_empties_the_directory(downloads):
    directory = playback._stream_cache_dir()
    directory.mkdir(parents=True)
    (directory / "abc.mp4").write_bytes(b"x" * 100)
    (directory / "abc.subs").mkdir()
    (directory / "abc.subs" / "1.vtt").write_text("vtt")

    result = playback.clear_cache()
    assert result["removed_files"] == 2
    assert result["removed_bytes"] == 100 + 3
    assert result["kept"] == 0
    assert playback.cache_stats()["files"] == 0
    assert list(directory.iterdir()) == []


def test_clear_cache_waits_for_a_cancelled_job(downloads, monkeypatch):
    cache = playback._stream_cache_dir() / "active.mp4"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"partial")
    process = _FakeProcess()
    job_id = "clear-wait-test"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache)
    with playback._processes_lock:
        playback._processes[job_id] = process
    monkeypatch.setattr(playback, "CLEAR_WAIT_SECONDS", 5)

    def worker():
        while not playback._cancel_requested(job_id):
            time.sleep(0.01)
        playback._finish_cancelled(job_id, cache)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        result = playback.clear_cache()
        assert process.terminated is True
        assert result["kept"] == 0
        assert not cache.exists()
    finally:
        thread.join(timeout=2)
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)
        with playback._processes_lock:
            playback._processes.pop(job_id, None)


def test_clear_cache_skips_what_a_stopped_job_still_holds(downloads, monkeypatch):
    cache = playback._stream_cache_dir() / "stuck.mp4"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"partial")
    job_id = "clear-stuck-test"
    with playback._jobs_lock:
        playback._jobs[job_id] = _job(cache)
    monkeypatch.setattr(playback, "CLEAR_WAIT_SECONDS", 0.1)
    try:
        result = playback.clear_cache()
        assert result["kept"] == 1
        assert cache.exists()
        # a job that is gone stops holding its file back
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)
        result = playback.clear_cache()
        assert result["kept"] == 0
        assert not cache.exists()
    finally:
        with playback._jobs_lock:
            playback._jobs.pop(job_id, None)
