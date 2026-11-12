"""Probing, browser compatibility and ffmpeg command building for playback.

The browser can only play a small slice of what ends up in a library: H.264
8-bit video with AAC/MP3 audio in MP4, or VP8/VP9/AV1 with Opus/Vorbis in WebM.
Everything else has to be remuxed (cheap, lossless) or transcoded. This module
answers *what* to do with a file and builds the ffmpeg command for it; the
session, cache and streaming logic lives in playback.py.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..logger import get_logger

logger = get_logger(__name__)

# Text subtitle codecs ffmpeg can turn into WebVTT. Bitmap formats (PGS, DVD,
# DVB) are deliberately absent: they cannot become text, and feeding them to
# the mov_text encoder is what used to kill whole transcodes.
TEXT_SUBTITLE_CODECS = frozenset(
    {
        "ass",
        "ssa",
        "subrip",
        "srt",
        "mov_text",
        "webvtt",
        "text",
        "microdvd",
        "subviewer",
        "subviewer1",
        "sami",
        "realtext",
        "jacosub",
        "mpl2",
        "pjs",
        "vplayer",
        "stl",
        "scc",
        "eia_608",
    }
)

BITMAP_SUBTITLE_CODECS = frozenset(
    {
        "hdmv_pgs_subtitle",
        "dvd_subtitle",
        "dvb_subtitle",
        "dvb_teletext",
        "xsub",
        "pgssub",
    }
)

# H.264 profiles a browser can decode in software. High 10 and the 4:2:2/4:4:4
# profiles are excluded, as are HDR sources (see is_hdr).
_UNSUPPORTED_H264_PROFILES = frozenset(
    {
        "high 10",
        "high 10 intra",
        "high 4:2:2",
        "high 4:2:2 intra",
        "high 4:4:4 predictive",
        "high 4:4:4 intra",
        "cavlc 4:4:4 intra",
    }
)

_MP4_VIDEO_CODECS = frozenset({"h264"})
_MP4_AUDIO_CODECS = frozenset({"aac", "mp3"})
_WEBM_VIDEO_CODECS = frozenset({"vp8", "vp9", "av1"})
_WEBM_AUDIO_CODECS = frozenset({"opus", "vorbis"})

_HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})

_LANGUAGE_LABELS = {
    "en": "English",
    "eng": "English",
    "ja": "Japanese",
    "jpn": "Japanese",
    "de": "German",
    "deu": "German",
    "ger": "German",
    "fr": "French",
    "fra": "French",
    "fre": "French",
    "es": "Spanish",
    "spa": "Spanish",
    "it": "Italian",
    "ita": "Italian",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
    "ar": "Arabic",
    "ara": "Arabic",
    "hi": "Hindi",
    "hin": "Hindi",
    "id": "Indonesian",
    "ind": "Indonesian",
    "ko": "Korean",
    "kor": "Korean",
    "zh": "Chinese",
    "zho": "Chinese",
    "chi": "Chinese",
    "th": "Thai",
    "tha": "Thai",
    "tr": "Turkish",
    "tur": "Turkish",
    "pl": "Polish",
    "pol": "Polish",
    "nl": "Dutch",
    "nld": "Dutch",
    "dut": "Dutch",
    "sv": "Swedish",
    "swe": "Swedish",
    "no": "Norwegian",
    "nor": "Norwegian",
    "nb": "Norwegian",
    "da": "Danish",
    "dan": "Danish",
    "fi": "Finnish",
    "fin": "Finnish",
    "cs": "Czech",
    "ces": "Czech",
    "cze": "Czech",
    "hu": "Hungarian",
    "hun": "Hungarian",
    "el": "Greek",
    "ell": "Greek",
    "gre": "Greek",
    "he": "Hebrew",
    "heb": "Hebrew",
    "uk": "Ukrainian",
    "ukr": "Ukrainian",
    "vi": "Vietnamese",
    "vie": "Vietnamese",
    "tl": "Filipino",
    "fil": "Filipino",
    "ms": "Malay",
    "may": "Malay",
}

_UNDETERMINED = frozenset({"", "und", "unknown", "none", "n/a"})


def ffmpeg_executable():
    from ..autodeps import DependencyManager

    return str(DependencyManager().fetch_binary("ffmpeg"))


def ffprobe_executable():
    """ffprobe next to the managed ffmpeg, else whatever is on PATH."""
    try:
        ffmpeg = Path(ffmpeg_executable())
    except Exception:
        ffmpeg = None
    if ffmpeg is not None:
        name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        sibling = ffmpeg.with_name(name)
        if sibling.exists():
            return str(sibling)
    return shutil.which("ffprobe")


def language_label(code):
    """Human label for an ISO 639-1/2 language tag."""
    code = str(code or "").strip().lower()
    if code in _UNDETERMINED:
        return ""
    return _LANGUAGE_LABELS.get(code, code.upper())


def track_label(language, title, fallback):
    """One readable label from a track's language and title tags."""
    parts = []
    language = language_label(language)
    title = str(title or "").strip()
    if language:
        parts.append(language)
    if title and title.lower() not in {language.lower(), "und"}:
        parts.append(title)
    return " — ".join(parts) or fallback


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
@dataclass
class VideoStream:
    index: int
    codec: str
    profile: str = ""
    pix_fmt: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    color_transfer: str = ""
    color_primaries: str = ""
    duration: float = 0.0


