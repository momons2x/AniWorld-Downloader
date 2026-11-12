"""Miruro (miruro.tv) models — English sub / dub anime.

The site aggregates HLS streams from several CDNs (hop/bee/sun/bun). Each CDN
needs its own Referer/Origin header, so every server is exposed as its own
provider (MiruroHop, MiruroBee, ...) and the shared download machinery falls
back through them in the configured preference order.
"""

import os
import re
import subprocess
from html import unescape
from pathlib import Path

try:
    from ...autodeps import DependencyManager
    from ...config import (
        GLOBAL_SESSION,
        MIRURO_EPISODE_PATTERN,
        NAMING_TEMPLATE,
        Audio,
        Subtitles,
        build_provider_attempt_order,
    )
    from ..common import ProviderData, check_downloaded
    from ..common.common import _run_ffmpeg_with_progress
    from ..common.common import download as episode_download
    from ..common.common import syncplay as episode_syncplay
    from ..common.common import watch as episode_watch
    from ..common.naming import template_path
    from ..common.paths import resolve_download_path
except ImportError:
    from aniworld.autodeps import DependencyManager
    from aniworld.config import (
        GLOBAL_SESSION,
        MIRURO_EPISODE_PATTERN,
        NAMING_TEMPLATE,
        Audio,
        Subtitles,
        build_provider_attempt_order,
    )
    from aniworld.models.common import ProviderData, check_downloaded
    from aniworld.models.common.common import _run_ffmpeg_with_progress
    from aniworld.models.common.common import download as episode_download
    from aniworld.models.common.common import syncplay as episode_syncplay
    from aniworld.models.common.common import watch as episode_watch
    from aniworld.models.common.naming import template_path
    from aniworld.models.common.paths import resolve_download_path

from ._api import (
    fetch_sources,
    media_id_from_url,
    track_language,
)

ENGLISH_SUB = "English Sub"
ENGLISH_DUB = "English Dub"

_SERVER_PREFERENCE = ("MiruroHop", "MiruroBee", "MiruroSun", "MiruroBun")

_PROVIDER_BY_NAME = {
    "hop": "MiruroHop",
    "bee": "MiruroBee",
    "sun": "MiruroSun",
    "bun": "MiruroBun",
}


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _stream_candidates(track):
    """(provider name, stream url) pairs for one track, best server first."""
    providers = track.get("providers") or []

    def order(provider):
        mapped = _PROVIDER_BY_NAME.get(str(provider.get("provider") or "").lower())
        if mapped in _SERVER_PREFERENCE:
            return _SERVER_PREFERENCE.index(mapped)
        return len(_SERVER_PREFERENCE)

    candidates = []
    for provider in sorted(providers, key=order):
        mapped = _PROVIDER_BY_NAME.get(str(provider.get("provider") or "").lower())
        if not mapped:
            continue
        for server in provider.get("servers") or []:
            for stream in server.get("streams") or []:
                url = stream.get("url")
                if url and url.startswith("http"):
                    candidates.append((mapped, url))
    return candidates


