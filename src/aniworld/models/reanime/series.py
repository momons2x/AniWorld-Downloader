"""Re:Anime series model."""

import re
from html import unescape

try:
    from ...config import REANIME_SERIES_PATTERN
    from ..common import run_each
    from ..common.common import clean_title
except ImportError:
    from aniworld.config import REANIME_SERIES_PATTERN
    from aniworld.models.common import run_each
    from aniworld.models.common.common import clean_title

from ._api import anime_id_from_url, fetch_anime_record, fetch_meta, search_exact


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


class ReanimeSeries:
    """A Re:Anime series entry (their own /anime/<id> pages)."""

    def __init__(self, url):
        if not self.__is_valid_reanime_series_url(url):
            raise ValueError(f"Invalid Re:Anime series URL: {url}")

        self.url = url

        self.__title = None
        self.__description = None
        self.__genres = None
        self.__release_year = None
        self.__poster_url = None
        self.__imdb = None
        self.__anilist_id = None
        self.__mal_id = None
        self.__seasons = None
        self.__meta = None
        self.__search = None
        self.__record = None

    @staticmethod
    def __is_valid_reanime_series_url(url):
        return bool(url and REANIME_SERIES_PATTERN.match(url))

    @property
    def anime_id(self):
        return anime_id_from_url(self.url)

    @property
    def _meta(self):
        if self.__meta is None:
            self.__meta = fetch_meta(self.anime_id)
        return self.__meta

    @property
    def _record(self):
        if self.__record is None:
            self.__record = fetch_anime_record(self.anime_id)
        return self.__record

    @property
    def anilist_id(self):
        if self.__anilist_id is None:
            self.__anilist_id = (self._search or {}).get("anilist_id")
        return self.__anilist_id

    @property
    def mal_id(self):
        if self.__mal_id is None:
            self.__mal_id = (self._search or {}).get("mal_id")
        return self.__mal_id

    @property
    def _search(self):
        if self.__search is None:
            title = self.title
            self.__search = search_exact(title, self.anime_id) if title else None
        return self.__search

    @property
    def title(self):
        if self.__title is None:
            title = self._meta.get("title") or {}
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
            record = self._record
            self.__description = _clean_text(
                record.get("description") or self._meta.get("description") or ""
            )
        return self.__description

    @property
    def genres(self):
        if self.__genres is None:
            self.__genres = [
                genre
                for genre in (self._search or {}).get("genres") or []
                if isinstance(genre, str) and genre.strip()
            ]
        return self.__genres

    @property
    def release_year(self):
        if self.__release_year is None:
            year = (self._search or {}).get("season_year") or (
                self._meta or {}
            ).get("season_year")
            self.__release_year = str(year) if year else ""
        return self.__release_year

    @property
    def poster_url(self):
        if self.__poster_url is None:
            cover = (self._search or {}).get("cover_image") or {}
            self.__poster_url = (
                cover.get("extra_large") or cover.get("large") or ""
            )
        return self.__poster_url

    @property
    def imdb(self):
        if self.__imdb is None:
            external = (self._search or {}).get("external_ids") or {}
            imdb = external.get("imdb")
            if isinstance(imdb, list) and imdb:
                imdb = imdb[0]
            self.__imdb = imdb or (self._record or {}).get("imdb_id")
        return self.__imdb

    @property
    def episode_count(self):
        count = (self._search or {}).get("episodes")
        return int(count or 0)

    @property
    def episode_counts(self):
        item = self._search or {}
        return {
            "raw": item.get("episodes"),
            "sub": item.get("subbed"),
            "dub": item.get("dubbed"),
        }

    @property
    def seasons(self):
        if self.__seasons is None:
            from .season import ReanimeSeason

            self.__seasons = [ReanimeSeason(self.url, series=self)]
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