@dataclass
class AudioStream:
    index: int
    ordinal: int
    codec: str
    channels: int = 2
    language: str = ""
    title: str = ""
    default: bool = False
    duration: float = 0.0


@dataclass
class SubtitleStream:
    index: int
    codec: str
    language: str = ""
    title: str = ""
    default: bool = False
    forced: bool = False

    @property
    def text(self):
        return self.codec in TEXT_SUBTITLE_CODECS


@dataclass
class MediaInfo:
    path: Path
    container: str = ""
    duration: float = 0.0
    video: VideoStream | None = None
    audios: list[AudioStream] = field(default_factory=list)
    subtitles: list[SubtitleStream] = field(default_factory=list)


_probe_cache = {}
_probe_lock = threading.Lock()
_PROBE_CACHE_SIZE = 64


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fps(value):
    if not value or "/" not in str(value):
        return _as_float(value)
    numerator, denominator = str(value).split("/", 1)
    denominator = _as_float(denominator, 1.0) or 1.0
    return _as_float(numerator) / denominator


def probe(path):
    """ffprobe one file, cached by (path, mtime, size). None when unprobeable."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _probe_lock:
        cached = _probe_cache.get(key)
    if cached is not None:
        return cached

    executable = ffprobe_executable()
    if not executable:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffprobe failed for %s: %s", path.name, exc)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        logger.warning("ffprobe could not read %s", path.name)
        return None

    try:
        payload = json.loads(result.stdout)
    except ValueError:
        logger.warning("ffprobe returned invalid JSON for %s", path.name)
        return None

    info = _media_info_from_payload(path, payload)
    with _probe_lock:
        if len(_probe_cache) >= _PROBE_CACHE_SIZE:
            _probe_cache.pop(next(iter(_probe_cache)))
        _probe_cache[key] = info
    return info


def _media_info_from_payload(path, payload):
    streams = payload.get("streams") or []
    container = str((payload.get("format") or {}).get("format_name") or "")
    duration = _as_float((payload.get("format") or {}).get("duration"))

    info = MediaInfo(path=Path(path), container=container, duration=duration)
    audio_ordinal = 0
    for stream in streams:
        kind = stream.get("codec_type")
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        stream_duration = _as_float(stream.get("duration"), duration)
        if kind == "video":
            if info.video is not None or disposition.get("attached_pic"):
                continue
            info.video = VideoStream(
                index=_as_int(stream.get("index")),
                codec=str(stream.get("codec_name") or ""),
                profile=str(stream.get("profile") or ""),
                pix_fmt=str(stream.get("pix_fmt") or ""),
                width=_as_int(stream.get("width")),
                height=_as_int(stream.get("height")),
                fps=_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                color_transfer=str(stream.get("color_transfer") or ""),
                color_primaries=str(stream.get("color_primaries") or ""),
                duration=stream_duration,
            )
        elif kind == "audio":
            info.audios.append(
                AudioStream(
                    index=_as_int(stream.get("index")),
                    ordinal=audio_ordinal,
                    codec=str(stream.get("codec_name") or ""),
                    channels=_as_int(stream.get("channels"), 2),
                    language=str(tags.get("language") or ""),
                    title=str(tags.get("title") or ""),
                    default=bool(disposition.get("default")),
                    duration=stream_duration,
                )
            )
            audio_ordinal += 1
        elif kind == "subtitle":
            info.subtitles.append(
                SubtitleStream(
                    index=_as_int(stream.get("index")),
                    codec=str(stream.get("codec_name") or ""),
                    language=str(tags.get("language") or ""),
                    title=str(tags.get("title") or ""),
                    default=bool(disposition.get("default")),
                    forced=bool(disposition.get("forced")),
                )
            )
    if not info.duration:
        info.duration = max(
            [info.video.duration if info.video else 0.0]
            + [stream.duration for stream in info.audios]
        )
    return info


# ---------------------------------------------------------------------------
# Browser compatibility
# ---------------------------------------------------------------------------
def is_hdr(video):
    if video is None:
        return False
    return (
        video.color_transfer.lower() in _HDR_TRANSFERS
        or video.color_primaries.lower() == "bt2020"
    )


def is_mp4_video(video):
    """H.264 a browser can decode without help."""
    if video is None or video.codec not in _MP4_VIDEO_CODECS:
        return False
    if video.profile.strip().lower() in _UNSUPPORTED_H264_PROFILES:
        return False
    if video.pix_fmt not in {"yuv420p", "yuvj420p"}:
        return False
    return not is_hdr(video)


def is_webm_video(video):
    return video is not None and video.codec in _WEBM_VIDEO_CODECS


def is_mp4_audio(audio):
    return audio is not None and audio.codec in _MP4_AUDIO_CODECS


def is_webm_audio(audio):
    return audio is not None and audio.codec in _WEBM_AUDIO_CODECS


def default_audio(info, ordinal=None):
    """The requested audio ordinal, else the file's default, else the first."""
    if info is None or not info.audios:
        return None
    if ordinal is not None:
        for audio in info.audios:
            if audio.ordinal == ordinal:
                return audio
    for audio in info.audios:
        if audio.default:
            return audio
    return info.audios[0]


