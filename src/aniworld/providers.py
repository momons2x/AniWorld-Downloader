from __future__ import annotations

from dataclasses import dataclass
from re import Pattern
from urllib.parse import urlparse, urlunparse

from .config import (
    MIRURO_EPISODE_PATTERN,
    MIRURO_SEASON_PATTERN,
    MIRURO_SERIES_PATTERN,
    NINEANIME_EPISODE_PATTERN,
    NINEANIME_SEASON_PATTERN,
    NINEANIME_SERIES_PATTERN,
    REANIME_EPISODE_PATTERN,
    REANIME_SEASON_PATTERN,
    REANIME_SERIES_PATTERN,
)
from .models import (
    MiruroEpisode,
    MiruroSeason,
    MiruroSeries,
    NineAnimeEpisode,
    NineAnimeSeason,
    NineAnimeSeries,
    ReanimeEpisode,
    ReanimeSeason,
    ReanimeSeries,
)


@dataclass(frozen=True)
class Provider:
    name: str
    series_pattern: Pattern[str] | None = None
    season_pattern: Pattern[str] | None = None
    episode_pattern: Pattern[str] | None = None

    series_cls: type | None = None
    season_cls: type | None = None
    episode_cls: type | None = None


PROVIDERS = [
    Provider(
        name="Miruro",
        series_pattern=MIRURO_SERIES_PATTERN,
        season_pattern=MIRURO_SEASON_PATTERN,
        episode_pattern=MIRURO_EPISODE_PATTERN,
        series_cls=MiruroSeries,
        season_cls=MiruroSeason,
        episode_cls=MiruroEpisode,
    ),
    Provider(
        name="ReAnime",
        series_pattern=REANIME_SERIES_PATTERN,
        season_pattern=REANIME_SEASON_PATTERN,
        episode_pattern=REANIME_EPISODE_PATTERN,
        series_cls=ReanimeSeries,
        season_cls=ReanimeSeason,
        episode_cls=ReanimeEpisode,
    ),
    Provider(
        name="9anime",
        series_pattern=NINEANIME_SERIES_PATTERN,
        season_pattern=NINEANIME_SEASON_PATTERN,
        episode_pattern=NINEANIME_EPISODE_PATTERN,
        series_cls=NineAnimeSeries,
        season_cls=NineAnimeSeason,
        episode_cls=NineAnimeEpisode,
    ),
]


def normalize_url(url: str) -> str:
    if not url:
        return url

    url = url.strip()
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return urlunparse(parsed._replace(path=path))


def resolve_provider(url: str) -> Provider:
    url = normalize_url(url)

    for provider in PROVIDERS:
        if provider.series_pattern and provider.series_pattern.fullmatch(url):
            return provider
        if provider.season_pattern and provider.season_pattern.fullmatch(url):
            return provider
        if provider.episode_pattern and provider.episode_pattern.fullmatch(url):
            return provider

    raise ValueError(f"Unsupported URL: {url}")
