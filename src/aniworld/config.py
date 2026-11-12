import json
import os
import random
import re
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import certifi
from niquests import Session
from packaging.version import parse as parse_version

from .env import CA_BUNDLE_ENV_VARS, initialize_app_env, resolve_env_directory
from .logger import get_logger

VERSION = None

try:
    VERSION = version("aniworld")
except PackageNotFoundError:
    VERSION = None


def get_latest_version():
    """Fetch the newest available version from PyPI."""
    try:
        logger.debug("Checking latest version on PyPI...")
        request = Request(
            "https://pypi.org/pypi/aniworld/json",
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
        latest_version = payload["info"]["version"]
        logger.debug(f"Latest PyPI version is {latest_version}")
        return latest_version
    except (URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        logger.debug(f"Could not fetch latest version from PyPI: {exc}")
        return None


def is_newest_version() -> bool:
    """Checks if the installed version is the newest available on PyPI."""
    if not VERSION:
        return False

    latest_version = get_latest_version()
    if not latest_version:
        return False

    return parse_version(VERSION) >= parse_version(latest_version)


# Resolve the app directory and load its .env file whenever config is imported.
ANIWORLD_CONFIG_DIR = initialize_app_env(
    Path(__file__).resolve().parent / ".env.example",
    Path.home() / ".aniworld",
)

for key in CA_BUNDLE_ENV_VARS:
    value = os.environ.get(key)
    if value:
        os.environ[key] = os.path.expanduser(value)

CA_CERT_BUNDLE = next(
    (os.environ[key] for key in CA_BUNDLE_ENV_VARS if os.environ.get(key)),
    certifi.where(),
)
for key in CA_BUNDLE_ENV_VARS:
    os.environ.setdefault(key, CA_CERT_BUNDLE)

logger = get_logger(__name__)

NAMING_TEMPLATE = os.getenv(
    "ANIWORLD_NAMING_TEMPLATE",
    "{title} ({year}) [imdbid-{imdbid}]/Season {season}/{title} S{season}E{episode}.mkv",
)

# Video codec configuration
VIDEO_CODEC = os.getenv("ANIWORLD_VIDEO_CODEC", "copy")

# Simple codec mapping using ffmpeg defaults
VIDEO_CODEC_MAP = {
    "copy": "copy",
    "h264": "libx264",
    "h265": "libx265",
    "av1": "libsvtav1",
    "h264_nvenc": "h264_nvenc",
    "hevc_nvenc": "hevc_nvenc",
    "h264_amf": "h264_amf",
    "hevc_amf": "hevc_amf",
    "av1_amf": "av1_amf",
    "h264_qsv": "h264_qsv",
    "hevc_qsv": "hevc_qsv",
    "av1_qsv": "av1_qsv",
}

ACTION_METHODS = {
    "Download": "download",
    "Watch": "watch",
    "Syncplay": "syncplay",
}


def get_video_codec():
    """Get and validate video codec from environment variable."""
    codec = VIDEO_CODEC
    if codec not in VIDEO_CODEC_MAP:
        logger.warning(
            f"Invalid video codec '{codec}', falling back to 'copy'. Valid options: {list(VIDEO_CODEC_MAP.keys())}"
        )
        return "copy"
    return VIDEO_CODEC_MAP[codec]


# NIQUESTS


def _get_random_user_agent() -> str:
    fallback = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    jsonl_path = Path(__file__).parent / "browsers.jsonl"

    if not jsonl_path.exists():
        return fallback

    valid_agents = []
    try:
        # Stream the file to avoid loading all 10,000 lines into memory
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                # Fast string pre-filter before expensive JSON parsing
                if '"Windows"' in line or '"Mac OS X"' in line:
                    data = json.loads(line)
                    if data.get("os") in ("Windows", "Mac OS X"):
                        ua = data.get("useragent")
                        if ua:
                            valid_agents.append(ua)

        if valid_agents:
            return random.choice(valid_agents)
    except Exception:
        pass

    return fallback


DEFAULT_USER_AGENT = _get_random_user_agent()

# TODO:
# This is so fucking annoying because using GLOBAL_SESSION anywhere in the
# codebase ends up importing basically every module, so even a simple fetch
# takes 20–30 seconds just to start...
#
# I already made a lazy-loading wrapper that defers all the __init__.py imports
# in another branch, but for now I just have to sit through the import time
# every run, even though the actual fetch only takes about a second
GLOBAL_SESSION = Session(
    resolver=["doh+cloudflare://"],
    disable_http3=True,
    multiplexed=False,
    headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Dest": "document",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Mode": "navigate",
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://aniworld.to/search",
        "Priority": "u=0, i",
    },
)
GLOBAL_SESSION.verify = CA_CERT_BUNDLE

logger.debug("Config initialized successfully")
logger.debug(
    "If this shit seems to hang, just wait. It's currently loading all modules, which can take up to ~30 seconds, but only once per startup. This is being worked on and won't take nearly as long in the future."
)

# -----------------------------
# Provider Stuff
# -----------------------------
SUPPORTED_PROVIDERS = (
    "VOE",
    "Vidmoly",
    "Vidoza",
    "Doodstream",
    "Filemoon",
    "Gupload",
    "MegaPlay",
    "Vidara",
    "MiruroHop",
    "MiruroBee",
    "MiruroSun",
    "MiruroBun",
    "ReAnimeHD1",
    "ReAnimeHD2",
)


def parse_provider_order(value, allowed_providers=None):
    allowed = tuple(dict.fromkeys(allowed_providers or SUPPORTED_PROVIDERS))
    if not allowed:
        return ()

    if not value:
        return allowed

    ordered = []
    seen = set()

    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).split(",")

    for raw_provider in raw_values:
        provider = raw_provider.strip()
        if provider and provider in allowed and provider not in seen:
            ordered.append(provider)
            seen.add(provider)

    for provider in allowed:
        if provider not in seen:
            ordered.append(provider)

    return tuple(ordered)


