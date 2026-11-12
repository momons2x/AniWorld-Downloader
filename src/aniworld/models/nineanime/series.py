"""9anime series model (English sub / dub)."""

import re
from html import unescape

try:
    from ...config import NINEANIME_SERIES_PATTERN
    from ..common import run_each
    from ..common.common import clean_title
    from ..common.http import get_html
except ImportError:
    from aniworld.config import NINEANIME_SERIES_PATTERN
    from aniworld.models.common import run_each
    from aniworld.models.common.common import clean_title
    from aniworld.models.common.http import get_html

BASE_URL = "https://9anime.org.lv"


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


class NineAnimeSeries:
    """A 9anime title page and its single flat episode list."""

    def __init__(self, url):
        if not self.__is_valid_nineanime_series_url(url):
            raise ValueError(f"Invalid 9anime series URL: {url}")

        self.url = url

        self.__title = None
        self.__description = None
        self.__genres = None
        self.__release_year = None
        self.__poster_url = None
        self.__seasons = None
        self.__html = None

    @staticmethod
    def __is_valid_nineanime_series_url(url):
        return bool(url and NINEANIME_SERIES_PATTERN.match(url))

    @property
    def title(self):
        if self.__title is None:
            self.__title = self.__extract_title()
        return self.__title

    @property
    def title_cleaned(self):
        return clean_title(self.title or "")

    @property
    def description(self):
        if self.__description is None:
            self.__description = self.__extract_description()
        return self.__description

    @property
    def genres(self):
        if self.__genres is None:
            self.__genres = self.__extract_genres()
        return self.__genres

    @property
    def release_year(self):
        if self.__release_year is None:
            self.__release_year = self.__extract_release_year()
        return self.__release_year

    @property
    def poster_url(self):
        if self.__poster_url is None:
            self.__poster_url = self.__extract_poster_url()
        return self.__poster_url

    @property
    def imdb(self):
        return None

    @property
    def seasons(self):
        if self.__seasons is None:
            from .season import NineAnimeSeason

            self.__seasons = [NineAnimeSeason(self.url, series=self)]
        return self.__seasons

    @property
    def season_count(self):
        return len(self.seasons)

    @property
    def _html(self):
        if self.__html is None:
            self.__html = get_html(self.url, headers={"Referer": BASE_URL + "/"})
        return self.__html

    def __extract_title(self):
        match = re.search(
            r'<h1[^>]*class="[^"]*entry-title[^"]*"[^>]*>(.*?)</h1>',
            self._html,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            return _clean_text(match.group(1))
        match = re.search(
            r'<meta property="og:title" content="([^"]*)"',
            self._html,
            re.IGNORECASE,
        )
        if match:
            return re.sub(
                r"\s+All Episodes\s*-\s*9anime\s*$",
                "",
                unescape(match.group(1)),
                flags=re.IGNORECASE,
            ).strip()
        match = re.search(r"<title>(.*?)</title>", self._html, re.IGNORECASE | re.DOTALL)
        return _clean_text(match.group(1)) if match else ""

    def __extract_description(self):
        for pattern in (
            r'<div class="mindesc[^"]*"[^>]*>(.*?)</div>',
            r'<div class="desc[^"]*"[^>]*>(.*?)</div>',
            r'<meta name="description" content="([^"]*)"',
        ):
            match = re.search(pattern, self._html, re.IGNORECASE | re.DOTALL)
            if match:
                text = _clean_text(match.group(1))
                if text:
                    return text
        return ""

    def __extract_genres(self):
        match = re.search(
            r'<div class="genxed[^"]*"[^>]*>(.*?)</div>',
            self._html,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return []
        genres = []
        for label in re.findall(r"<a[^>]*>(.*?)</a>", match.group(1), re.DOTALL):
            name = _clean_text(label)
            if name and name not in genres:
                genres.append(name)
        return genres

    def __extract_release_year(self):
        match = re.search(
            r"Released:\s*</b>\s*(?:<[^>]+>\s*)*([A-Za-z]+\s+\d{1,2},\s+(\d{4}))",
            self._html,
            re.IGNORECASE,
        )
        if match:
            return match.group(2)
        match = re.search(
            r'itemprop="datePublished"[^>]*datetime="(\d{4})',
            self._html,
            re.IGNORECASE,
        )
        return match.group(1) if match else ""

    def __extract_poster_url(self):
        match = re.search(
            r'<meta property="og:image" content="([^"]*)"',
            self._html,
            re.IGNORECASE,
        )
        return unescape(match.group(1)) if match else ""

    def download(self):
        run_each(self.seasons[0].episodes, "download")

    def watch(self):
        run_each(self.seasons[0].episodes, "watch")

    def syncplay(self):
        run_each(self.seasons[0].episodes, "syncplay")
