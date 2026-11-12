"""Re:Anime (reanime.to) models — English sub / dub anime.

Streams come from flixcloud.cc embeds whose playback URLs are encrypted
inside the page with a WebAssembly helper; the `reanme` extractor in
`extractors/provider` decrypts them with the bundled Node.js sidecar.
Subtitles ship per embed page (ASS/SRT), muxed into the MKV after download.
"""

import os
import re
from html import unescape
from pathlib import Path

try:
    from ...config import (
        GLOBAL_SESSION,
        NAMING_TEMPLATE,
        REANIME_EPISODE_PATTERN,
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
    from aniworld.config import (
        GLOBAL_SESSION,
        NAMING_TEMPLATE,
        REANIME_EPISODE_PATTERN,
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
    anime_id_from_url,
    episode_from_url,
    fetch_embed_page,
    flix_servers,
    parse_subtitles,
)

ENGLISH_SUB = "English Sub"
ENGLISH_DUB = "English Dub"

_SERVER_PREFERENCE = ("ReAnimeHD1", "ReAnimeHD2")


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _server_provider(server):
    name = str(server.get("serverName") or "")
    match = re.search(r"(\d+)$", name)
    return f"ReAnimeHD{match.group(1) if match else 1}"


class ReanimeEpisode:
    """A single Re:Anime episode backed by one flixcloud embed."""

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
        if not self.__is_valid_reanime_episode_url(url):
            raise ValueError(f"Invalid Re:Anime episode URL: {url}")

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
        self.__servers = None

        self.__selected_path = None
        self.__selected_language = None
        self.__selected_provider = None

        self.__redirect_url = None
        self.__provider_url = None
        self.__episode_path = None
        self.__is_downloaded = None

    @staticmethod
    def __is_valid_reanime_episode_url(url):
        return bool(url and REANIME_EPISODE_PATTERN.match(url))

    @property
    def series(self):
        if self._series is None:
            from .series import ReanimeSeries

            self._series = ReanimeSeries(self.series_url)
        return self._series

    @property
    def series_url(self):
        return re.sub(r"\?ep=\d+/?$", "", self.url.rstrip("/")).replace(
            "/watch/", "/anime/"
        )

    @property
    def anime_id(self):
        return anime_id_from_url(self.url)

    @property
    def season(self):
        if self._season is None:
            from .season import ReanimeSeason

            self._season = ReanimeSeason(self.series.url, series=self.series)
        return self._season

    @property
    def episode_number(self):
        if self.__episode_number is None:
            self.__episode_number = episode_from_url(self.url)
        return self.__episode_number


    @property
    def title_en(self):
        if self.__title_en is None:
            self.__title_en = f"{self.series.title} Episode {self.episode_number}"
        return self.__title_en

    @property
    def _servers(self):
        if self.__servers is None:
            try:
                anilist_id = self.series.anilist_id
                mal_id = self.series.mal_id
                self.__servers = flix_servers(
                    anilist_id, self.episode_number, mal_id=mal_id
                )
            except Exception:
                self.__servers = []
        return self.__servers

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
        counts = self.series.episode_counts or {}
        try:
            sub_count = int(counts.get("sub") or 0)
            dub_count = int(counts.get("dub") or 0)
        except (TypeError, ValueError):
            sub_count, dub_count = 0, 0
        number = self.episode_number or 0
        if number and dub_count and number > sub_count:
            return ENGLISH_DUB
        return ENGLISH_SUB

    def __extract_provider_data(self):
        providers = {}
        for server in self._servers:
            data_type = str(server.get("dataType") or "").lower()
            if data_type == "dub":
                language = ENGLISH_DUB
            elif data_type == "sub":
                language = ENGLISH_SUB
            else:
                continue
            key = self._normalize_language(language)
            providers.setdefault(key, {})[_server_provider(server)] = server.get(
                "dataLink"
            )
        if not providers:
            return None
        return ProviderData(providers)

    def _normalize_language(self, language):
        if isinstance(language, tuple) and len(language) == 2:
            return language
        text = str(language or "").strip().lower()
        if text in {"english dub", "english dubbed", "dub", "dubbed", "english"}:
            return (Audio.ENGLISH, Subtitles.NONE)
        if text in {"english sub", "english subbed", "sub", "subbed", "japanese"}:
            return (Audio.JAPANESE, Subtitles.ENGLISH)
        raise ValueError(f"Re:Anime only offers English sub or dub (got: {language})")

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
                if candidate[0].value == key[0].value and candidate[1].value == key[1].value:
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
                if candidate[0].value == key[0].value and candidate[1].value == key[1].value:
                    provider_dict = values
                    break
        if not provider_dict:
            return ()
        return tuple(
            name for name in _SERVER_PREFERENCE if name in provider_dict
        ) + tuple(name for name in provider_dict if name not in _SERVER_PREFERENCE)

    def provider_attempt_order(self):
        return build_provider_attempt_order(
            self.selected_provider,
            self.available_providers(),
        )

    def _subtitle_source(self):
        """English subtitle URL from the episode's embed page."""
        try:
            html = fetch_embed_page(self.provider_url)
        except Exception:
            return None
        entries = parse_subtitles(html)
        english = [
            entry
            for entry in entries
            if "english" in str(entry.get("language") or "").lower()
        ]
        if not english:
            return None
        english.sort(key=lambda entry: not entry.get("default"))
        return english[0]["url"]

    def _mux_subtitles(self):
        target = self._episode_path
        if not target.exists():
            return
        source = self._subtitle_source()
        if not source:
            return
        import ffmpeg as ffmpeg_python

        subtitle_path = target.with_name(f".{self._file_name}.subtrack")
        muxed = target.with_name(f".{self._file_name}.subbed.mkv")
        try:
            response = GLOBAL_SESSION.get(
                source,
                headers={"Referer": "https://flixcloud.cc/", "Accept-Encoding": "identity"},
                timeout=60,
            )
            response.raise_for_status()
            subtitle_path.write_bytes(response.content)
            video = ffmpeg_python.input(str(target))
            subtitle = ffmpeg_python.input(str(subtitle_path))
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
            subtitle_path.unlink(missing_ok=True)
            muxed.unlink(missing_ok=True)

    def download(self):
        episode_download(self)
        self._mux_subtitles()

    watch = episode_watch
    syncplay = episode_syncplay