def has_text_subtitles(info):
    return any(subtitle.text for subtitle in (info.subtitles if info else []))


def _direct_mime(ext, info, audio):
    if ext in (".mp4", ".m4v") and is_mp4_video(info.video) and is_mp4_audio(audio):
        return "video/mp4"
    if ext == ".webm" and is_webm_video(info.video) and is_webm_audio(audio):
        return "video/webm"
    return None


@dataclass
class PlaybackPlan:
    mode: str
    container: str
    mime: str
    audio: AudioStream | None = None
    video_copy: bool = False
    audio_copy: bool = False
    reason: str = ""

    @property
    def video_transcode(self):
        return not self.video_copy

    @property
    def audio_transcode(self):
        return self.audio is not None and not self.audio_copy


def choose_plan(info, ext, audio_ordinal=None, force_transcode=False):
    """Decide direct / remux / transcode for one file."""
    name = str(ext or "").lower()
    audio = default_audio(info, audio_ordinal)

    if info is None:
        # Without a probe there is no way to know the codecs. MP4/WebM get one
        # direct attempt (the watch page falls back to a transcode on error),
        # everything else is transcoded blind.
        if name in (".mp4", ".m4v", ".webm") and not force_transcode:
            mime = "video/webm" if name == ".webm" else "video/mp4"
            return PlaybackPlan(
                mode="direct",
                container="webm" if name == ".webm" else "mp4",
                mime=mime,
                audio=audio,
                video_copy=True,
                audio_copy=True,
                reason="no probe, trusting the container",
            )
        return PlaybackPlan(
            mode="transcode",
            container="mp4",
            mime="video/mp4",
            audio=audio,
            reason="no probe, forcing a transcode",
        )

    if force_transcode:
        return PlaybackPlan(
            mode="transcode",
            container="mp4",
            mime="video/mp4",
            audio=audio,
            reason="requested by the player",
        )

    # Direct play needs no text subtitles: otherwise the MP4 would either show
    # two copies (native + sidecar) or none at all. A remux strips them and is
    # cheap enough. Browsers also cannot switch between MP4 audio tracks, so a
    # requested non-first track needs a remux that maps it as the only one.
    direct_mime = _direct_mime(name, info, audio)
    audio_is_first = audio is None or audio.ordinal == 0
    if direct_mime and not has_text_subtitles(info) and audio_is_first:
        return PlaybackPlan(
            mode="direct",
            container="webm" if direct_mime == "video/webm" else "mp4",
            mime=direct_mime,
            audio=audio,
            video_copy=True,
            audio_copy=True,
            reason="already browser compatible",
        )

    video_copy = is_mp4_video(info.video)
    audio_copy = audio is None or is_mp4_audio(audio)
    return PlaybackPlan(
        mode="remux" if video_copy else "transcode",
        container="mp4",
        mime="video/mp4",
        audio=audio,
        video_copy=video_copy,
        audio_copy=audio_copy,
        reason=(
            "browser-safe video, container/audio needs remuxing"
            if video_copy
            else f"{info.video.codec if info.video else 'video'} is not browser safe"
        ),
    )


