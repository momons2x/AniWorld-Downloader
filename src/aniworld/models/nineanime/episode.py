"""9anime (9anime.org.lv) models — English sub / dub anime.

The site runs the Themesia "Animestream" WordPress theme: title pages carry the
poster, synopsis, genres and the complete episode list (``ul.epcheck``), while
episode pages embed their player (Vidmoly). There are no real seasons, so a
title exposes one season holding every episode.
"""

import os
import re
from html import unescape
from pathlib import Path

try:
    from ...config import (
        NAMING_TEMPLATE,
        NINEANIME_EPISODE_PATTERN,
        Audio,
        Subtitles,
        build_provider_attempt_order,
    )
    from ..common import ProviderData, check_downloaded
    from ..common.common import download as episode_download
    from ..common.common import syncplay as episode_syncplay
    from ..common.common import watch as episode_watch
    from ..common.extraction import resolve_stream_url
    from ..common.http import get_html
    from ..common.naming import template_path
    from ..common.paths import resolve_download_path
except ImportError:
    from aniworld.config import (
        NAMING_TEMPLATE,
        NINEANIME_EPISODE_PATTERN,
        Audio,
        Subtitles,
        build_provider_attempt_order,
    )
    from aniworld.models.common import ProviderData, check_downloaded
    from aniworld.models.common.common import download as episode_download
    from aniworld.models.common.common import syncplay as episode_syncplay
    from aniworld.models.common.common import watch as episode_watch
    from aniworld.models.common.extraction import resolve_stream_url
    from aniworld.models.common.http import get_html
    from aniworld.models.common.naming import template_path
    from aniworld.models.common.paths import resolve_download_path

BASE_URL = "https://9anime.org.lv"
DEFAULT_PROVIDER = "MegaPlay"

ENGLISH_SUB = "English Sub"
ENGLISH_DUB = "English Dub"

_SERIES_SLUG_RE = re.compile(r"-episode-\d+/?$", re.IGNORECASE)
_EPISODE_NUMBER_RE = re.compile(r"-episode-(\d+)", re.IGNORECASE)


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _language_label(flag):
    return ENGLISH_DUB if str(flag or "").strip().lower() == "dub" else ENGLISH_SUB


