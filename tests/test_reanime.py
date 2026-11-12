"""Offline tests for the Re:Anime (flixcloud) provider."""

from aniworld.config import Audio, Subtitles
from aniworld.models.common import ProviderData
from aniworld.models.reanime import ReanimeEpisode, ReanimeSeries
from aniworld.models.reanime._api import (
    anime_id_from_url,
    episode_from_url,
    episode_url_for,
    parse_subtitles,
    series_url_for,
)
from aniworld.providers import resolve_provider

ANIME_ID = "even-the-student-council-has-its-holes-6ayeba"
SERIES_URL = f"https://reanime.to/anime/{ANIME_ID}"
EPISODE_URL = f"https://reanime.to/watch/{ANIME_ID}?ep=1"

SEARCH_ITEM = {
    "anime_id": ANIME_ID,
    "anilist_id": 191656,
    "mal_id": 61578,
    "title": {
        "english": "Even the Student Council Has Its Holes!",
        "romaji": "Seitokai ni mo Ana wa Aru!",
        "native": "",
    },
    "cover_image": {
        "extra_large": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx.jpg",
        "large": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx.jpg",
    },
    "genres": ["Comedy", "Ecchi", "Slice of Life"],
    "season_year": 2026,
    "episodes": 12,
    "subbed": 5,
    "dubbed": 1,
}

META = {
    "anime_id": ANIME_ID,
    "cover_image": {"extra_large": "https://s4.anilist.co/x/large.jpg"},
    "title": {
        "english": "Even the Student Council Has Its Holes!",
        "romaji": "Seitokai ni mo Ana wa Aru!",
    },
}

RECORD = {
    "anime_id": ANIME_ID,
    "title": {
        "english": "Even the Student Council Has Its Holes!",
        "romaji": "Seitokai ni mo Ana wa Aru!",
    },
    "description": "This is Fujinasu Academy, a private high school.",
    "genres": ["Comedy", "Ecchi"],
    "season_year": 2026,
    "cover_image": {"extra_large": "https://s4.anilist.co/x/large.jpg"},
    "imdb_id": "tt43650242",
    "format": "TV",
    "status": "Releasing",
}

SERVERS = [
    {
        "serverName": "HD-1",
        "dataLink": "https://flixcloud.cc/e/dt6euqqbw6oo?v=1",
        "dataType": "sub",
    },
    {
        "serverName": "HD-1",
        "dataLink": "https://flixcloud.cc/e/dt6euqqbw6oo?v=1",
        "dataType": "dub",
    },
    {
        "serverName": "HD-2",
        "dataLink": "https://flixcloud.cc/e/dt6euqqbw6oo?v=2",
        "dataType": "sub",
    },
]

EMBED_PAGE = """<script>
data = {
  subtitles: [
    {url: "https://vault.example/subs/ep_eng.srt", language: "English (SDH)", format: "srt", default: true},
    {url: "https://vault.example/subs/ep_jpn.srt", language: "Japanese (SDH)", format: "srt", default: false}
  ]
};
</script>
"""


def _patch(monkeypatch):
    monkeypatch.setattr(
        "aniworld.models.reanime.series.fetch_meta", lambda anime_id: META
    )
    monkeypatch.setattr(
        "aniworld.models.reanime.series.search_exact",
        lambda keyword, anime_id: SEARCH_ITEM,
    )
    monkeypatch.setattr(
        "aniworld.models.reanime.series.fetch_anime_record",
        lambda anime_id: RECORD,
    )
    monkeypatch.setattr(
        "aniworld.models.reanime.episode.flix_servers",
        lambda anilist_id, number, mal_id=None: SERVERS,
    )
    monkeypatch.setattr(
        "aniworld.models.reanime.episode.fetch_embed_page",
        lambda url: EMBED_PAGE,
    )


def test_url_helpers():
    assert anime_id_from_url(SERIES_URL) == ANIME_ID
    assert anime_id_from_url(EPISODE_URL) == ANIME_ID
    assert episode_from_url(EPISODE_URL) == 1
    assert episode_url_for(ANIME_ID, "Even the Student Council Has Its Holes!", 3).endswith(
        "?ep=3"
    )


def test_parse_subtitles():
    entries = parse_subtitles(EMBED_PAGE)
    assert entries == [
        {
            "url": "https://vault.example/subs/ep_eng.srt",
            "language": "English (SDH)",
            "default": True,
        },
        {
            "url": "https://vault.example/subs/ep_jpn.srt",
            "language": "Japanese (SDH)",
            "default": False,
        },
    ]


def test_resolve_provider_distinguishes_series_and_episode():
    series = resolve_provider(SERIES_URL)
    episode = resolve_provider(EPISODE_URL)
    assert series is not None and series.name == "ReAnime"
    assert episode is not None and episode.name == "ReAnime"
    assert series.series_pattern.match(SERIES_URL)
    assert not series.series_pattern.match(EPISODE_URL)
    assert episode.episode_pattern.match(EPISODE_URL)


def test_series_metadata_and_season(monkeypatch):
    _patch(monkeypatch)
    series = ReanimeSeries(SERIES_URL)
    assert series.title == "Even the Student Council Has Its Holes!"
    assert series.release_year == "2026"
    assert series.genres == ["Comedy", "Ecchi", "Slice of Life"]
    assert series.description.startswith("This is Fujinasu Academy")
    assert series.imdb == "tt43650242"
    assert series.episode_count == 12
    season = series.seasons[0]
    assert season.episode_count == 5
    assert list(season.episode_languages.items()) == [
        (1, ("English Sub", "English Dub")),
        (2, ("English Sub",)),
        (3, ("English Sub",)),
        (4, ("English Sub",)),
        (5, ("English Sub",)),
    ]
    assert season.episodes[0].url.endswith("?ep=1")


def test_episode_provider_data_and_subtitles(monkeypatch):
    _patch(monkeypatch)
    episode = ReanimeEpisode(EPISODE_URL)
    assert episode.episode_number == 1
    assert episode.language == "English Sub"
    assert episode.anilist_id if hasattr(episode, "anilist_id") else True
    assert episode.available_providers() == ("ReAnimeHD1", "ReAnimeHD2")
    assert episode.provider_attempt_order()[0] == "ReAnimeHD1"
    assert isinstance(episode.provider_data, ProviderData)
    assert episode.provider_link(provider="ReAnimeHD1") == (
        "https://flixcloud.cc/e/dt6euqqbw6oo?v=1"
    )
    assert episode._normalize_language("English Sub") == (
        Audio.JAPANESE,
        Subtitles.ENGLISH,
    )
    assert episode._normalize_language("English Dub") == (Audio.ENGLISH, Subtitles.NONE)
    assert episode._subtitle_source() == "https://vault.example/subs/ep_eng.srt"
    assert episode._episode_path.name == (
        "Even the Student Council Has Its Holes! S01E001.mkv"
    )


def test_dub_episode_uses_dub_embed(monkeypatch):
    _patch(monkeypatch)
    episode = ReanimeEpisode(EPISODE_URL, selected_language="English Dub")
    assert episode.provider_link() == "https://flixcloud.cc/e/dt6euqqbw6oo?v=1"


def test_series_url_for_and_episode_url_roundtrip():
    series_url = series_url_for(ANIME_ID, "Even the Student Council Has Its Holes!")
    assert series_url == SERIES_URL