# ---------------------------------------------------------------------------
# Encoder detection
# ---------------------------------------------------------------------------
_ENCODERS = (
    "libx264",
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "h264_videotoolbox",
    "h264_vaapi",
)

_encoder_cache = {"value": None}
_encoder_lock = threading.Lock()
_filter_cache = {}


def _platform_encoder_candidates():
    if os.name == "nt":
        return ["h264_nvenc", "h264_qsv", "h264_amf"]
    if os.uname().sysname == "Darwin":
        return ["h264_videotoolbox"]
    return ["h264_nvenc", "h264_qsv"]


def _encoder_works(ffmpeg, encoder):
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=64x64:d=0.04",
                "-frames:v",
                "1",
                "-an",
                "-c:v",
                encoder,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def detect_encoder(ffmpeg=None):
    """Best working H.264 encoder, cached for the process.

    ANIWORLD_PLAYBACK_ENCODER pins one; anything unknown or unavailable falls
    back to libx264. Hardware encoders are verified with a one-frame encode
    because having the encoder compiled in says nothing about the hardware.
    """
    with _encoder_lock:
        if _encoder_cache["value"] is not None:
            return _encoder_cache["value"]

        forced = os.environ.get("ANIWORLD_PLAYBACK_ENCODER", "auto").strip().lower()
        if forced and forced != "auto":
            chosen = forced if forced in _ENCODERS else "libx264"
            if chosen != "libx264":
                executable = ffmpeg or ffmpeg_executable()
                if not _encoder_works(executable, chosen):
                    logger.warning(
                        "Playback encoder %s does not work here, using libx264",
                        chosen,
                    )
                    chosen = "libx264"
            _encoder_cache["value"] = chosen
            return chosen

        executable = ffmpeg or ffmpeg_executable()
        for candidate in _platform_encoder_candidates():
            if _encoder_works(executable, candidate):
                logger.info("Playback transcodes will use %s", candidate)
                _encoder_cache["value"] = candidate
                return candidate
        _encoder_cache["value"] = "libx264"
        return "libx264"


def _ffmpeg_has_filter(ffmpeg, name):
    key = (ffmpeg, name)
    if key in _filter_cache:
        return _filter_cache[key]
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        found = re.search(rf"\b{re.escape(name)}\b", result.stdout or "") is not None
    except (OSError, subprocess.SubprocessError):
        found = False
    _filter_cache[key] = found
    return found


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------
def _hdr_filter(ffmpeg):
    if _ffmpeg_has_filter(ffmpeg, "zscale") and _ffmpeg_has_filter(ffmpeg, "tonemap"):
        return (
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
        )
    return "format=yuv420p"


def _video_filters(ffmpeg, info):
    filters = []
    if info is not None and is_hdr(info.video):
        filters.append(_hdr_filter(ffmpeg))
    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return ",".join(filters)


def encoder_args(encoder, preset=None, crf=None):
    preset = preset or os.environ.get("ANIWORLD_PLAYBACK_PRESET", "superfast")
    crf = crf or os.environ.get("ANIWORLD_PLAYBACK_CRF", "23")
    if encoder == "libx264":
        return ["-preset", preset, "-crf", str(crf), "-tune", "animation"]
    if encoder == "h264_nvenc":
        return ["-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-preset", "veryfast", "-global_quality", str(crf)]
    if encoder == "h264_amf":
        return [
            "-quality",
            "balanced",
            "-rc",
            "cqp",
            "-qp_i",
            str(crf),
            "-qp_p",
            str(crf),
        ]
    if encoder == "h264_videotoolbox":
        return ["-q:v", "55"]
    if encoder == "h264_vaapi":
        return ["-qp", str(crf)]
    return ["-preset", preset, "-crf", str(crf), "-tune", "animation"]