def get_provider_fallback_order(allowed_providers=None):
    return parse_provider_order(
        os.getenv("ANIWORLD_PROVIDER_FALLBACK_ORDER", ""),
        allowed_providers=allowed_providers,
    )


def build_provider_attempt_order(
    selected_provider, available_providers, fallback_order=None
):
    available = tuple(
        dict.fromkeys(
            str(provider).strip()
            for provider in available_providers
            if str(provider).strip()
        )
    )
    if not available:
        return (selected_provider,) if selected_provider else ()

    ordered = []
    seen = set()

    if selected_provider and selected_provider in available:
        ordered.append(selected_provider)
        seen.add(selected_provider)

    if fallback_order is None:
        fallback_order = get_provider_fallback_order(allowed_providers=available)

    for provider in parse_provider_order(fallback_order, allowed_providers=available):
        if provider not in seen:
            ordered.append(provider)
            seen.add(provider)

    return tuple(ordered)


PROVIDER_HEADERS_D = {
    "Vidmoly": {"Referer": "https://vidmoly.biz"},
    # Doodstream signs the direct link against the requesting client, so the
    # download must reuse the same User-Agent the extractor sent.
    "Doodstream": {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": "https://dood.li/",
    },
    "VOE": {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://voe.sx/",
        "Origin": "https://voe.sx",
    },
    "Filemoon": {"User-Agent": DEFAULT_USER_AGENT, "Referer": "https://filemoon.to"},
    "Gupload": {"Referer": "https://gupload.xyz/", "Accept-Encoding": "identity"},
    "Vidara": {"Referer": "https://vidara.to/"},
    "MegaPlay": {"Referer": "https://megaplay.buzz/"},
    "MiruroHop": {
        "Referer": "https://krussdomi.com/",
        "Origin": "https://krussdomi.com",
    },
    "MiruroBee": {"Referer": "https://megaplay.buzz/"},
    "MiruroSun": {
        "Origin": "https://ultracloud.cc",
        "Referer": "https://ultracloud.cc/",
    },
    "MiruroBun": {"Referer": "https://krussdomi.com/"},
    "ReAnimeHD1": {"Referer": "https://flixcloud.cc/"},
    "ReAnimeHD2": {"Referer": "https://flixcloud.cc/"},
}

PROVIDER_HEADERS_W = {
    "Vidmoly": {"Referer": "https://vidmoly.biz"},
    # Doodstream signs the direct link against the requesting client, so the
    # download must reuse the same User-Agent the extractor sent.
    "Doodstream": {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": "https://dood.li/",
    },
    "VOE": {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://voe.sx/",
        "Origin": "https://voe.sx",
    },
    "Filemoon": {"User-Agent": DEFAULT_USER_AGENT, "Referer": "https://filemoon.to"},
    "Gupload": {"Referer": "https://gupload.xyz/", "Accept-Encoding": "identity"},
    "Vidara": {"Referer": "https://vidara.to/"},
    "MegaPlay": {"Referer": "https://megaplay.buzz/"},
    "MiruroHop": {
        "Referer": "https://krussdomi.com/",
        "Origin": "https://krussdomi.com",
    },
    "MiruroBee": {"Referer": "https://megaplay.buzz/"},
    "MiruroSun": {
        "Origin": "https://ultracloud.cc",
        "Referer": "https://ultracloud.cc/",
    },
    "MiruroBun": {"Referer": "https://krussdomi.com/"},
    "ReAnimeHD1": {"Referer": "https://flixcloud.cc/"},
    "ReAnimeHD2": {"Referer": "https://flixcloud.cc/"},
}


# -----------------------------
# Language Stuff
# -----------------------------
class Audio(Enum):
    """
    Available audio language options:

        - JAPANESE: Japanese dubbed audio
        - ENGLISH:  English dubbed audio

    Required source for each option:

        Japanese Dub -> Source: English Sub
        English Dub  -> Source: English Dub
    """

    JAPANESE = "Japanese"
    ENGLISH = "English"