class MiruroEpisode:
    """A single Miruro episode backed by the site's HLS sources."""

    _separate_audio_rendition = True

    def __init__(
        self,
        url=None,
        series=None,
        season=None,
        episode_number=None,
        title_en=None,
        language=None,
        selected_path=None,
        selected_language=None,
        selected_provider=None,
    ):
        if not self.__is_valid_miruro_episode_url(url):
            raise ValueError(f"Invalid Miruro episode URL: {url}")

        self.url = url
        self._series = series
        self._season = season

        self.__episode_number = episode_number
        self.__title_en = title_en
        self.__language = language

        self.__selected_path_param = selected_path
        self.__selected_language_param = selected_language
        self.__selected_provider_param = selected_provider

        self.__provider_data = None
        self.__skip_times = None
        self.__sources = None

        self.__selected_path = None
        self.__selected_language = None
        self.__selected_provider = None

        self.__redirect_url = None
        self.__provider_url = None
        self.__episode_path = None
        self.__is_downloaded = None

    @staticmethod
    def __is_valid_miruro_episode_url(url):
        return bool(url and MIRURO_EPISODE_PATTERN.match(url))

    @property
    def series(self):
        if self._series is None:
            from .series import MiruroSeries

            self._series = MiruroSeries(self.series_url)
        return self._series

    @property
    def series_url(self):
        base = re.sub(r"/episode/\d+/?$", "", self.url.rstrip("/"))
        return base.replace("/watch/", "/info/")

    @property
    def media_id(self):
        match = MIRURO_EPISODE_PATTERN.match(self.url)
        return match.group("media_id") if match else media_id_from_url(self.url)

    @property
    def season(self):
        if self._season is None:
            from .season import MiruroSeason

            self._season = MiruroSeason(self.series.url, series=self.series)
        return self._season

    @property
    def episode_number(self):
        if self.__episode_number is None:
            match = MIRURO_EPISODE_PATTERN.match(self.url)
            self.__episode_number = (
                int(match.group("episode")) if match else None
            )
        return self.__episode_number


    @property
    def title_en(self):
        if self.__title_en is None:
            self.__title_en = f"{self.series.title} Episode {self.episode_number}"
        return self.__title_en

    @property
    def _sources(self):
        if self.__sources is None:
            self.__sources = fetch_sources(self.media_id, self.episode_number)
        return self.__sources

    @property
    def language(self):
        if self.__language is None:
            self.__language = self.__extract_language()
        return self.__language

    @property
    def provider_data(self):
        if self.__provider_data is None:
            self.__provider_data = self.__extract_provider_data()
        return self.__provider_data

    @property
    def skip_times(self):
        if self.__skip_times is None:
            self.__skip_times = self.__extract_skip_times()
        return self.__skip_times

    @property
    def selected_path(self):
        if self.__selected_path is None:
            self.__selected_path = str(resolve_download_path(self.__selected_path_param))
        return self.__selected_path

    @selected_path.setter
    def selected_path(self, value):
        self.__selected_path_param = value
        self.__selected_path = None
        self.__episode_path = None

    @property
    def selected_language(self):
        if self.__selected_language is None:
            candidate = self.__selected_language_param or os.getenv("ANIWORLD_LANGUAGE")
            if not candidate:
                candidate = self.language
            try:
                self._normalize_language(candidate)
            except ValueError:
                candidate = self.language
            self.__selected_language = candidate
        return self.__selected_language

    @selected_language.setter
    def selected_language(self, value):
        self.__selected_language_param = value
        self.__selected_language = None
        self.__redirect_url = None
        self.__provider_url = None
        self.__is_downloaded = None
        self.__episode_path = None

    @property
    def selected_provider(self):
        if self.__selected_provider is None:
            candidate = self.__selected_provider_param or os.getenv("ANIWORLD_PROVIDER")
            available = self.available_providers()
            if not candidate or candidate not in available:
                candidate = available[0] if available else _SERVER_PREFERENCE[0]
            self.__selected_provider = candidate
        return self.__selected_provider

    @selected_provider.setter
    def selected_provider(self, value):
        self.__selected_provider_param = value
        self.__selected_provider = None
        self.__redirect_url = None
        self.__provider_url = None

    @property
    def redirect_url(self):
        if self.__redirect_url is None:
            self.__redirect_url = self.provider_link(
                self.selected_language, self.selected_provider
            )
        return self.__redirect_url

    @property
    def provider_url(self):
        if self.__provider_url is None:
            self.__provider_url = self.redirect_url
        return self.__provider_url

    @property
    def stream_url(self):
        from ..common.extraction import resolve_stream_url

        return resolve_stream_url(self)

    @property
    def _episode_path(self):
        if self.__episode_path is None:
            template = os.getenv("ANIWORLD_NAMING_TEMPLATE", NAMING_TEMPLATE)
            self.__episode_path = template_path(
                self.selected_path,
                template,
                {
                    "title": self.series.title_cleaned,
                    "year": self.series.release_year,
                    "imdbid": self.series.imdb or "",
                    "season": f"{self.season.season_number:02d}",
                    "episode": f"{self.episode_number:03d}",
                    "language": self.selected_language,
                    "resolution": getattr(self, "_resolution", "unknown"),
                },
                is_series=True,
            )
        return self.__episode_path

    @property
    def _base_folder(self):
        root = Path(self.selected_path)
        relative = self._episode_path.relative_to(root)
        return root / relative.parts[0] if len(relative.parts) > 1 else root

    @property
    def _folder_path(self):
        return self._episode_path.parent

    @property
    def _file_name(self):
        return self._episode_path.stem

    @property
    def _file_extension(self):
        return self._episode_path.suffix.lstrip(".")

    @property
    def is_downloaded(self):
        if self.__is_downloaded is None:
            self.__is_downloaded = check_downloaded(self._episode_path)
        return self.__is_downloaded

    def __extract_language(self):
        tracks = (self._sources or {}).get("tracks") or []
        names = [track_language(track.get("track")) for track in tracks]
        if ENGLISH_DUB in names and ENGLISH_SUB not in names:
            return ENGLISH_DUB
        return ENGLISH_SUB

    def __extract_provider_data(self):
        tracks = (self._sources or {}).get("tracks") or []
        providers = {}
        for track in tracks:
            language = track_language(track.get("track"))
            if not language:
                continue
            key = self._normalize_language(language)
            bucket = providers.setdefault(key, {})
            for provider_name, url in _stream_candidates(track):
                bucket.setdefault(provider_name, url)
        if not providers:
            return None
        return ProviderData(providers)

    def __extract_skip_times(self):
        results = []
        for track in (self._sources or {}).get("tracks") or []:
            for provider in track.get("providers") or []:
                for skip in provider.get("skipTimes") or []:
                    kind = str(skip.get("kind") or "").lower()
                    skip_type = "op" if "op" in kind or "intro" in kind else "ed"
                    start = skip.get("start")
                    end = skip.get("end")
                    if start is None or end is None:
                        continue
                    results.append(
                        {
                            "interval": {"start_time": start, "end_time": end},
                            "skip_type": skip_type,
                            "skip_id": "",
                            "episode_length": 0,
                        }
                    )
                if results:
                    break
            if results:
                break
        return {"found": bool(results), "results": results}

    def _normalize_language(self, language):
        if isinstance(language, tuple) and len(language) == 2:
            return language
        text = str(language or "").strip().lower()
        if text in {"english dub", "english dubbed", "dub", "dubbed", "english"}:
            return (Audio.ENGLISH, Subtitles.NONE)
        if text in {"english sub", "english subbed", "sub", "subbed", "japanese"}:
            return (Audio.JAPANESE, Subtitles.ENGLISH)
        raise ValueError(f"Miruro only offers English sub or dub (got: {language})")

    def provider_link(self, language=None, provider=None):
        if language is None:
            language = self.selected_language
        if provider is None:
            provider = self.selected_provider
        provider_data = self.provider_data
        if not provider_data:
            raise ValueError(f"No provider data found for: {self.url}")
        key = self._normalize_language(language)
        provider_dict = provider_data.get(key)
        if not provider_dict:
            for candidate, values in provider_data._data.items():
                if (
                    candidate[0].value == key[0].value
                    and candidate[1].value == key[1].value
                ):
                    provider_dict = values
                    break
        if not provider_dict:
            raise ValueError(f"Language '{language}' is not available for: {self.url}")
        url = provider_dict.get(provider) or next(iter(provider_dict.values()))
        return url

    def available_providers(self, language=None):
        if language is None:
            language = self.selected_language
        provider_data = self.provider_data
        if not provider_data:
            return ()
        key = self._normalize_language(language)
        provider_dict = provider_data.get(key)
        if not provider_dict:
            for candidate, values in provider_data._data.items():
                if (
                    candidate[0].value == key[0].value
                    and candidate[1].value == key[1].value
                ):
                    provider_dict = values
                    break
        if not provider_dict:
            return ()
        return tuple(
            name
            for name in _SERVER_PREFERENCE
            if name in provider_dict
        ) + tuple(name for name in provider_dict if name not in _SERVER_PREFERENCE)

    def provider_attempt_order(self):
        return build_provider_attempt_order(
            self.selected_provider,
            self.available_providers(),
        )

    def _subtitle_source(self):
        """English WebVTT for the selected language, preferred by server order."""
        from ...config import PROVIDER_HEADERS_D

        tracks = (self._sources or {}).get("tracks") or []
        order = ("hop", "bee", "bun", "sun")
        best = None
        for track in tracks:
            if track_language(track.get("track")) != self.selected_language:
                continue
            for provider in track.get("providers") or []:
                name = str(provider.get("provider") or "").lower()
                rank = order.index(name) if name in order else len(order)
                headers = PROVIDER_HEADERS_D.get(
                    _PROVIDER_BY_NAME.get(name, "MiruroHop"),
                    {"Referer": "https://krussdomi.com/"},
                )
                for subtitle in provider.get("subtitles") or []:
                    language = str(subtitle.get("language") or "").lower()
                    if language not in {"en", "eng", "english"}:
                        continue
                    url = subtitle.get("file")
                    if not url or not str(url).startswith("http"):
                        continue
                    if best is None or rank < best[0]:
                        best = (rank, url, headers)
        return (best[1], best[2]) if best else None

    def _has_subtitle_stream(self, path):
        ffmpeg = DependencyManager().fetch_binary("ffmpeg", prompt_user=False)
        probe = Path(str(ffmpeg)).with_name("ffprobe.exe")
        if not probe.exists():
            probe = Path(str(ffmpeg)).with_name("ffprobe")
        if not probe.exists():
            return False
        result = subprocess.run(
            [
                str(probe),
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=index",
                "-of",
                "csv",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        return bool(result.stdout.strip())

    def _mux_subtitles(self):
        target = self._episode_path
        if not target.exists() or self._has_subtitle_stream(target):
            return
        source = self._subtitle_source()
        if not source:
            return
        url, headers = source
        import ffmpeg as ffmpeg_python

        vtt_path = target.with_name(f".{self._file_name}.vtt")
        muxed = target.with_name(f".{self._file_name}.subbed.mkv")
        try:
            response = GLOBAL_SESSION.get(
                url,
                headers={**headers, "Accept-Encoding": "identity"},
                timeout=60,
            )
            response.raise_for_status()
            content = re.sub(r"<[^>]+>", "", response.content.decode("utf-8", "ignore"))
            if "WEBVTT" not in content[:200]:
                return
            vtt_path.write_text(content, encoding="utf-8")
            video = ffmpeg_python.input(str(target))
            subtitle = ffmpeg_python.input(str(vtt_path))
            node = ffmpeg_python.output(
                video,
                subtitle,
                str(muxed),
                c="copy",
                **{
                    "metadata:s:s:0": "language=eng",
                    "disposition:s:0": "default",
                },
            )
            _run_ffmpeg_with_progress(node, label=self._file_name)
        except Exception:
            pass
        else:
            try:
                muxed.replace(target)
            except (PermissionError, OSError):
                pass
        finally:
            vtt_path.unlink(missing_ok=True)
            muxed.unlink(missing_ok=True)

    def download(self):
        episode_download(self)
        self._mux_subtitles()

    watch = episode_watch
    syncplay = episode_syncplay