def build_command(
    input_path,
    output_path,
    plan,
    info=None,
    ffmpeg=None,
    encoder=None,
    audio_bitrate=None,
):
    """ffmpeg argv for one plan. Video output is always fragmented MP4."""
    ffmpeg = ffmpeg or ffmpeg_executable()
    encoder = encoder or detect_encoder(ffmpeg)
    audio_bitrate = audio_bitrate or os.environ.get(
        "ANIWORLD_PLAYBACK_AUDIO_BITRATE", "160k"
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-nostats",
        "-progress",
        "pipe:2",
        "-i",
        str(input_path),
        "-map",
        "0:V:0",
    ]

    if plan.audio is not None:
        command += ["-map", f"0:{plan.audio.index}"]
    elif info is not None:
        command += ["-an"]
    else:
        # Without a probe, keep whatever default audio ffmpeg finds.
        command += ["-map", "0:a:0?"]

    if plan.video_copy:
        command += ["-c:v", "copy"]
    else:
        command += ["-c:v", encoder, *encoder_args(encoder)]
        command += ["-pix_fmt", "yuv420p"]
        command += ["-vf", _video_filters(ffmpeg, info)]

    if plan.audio is not None:
        if plan.audio_copy:
            command += ["-c:a", "copy"]
        else:
            command += ["-c:a", "aac", "-b:a", audio_bitrate]
            if plan.audio.channels > 2:
                command += ["-ac", "2"]
        language = plan.audio.language.strip()
        if language:
            command += ["-metadata:s:a:0", f"language={language}"]
        title = plan.audio.title.strip()
        if title:
            command += ["-metadata:s:a:0", f"title={title}"]
    elif info is None:
        command += ["-c:a", "aac", "-b:a", audio_bitrate]

    command += [
        "-sn",
        "-dn",
        # MP4 stores chapters as an extra data track no browser shows.
        "-map_chapters",
        "-1",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof+global_sidx",
        "-frag_duration",
        "4000000",
        "-max_muxing_queue_size",
        "2048",
        "-f",
        "mp4",
        "-y",
        str(output_path),
    ]
    return command


def subtitle_command(input_path, stream_index, output_path, ffmpeg=None):
    return [
        ffmpeg or ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(input_path),
        "-map",
        f"0:{int(stream_index)}",
        "-c:s",
        "webvtt",
        "-f",
        "webvtt",
        "-y",
        str(output_path),
    ]


# ---------------------------------------------------------------------------
# Fragmented MP4 duration
# ---------------------------------------------------------------------------
def _iter_boxes(buffer, start=0, end=None):
    end = len(buffer) if end is None else end
    position = start
    while position + 8 <= end:
        size = int.from_bytes(buffer[position : position + 4], "big")
        kind = bytes(buffer[position + 4 : position + 8]).decode("latin1")
        header = 8
        if size == 1:
            if position + 16 > end:
                return
            size = int.from_bytes(buffer[position + 8 : position + 16], "big")
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            return
        yield kind, position, position + header, position + size
        position += size


def _find_box(buffer, kind, start=0, end=None):
    for found_kind, box_start, content_start, box_end in _iter_boxes(
        buffer, start, end
    ):
        if found_kind == kind:
            return box_start, content_start, box_end
    return None


def _fullbox_version(buffer, content_start):
    return buffer[content_start]


def _set_uint(buffer, offset, value, width):
    value = max(0, min(int(value), (1 << (width * 8)) - 1))
    buffer[offset : offset + width] = value.to_bytes(width, "big")


def _read_timescale(buffer, content_start, version):
    if version == 1:
        return int.from_bytes(buffer[content_start + 20 : content_start + 24], "big")
    return int.from_bytes(buffer[content_start + 12 : content_start + 16], "big")


def _patch_mvhd(buffer, content_start, duration):
    version = _fullbox_version(buffer, content_start)
    timescale = _read_timescale(buffer, content_start, version) or 1000
    if version == 1:
        _set_uint(buffer, content_start + 24, duration * timescale, 8)
    else:
        _set_uint(buffer, content_start + 16, duration * timescale, 4)
    return timescale


def _patch_mdhd(buffer, content_start, duration):
    version = _fullbox_version(buffer, content_start)
    timescale = _read_timescale(buffer, content_start, version) or 1000
    if version == 1:
        _set_uint(buffer, content_start + 24, duration * timescale, 8)
    else:
        _set_uint(buffer, content_start + 16, duration * timescale, 4)