class Subtitles(Enum):
    """
    Available subtitle language options:

        - NONE:    No subtitles
        - ENGLISH: English subtitles
        - INDONESIAN: Indonesian subtitles

    Required source for each option:

        English Sub  -> Source: English Sub
        Indonesian Sub -> Source: Indonesian Sub
    """

    NONE = "None"
    ENGLISH = "English"
    INDONESIAN = "Indonesian"


# Map site-specific language keys to semantic meaning
LANG_KEY_MAP = {
    "2": (Audio.JAPANESE, Subtitles.ENGLISH),  # English Sub
    "4": (Audio.ENGLISH, Subtitles.NONE),  # English Dub
    "5": (Audio.JAPANESE, Subtitles.INDONESIAN),  # Indonesian Sub
}

LANG_LABELS = {
    "2": "English Sub",
    "4": "English Dub",
    "5": "Indonesian Sub",
}

LANG_CODE_MAP = {
    Audio.ENGLISH: "eng",
    Audio.JAPANESE: "jpn",
    Subtitles.ENGLISH: "eng",
    Subtitles.INDONESIAN: "ind",
    Subtitles.NONE: None,
}


INVERSE_LANG_KEY_MAP = {v: k for k, v in LANG_KEY_MAP.items()}
INVERSE_LANG_LABELS = {v: k for k, v in LANG_LABELS.items()}

# -----------------------------
# Patterns
# -----------------------------

# -----------------------------
# Directories
# -----------------------------
# TODO: add many other directories and use them throughout the app


def _mpv_config_dir():
    if os.name == "nt":
        base = resolve_env_directory("APPDATA", Path.home() / "AppData" / "Roaming")
    else:
        base = resolve_env_directory("XDG_CONFIG_HOME", Path.home() / ".config")
        value = os.environ.get("XDG_CONFIG_HOME", "")
        if value and not Path(value).is_absolute():
            # Some mpv builds otherwise read relative XDG paths in child processes.
            os.environ["XDG_CONFIG_HOME"] = str(base)
    return base / "mpv"


MPV_CONFIG_DIR = _mpv_config_dir()
MPV_SCRIPTS_DIR = MPV_CONFIG_DIR / "scripts"


# 9anime.org.lv: English sub/dub anime (Themesia "Animestream" theme).
# Title pages live at /anime/<slug>/ (the bare /<slug>/ alias answers too),
# episodes at /<slug>-episode-<n>/ — the negative lookahead keeps the series
# pattern from swallowing episode links, which resolve_provider checks first.
NINEANIME_SERIES_PATTERN = re.compile(
    r"^https?://(?:www\.)?9anime\.org\.lv/(?:anime/)?"
    r"(?!.*-episode-\d+/?$)[a-zA-Z0-9\-]+/?$",
    re.IGNORECASE,
)
NINEANIME_SEASON_PATTERN = NINEANIME_SERIES_PATTERN
NINEANIME_EPISODE_PATTERN = re.compile(
    r"^https?://(?:www\.)?9anime\.org\.lv/(?:anime/)?[a-zA-Z0-9\-]+-episode-\d+/?$",
    re.IGNORECASE,
)

# Miruro (miruro.tv): English sub/dub anime streaming (HLS).
# Series pages are /info/<id>[/<slug>], episodes are /watch/<id>/episode/<n>.
MIRURO_SERIES_PATTERN = re.compile(
    r"^https?://(?:www\.)?miruro\.tv/info/(?P<media_id>[A-Za-z0-9\-_]+)(?:/[^/]+)?/?$",
    re.IGNORECASE,
)
MIRURO_SEASON_PATTERN = MIRURO_SERIES_PATTERN
MIRURO_EPISODE_PATTERN = re.compile(
    r"^https?://(?:www\.)?miruro\.tv/watch/(?P<media_id>[A-Za-z0-9\-_]+)(?:/[^/]+)?/episode/(?P<episode>\d+)/?$",
    re.IGNORECASE,
)

# Re:Anime (reanime.to): English sub/dub anime streaming (flixcloud HLS).
# Series pages are /anime/<id>, episodes are /watch/<id>?ep=<n>.
REANIME_SERIES_PATTERN = re.compile(
    r"^https?://(?:www\.)?reanime\.to/anime/(?P<anime_id>[a-zA-Z0-9\-]+)/?$",
    re.IGNORECASE,
)
REANIME_SEASON_PATTERN = REANIME_SERIES_PATTERN
REANIME_EPISODE_PATTERN = re.compile(
    r"^https?://(?:www\.)?reanime\.to/watch/(?P<anime_id>[a-zA-Z0-9\-]+)(?:\?ep=(?P<episode>\d+))?/?$",
    re.IGNORECASE,
)