class NineAnimeEpisode:
    """A single 9anime episode backed by one Vidmoly embed."""

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
        if not self.__is_valid_nineanime_episode_url(url):
            raise ValueError(f"Invalid 9anime episode URL: {url}")

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

        self.__selected_path = None
        self.__selected_language = None
        self.__selected_provider = None

        self.__redirect_url = None
        self.__provider_url = None

        self.__base_folder = None
        self.__folder_path = None
        self.__file_name = None
        self.__file_extension = None
        self.__episode_path = None

        self.__is_downloaded = None
        self.__html = None

    @staticmethod
    def __is_valid_nineanime_episode_url(url):
        return bool(url and NINEANIME_EPISODE_PATTERN.match(url))

    @property
    def series(self):
        if self._series is None:
            from .series import NineAnimeSeries

            self._series = NineAnimeSeries(self.series_url)
        return self._series

    @property
    def series_url(self):
        return BASE_URL + "/anime/" + self.url.rstrip("/").rsplit("/", 1)[-1].split("-episode-")[0] + "/"

    @property
    def season(self):
        if self._season is None:
            from .season import NineAnimeSeason

            self._season = NineAnimeSeason(self.series.url, series=self.series)
        return self._season

    @property
    def episode_number(self):
        if self.__episode_number is None:
            match = _EPISODE_NUMBER_RE.search(self.url)
            self.__episode_number = int(match.group(1)) if match else 1
        return self.__episode_number


    @property
    def title_en(self):
        if self.__title_en is None:
            self.__title_en = self.__extract_title_en()
        return self.__title_en

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
        self.__base_folder = None
        self.__folder_path = None
        self.__episode_path = None

    @property
    def selected_language(self):
        if self.__selected_language is None:
            candidate = self.__selected_language_param or os.getenv(
                "ANIWORLD_LANGUAGE"
            )
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
        self.__base_folder = None
        self.__folder_path = None
        self.__episode_path = None
        self.__file_name = None

    @property
    def selected_provider(self):
        if self.__selected_provider is None:
            candidate = self.__selected_provider_param or os.getenv(
                "ANIWORLD_PROVIDER"
            )
            available = self.available_providers()
            if not candidate or candidate not in available:
                candidate = available[0] if available else DEFAULT_PROVIDER
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
            link = self.provider_link(self.selected_language, self.selected_provider)
            if link is None:
                raise ValueError(
                    f"Language '{self.selected_language}' with provider "
                    f"'{self.selected_provider}' is not available for "
                    f"episode: {self.url}"
                )
            self.__redirect_url = link
        return self.__redirect_url

    @property
    def provider_url(self):
        if self.__provider_url is None:
            self.__provider_url = self.redirect_url
        return self.__provider_url

    @property
    def stream_url(self):
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
                    "imdbid": "",
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

    @property
    def _html(self):
        if self.__html is None:
            self.__html = get_html(
                self.url, headers={"Referer": BASE_URL + "/"}
            )
        return self.__html

    def __extract_title_en(self):
        html = self._html
        match = re.search(
            r'<h1[^>]*class="[^"]*entry-title[^"]*"[^>]*>(.*?)</h1>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            match = re.search(
                r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL
            )
        if not match:
            return f"{self.series.title} Episode {self.episode_number}"
        title = _clean_text(match.group(1))
        title = re.sub(r"\s*[-–|]\s*9anime\s*$", "", title, flags=re.IGNORECASE)
        return title

    def __extract_language(self):
        iframe = re.search(
            r'<iframe[^>]+src="([^"]*(?:newplayer\.php|megaplay\.buzz/stream)[^"]*)"',
            self._html,
            re.IGNORECASE,
        )
        if iframe and re.search(r"[?&/]dub(?:[&/\"']|$)", iframe.group(1), re.IGNORECASE):
            return ENGLISH_DUB
        match = re.search(r"<title>(.*?)</title>", self._html, re.IGNORECASE | re.DOTALL)
        text = (match.group(1) if match else self._html).lower()
        if "dubbed" in text or "dub" in text.split():
            return ENGLISH_DUB
        return ENGLISH_SUB

    def __extract_provider_data(self):
        providers = {}
        for match in re.finditer(
            r'<iframe[^>]+src="(https?://[^"]+)"',
            self._html,
            re.IGNORECASE,
        ):
            src = unescape(match.group(1))
            lowered = src.lower()
            if "vidmoly" in lowered:
                providers.setdefault("Vidmoly", src)
            elif "megaplay.buzz/stream" in lowered or "newplayer.php" in lowered:
                providers.setdefault("MegaPlay", src)
        if not providers:
            return None
        return ProviderData({self._normalize_language(self.language): providers})

    def _normalize_language(self, language):
        if isinstance(language, tuple) and len(language) == 2:
            return language
        text = str(language or "").strip().lower()
        if text in {"english dub", "english dubbed", "dub", "dubbed", "english"}:
            return (Audio.ENGLISH, Subtitles.NONE)
        if text in {"english sub", "english subbed", "sub", "subbed", "japanese"}:
            return (Audio.JAPANESE, Subtitles.ENGLISH)
        raise ValueError(f"9anime only offers English sub or dub (got: {language})")

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

        url = provider_dict.get(provider) or provider_dict.get(str(provider).title())
        if not url:
            url = next(iter(provider_dict.values()), None)
        if not url:
            raise ValueError(
                f"Provider '{provider}' not found for language: {language}."
            )
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
        return tuple(provider_dict.keys()) if provider_dict else ()

    def provider_attempt_order(self):
        return build_provider_attempt_order(
            self.selected_provider,
            self.available_providers(),
        )

    download = episode_download
    watch = episode_watch
    syncplay = episode_syncplay