def _patch_tkhd(buffer, content_start, duration, movie_timescale):
    version = _fullbox_version(buffer, content_start)
    if version == 1:
        _set_uint(buffer, content_start + 28, duration * movie_timescale, 8)
    else:
        _set_uint(buffer, content_start + 20, duration * movie_timescale, 4)


def _handler_type(buffer, hdlr):
    if hdlr is None:
        return ""
    _kind, _box_start, content_start, _box_end = hdlr
    return bytes(buffer[content_start + 8 : content_start + 12]).decode("latin1")


def patch_moov_duration(moov, duration, video_duration=None, audio_duration=None):
    """Put the real duration into an empty-moov fMP4 header.

    ffmpeg writes duration 0 into mvhd/tkhd/mdhd for a fragmented output, so
    the browser shows 0:00 until the trailer arrives. Overwriting the existing
    fields keeps every byte offset intact, which matters because the file is
    being streamed while it is written.
    """
    if not moov or duration <= 0:
        return moov
    buffer = bytearray(moov)
    moov_box = _find_box(buffer, "moov")
    if moov_box is None:
        return bytes(buffer)
    _moov_start, moov_content, moov_end = moov_box
    moov_children = list(_iter_boxes(buffer, moov_content, moov_end))
    mvhd = next((box for box in moov_children if box[0] == "mvhd"), None)
    if mvhd is None:
        return bytes(buffer)
    _kind, _box_start, content_start, _box_end = mvhd
    movie_timescale = _patch_mvhd(buffer, content_start, duration)

    for kind, _trak_start, trak_content, trak_end in moov_children:
        if kind != "trak":
            continue
        trak_children = list(_iter_boxes(buffer, trak_content, trak_end))
        tkhd = next((box for box in trak_children if box[0] == "tkhd"), None)
        mdia = next((box for box in trak_children if box[0] == "mdia"), None)
        mdhd = None
        hdlr = None
        if mdia is not None:
            mdia_children = list(_iter_boxes(buffer, mdia[2], mdia[3]))
            mdhd = next((box for box in mdia_children if box[0] == "mdhd"), None)
            hdlr = next((box for box in mdia_children if box[0] == "hdlr"), None)
        handler = _handler_type(buffer, hdlr)
        if handler == "vide":
            track_duration = video_duration or duration
        elif handler == "soun":
            track_duration = audio_duration or duration
        else:
            track_duration = duration

        if tkhd is not None:
            _patch_tkhd(buffer, tkhd[2], track_duration, movie_timescale)
        if mdhd is not None:
            _patch_mdhd(buffer, mdhd[2], track_duration)

    mvex = next((box for box in moov_children if box[0] == "mvex"), None)
    if mvex is not None:
        mehd = _find_box(buffer, "mehd", mvex[2], mvex[3])
        if mehd is not None:
            _box_start, content_start, _box_end = mehd
            version = _fullbox_version(buffer, content_start)
            if version == 1:
                _set_uint(buffer, content_start + 4, duration * movie_timescale, 8)
            else:
                _set_uint(buffer, content_start + 4, duration * movie_timescale, 4)
    return bytes(buffer)


def read_moov(path, timeout=15.0):
    """Wait for ffmpeg to write ftyp+moov, return (moov_offset, moov_bytes).

    Returns (0, None) when the header does not show up in time.
    """
    path = Path(path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size >= 16:
            try:
                with open(path, "rb") as handle:
                    head = handle.read(min(size, 64 * 1024))
            except OSError:
                head = b""
            if head:
                for kind, box_start, _content_start, box_end in _iter_boxes(head):
                    if kind == "moov":
                        if size < box_end:
                            break
                        with open(path, "rb") as handle:
                            handle.seek(box_start)
                            moov = handle.read(box_end - box_start)
                        if len(moov) == box_end - box_start:
                            return box_start, moov
                        break
        time.sleep(0.1)
    return 0, None


def write_header(path, header, offset=0):
    """Overwrite an in-place patched moov while ffmpeg appends fragments."""
    if not header:
        return False
    try:
        with open(path, "r+b") as handle:
            handle.seek(offset)
            handle.write(header)
            handle.flush()
        return True
    except OSError as exc:
        logger.warning("Could not patch playback header for %s: %s", path, exc)
        return False
