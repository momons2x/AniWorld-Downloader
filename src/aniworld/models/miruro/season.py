"""Miruro season model: episodes 1..episode_count with sub/dub badges."""

try:
    from ..common import run_each
except ImportError:
    from aniworld.models.common import run_each

from ._api import episode_url_for


class MiruroSeason:
    """One flat season; every entry of the series is a single season."""

    def __init__(self, url, series=None):
        self.url = url
        self._series = series
        self.__episodes = None

    @property
    def series(self):
        if self._series is None:
            from .series import MiruroSeries

            self._series = MiruroSeries(self.url)
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
        counts = self.series.episode_counts

        def _count(key):
            try:
                return int(counts.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        sub_count = _count("sub")
        dub_count = _count("dub")
        languages = {}
        for item in self.episodes:
            available = []
            if item.episode_number <= sub_count or not counts:
                available.append("English Sub")
            if item.episode_number <= dub_count:
                available.append("English Dub")
            if not available:
                available = [item.language]
            languages[item.episode_number] = tuple(available)
        return languages

    def __extract_episodes(self):
        from .episode import ENGLISH_DUB, ENGLISH_SUB, MiruroEpisode

        planned = self.series.episode_count
        counts = self.series.episode_counts

        def _count(key):
            try:
                return int(counts.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        sub_count = _count("sub")
        dub_count = _count("dub")
        # Only aired episodes exist yet; a releasing show's announced total
        # is larger than anything actually available to stream.
        total = sub_count or dub_count or planned

        media_id = self.series.media_id
        title = self.series.title
        title_slug = title or "anime"
        episodes = []
        for number in range(1, total + 1):
            language = ENGLISH_SUB
            if number > sub_count and dub_count > 0:
                language = ENGLISH_DUB
            episodes.append(
                MiruroEpisode(
                    url=episode_url_for(media_id, title_slug, number),
                    series=self.series,
                    season=self,
                    episode_number=number,
                    title_en=f"{title} Episode {number}",
                    language=language,
                )
            )
        return episodes

    def download(self):
        run_each(self.episodes, "download")

    def watch(self):
        run_each(self.episodes, "watch")

    def syncplay(self):
        run_each(self.episodes, "syncplay")
