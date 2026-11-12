"""9anime season model: every episode of a title as one season."""

import re
from html import unescape

try:
    from ..common import run_each
    from ..common.http import get_html
except ImportError:
    from aniworld.models.common import run_each
    from aniworld.models.common.http import get_html

BASE_URL = "https://9anime.org.lv"

_EPISODE_ITEM_RE = re.compile(
    r'<li[^>]*data-index="[^"]*"[^>]*>\s*'
    r'<a href="(?P<href>https?://[^"]+)"[^>]*>\s*'
    r'<div class="epl-num">(?P<num>[^<]*)</div>\s*'
    r'<div class="epl-title">(?P<title>[^<]*)</div>'
    r'(?P<rest>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _clean_text(value):
    return unescape(re.sub(r"\s+", " ", value or "")).strip()


class NineAnimeSeason:
    """One flat season backed by the title page's complete episode list."""

    def __init__(self, url, series=None):
        self.url = url
        self._series = series

        self.__episodes = None
        self.__html = None

    @property
    def series(self):
        if self._series is None:
            from .series import NineAnimeSeries

            self._series = NineAnimeSeries(self.url)
        return self._series

    @property
    def season_number(self):
        return 1

    @property
    def episode_count(self):
        return len(self.episodes)

    @property
    def episodes(self):
        if self.__episodes is None:
            self.__episodes = self.__extract_episodes()
        return self.__episodes

    @property
    def episode_languages(self):
        """Language badges keyed by episode number, read from the same page."""
        return {
            episode.episode_number: (episode.language,)
            for episode in self.episodes
        }

    @property
    def _html(self):
        if self.__html is None:
            self.__html = get_html(self.url, headers={"Referer": BASE_URL + "/"})
        return self.__html

    def __extract_episodes(self):
        from .episode import ENGLISH_DUB, ENGLISH_SUB, NineAnimeEpisode

        episodes = {}
        for match in _EPISODE_ITEM_RE.finditer(self._html):
            try:
                number = int(_clean_text(match.group("num")))
            except ValueError:
                continue
            if number in episodes:
                continue
            status = re.search(
                r'<span class="status ([^"]+)">', match.group("rest"), re.IGNORECASE
            )
            language = (
                ENGLISH_DUB
                if status and status.group(1).strip().lower() == "dub"
                else ENGLISH_SUB
            )
            episodes[number] = NineAnimeEpisode(
                url=unescape(match.group("href")),
                series=self.series,
                season=self,
                episode_number=number,
                title_en=_clean_text(match.group("title")),
                language=language,
            )
        return [episodes[number] for number in sorted(episodes)]

    def download(self):
        run_each(self.episodes, "download")

    def watch(self):
        run_each(self.episodes, "watch")

    def syncplay(self):
        run_each(self.episodes, "syncplay")
