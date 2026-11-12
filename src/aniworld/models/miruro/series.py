"""Miruro series model."""

import re
from html import unescape

try:
    from ...config import MIRURO_SERIES_PATTERN
    from ..common import run_each
    from ..common.common import clean_title
except ImportError:
    from aniworld.config import MIRURO_SERIES_PATTERN
    from aniworld.models.common import run_each
    from aniworld.models.common.common import clean_title

from ._api import fetch_info, media_id_from_url, search_exact


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


class MiruroSeries:
    """A Miruro series entry (their own /info/<id>/<slug> pages)."""

    def __init__(self, url):
        if not self.__is_valid_miruro_series_url(url):
            raise ValueError(f"Invalid Miruro series URL: {url}")

        self.url = url

        self.__title = None
        self.__description = None
        self.__genres = None
        self.__release_year = None
        self.__poster_url = None
        self.__imdb = None
        self.__seasons = None
        self.__media = None
        self.__search = None

    @staticmethod
    def __is_valid_miruro_series_url(url):
        return bool(url and MIRURO_SERIES_PATTERN.match(url))

    @property
    def media_id(self):
        return media_id_from_url(self.url)

    @property
    def _media(self):
        if self.__media is None:
            self.__media = fetch_info(self.media_id)
        return self.__media

    @property
    def _search(self):
        if self.__search is None:
            title = self._media.get("title") or {}
            keyword = title.get("english") or title.get("romaji") or ""
            self.__search = search_exact(keyword, self.media_id) if keyword else None
        return self.__search

    @property
    def episode_count(self):
        count = (self._search or {}).get("episode_count")
        if count is None:
            count = self._media.get("episode_count")
        return int(count or 0)

    @property
    def episode_counts(self):
        counts = (self._search or {}).get("episode_counts")
        return counts if isinstance(counts, dict) else {}

    @property
    def title(self):
        if self.__title is None:
            title = self._media.get("title") or {}
            self.__title = (
                title.get("english") or title.get("romaji") or ""
            ).strip()
        return self.__title

    @property
    def title_cleaned(self):
        return clean_title(self.title or "")

    @property
    def description(self):
        if self.__description is None:
            self.__description = _clean_text(self._media.get("description") or "")
        return self.__description

    @property
    def genres(self):
        if self.__genres is None:
            genres = self._media.get("genres") or []
            self.__genres = [
                genre for genre in genres if isinstance(genre, str) and genre.strip()
            ]
        return self.__genres

    @property
    def release_year(self):
        if self.__release_year is None:
            year = self._media.get("season_year")
            self.__release_year = str(year) if year else ""
        return self.__release_year

    @property
    def poster_url(self):
        if self.__poster_url is None:
            self.__poster_url = self._media.get("cover_url") or ""
        return self.__poster_url

    @property
    def imdb(self):
        if self.__imdb is None:
            external = (self._search or {}).get("external_ids") or self._media.get(
                "external_ids"
            ) or {}
            imdb = external.get("imdb")
            self.__imdb = imdb[0] if isinstance(imdb, list) and imdb else imdb
        return self.__imdb

    @property
    def seasons(self):
        if self.__seasons is None:
            from .season import MiruroSeason

            self.__seasons = [MiruroSeason(self.url, series=self)]
        return self.__seasons

    @property
    def season_count(self):
        return len(self.seasons)

    def download(self):
        run_each(self.seasons[0].episodes, "download")

    def watch(self):
        run_each(self.seasons[0].episodes, "watch")

    def syncplay(self):
        run_each(self.seasons[0].episodes, "syncplay")
