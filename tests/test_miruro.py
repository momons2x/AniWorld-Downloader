"""Offline tests for the Miruro (HLS) provider."""

from aniworld.config import Audio, Subtitles
from aniworld.models.common import ProviderData
from aniworld.models.miruro import MiruroEpisode, MiruroSeries
from aniworld.models.miruro._api import media_id_from_url, track_language
from aniworld.models.miruro.episode import _stream_candidates
from aniworld.providers import resolve_provider

MEDIA_ID = "P6joTcOcZ5MAg745EfyST5b3IZR8Vs9w"
SERIES_URL = (
    "https://miruro.tv/info/P6joTcOcZ5MAg745EfyST5b3IZR8Vs9w/"
    "even-the-student-council-has-its-holes"
)
EPISODE_URL = SERIES_URL.replace("/info/", "/watch/") + "/episode/1"

MEDIA = {
    "id": MEDIA_ID,
    "title": {
        "romaji": "Seitokai ni mo Ana wa Aru!",
        "english": "Even the Student Council Has Its Holes!",
        "native": "",
    },
    "description": "This is Fujinasu Academy, a private high school.",
    "genres": ["Comedy", "Ecchi", "Slice of Life"],
    "cover_url": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx.jpg",
    "season_year": 2026,
    "episode_count": 12,
    "episode_counts": {"raw": 12, "sub": 12, "dub": 12},
    "external_ids": {"imdb": ["tt43650242"], "anilist": ["191656"]},
    "is_adult": False,
}

SOURCES = {
    "episodeNumber": 1,
    "tracks": [
        {
            "track": "ssub",
            "providers": [
                {
                    "provider": "sun",
                    "skipTimes": [{"kind": "outro", "start": 1280, "end": 1369}],
                    "servers": [
                        {
                            "server": "icarus-2-1",
                            "headers": {"Referer": "https://ultracloud.cc/"},
                            "streams": [
                                {"url": "https://sun.example/stream.m3u8", "format": "hls"}
                            ],
                        }
                    ],
                },
                {
                    "provider": "hop",
                    "servers": [
                        {
                            "server": "Vid",
                            "headers": {"Referer": "https://krussdomi.com/"},
                            "streams": [
                                {"url": "https://hop.example/master.m3u8", "format": "hls"}
                            ],
                        }
                    ],
                },
                {
                    "provider": "bee",
                    "servers": [
                        {
                            "server": "Vidstream-1",
                            "headers": {"Referer": "https://megaplay.buzz/"},
                            "streams": [
                                {"url": "https://bee.example/master.m3u8", "format": "hls"}
                            ],
                        }
                    ],
                },
                {
                    "provider": "bun",
                    "servers": [{"server": "Vidstream-2", "streams": [{"url": ""}]}],
                },
            ],
        },
        {
            "track": "dub",
            "providers": [
                {
                    "provider": "hop",
                    "servers": [
                        {
                            "server": "Vid",
                            "headers": {"Referer": "https://krussdomi.com/"},
                            "streams": [
                                {"url": "https://hop.example/dub/master.m3u8", "format": "hls"}
                            ],
                        }
                    ],
                }
            ],
        },
    ],
}


def _patch(monkeypatch):
    search_hit = {
        **MEDIA,
        "episode_count": 12,
        "episode_counts": {"raw": 12, "sub": 12, "dub": 12},
    }
    monkeypatch.setattr(
        "aniworld.models.miruro.series.fetch_info", lambda media_id: MEDIA
    )
    monkeypatch.setattr(
        "aniworld.models.miruro.series.search_exact",
        lambda keyword, media_id: search_hit,
    )
    monkeypatch.setattr(
        "aniworld.models.miruro.episode.fetch_sources",
        lambda media_id, number: SOURCES,
    )


def test_track_language():
    assert track_language("ssub") == "English Sub"
    assert track_language("sub") == "English Sub"
    assert track_language("dub") == "English Dub"
    assert track_language("raw") is None


def test_stream_candidates_prefers_hop_and_maps_providers():
    candidates = _stream_candidates(SOURCES["tracks"][0])
    names = [name for name, _url in candidates]
    assert names[0] == "MiruroHop"
    assert "MiruroBee" in names
    assert "MiruroSun" in names
    assert "MiruroBun" not in names  # no usable bun stream
    assert candidates[0][1] == "https://hop.example/master.m3u8"
    named = dict(candidates)
    assert named["MiruroBee"] == "https://bee.example/master.m3u8"
    assert named["MiruroSun"] == "https://sun.example/stream.m3u8"


def test_media_id_parsing():
    assert media_id_from_url(SERIES_URL) == MEDIA_ID
    assert media_id_from_url(EPISODE_URL) == MEDIA_ID


def test_resolve_provider_distinguishes_series_and_episode():
    series = resolve_provider(SERIES_URL)
    episode = resolve_provider(EPISODE_URL)
    assert series is not None and series.name == "Miruro"
    assert episode is not None and episode.name == "Miruro"
    assert series.series_pattern.match(SERIES_URL)
    assert not series.series_pattern.match(EPISODE_URL)
    assert episode.episode_pattern.match(EPISODE_URL)


def test_series_metadata_and_season(monkeypatch):
    _patch(monkeypatch)
    series = MiruroSeries(SERIES_URL)
    assert series.title == "Even the Student Council Has Its Holes!"
    assert series.release_year == "2026"
    assert series.genres == ["Comedy", "Ecchi", "Slice of Life"]
    assert series.imdb == "tt43650242"
    assert series.description.startswith("This is Fujinasu Academy")
    assert series.episode_count == 12
    season = series.seasons[0]
    assert season.season_number == 1
    assert season.episode_count == 12
    assert all(
        episode.language == "English Sub" for episode in season.episodes
    )
    assert season.episodes[0].url == SERIES_URL.replace("/info/", "/watch/") + "/episode/1"
    assert season.episode_languages == {
        number: ("English Sub", "English Dub")
        for number in range(1, 13)
    }


def test_episode_provider_data_and_skip_times(monkeypatch):
    _patch(monkeypatch)
    episode = MiruroEpisode(EPISODE_URL)
    assert episode.episode_number == 1
    assert episode.language == "English Sub"
    assert isinstance(episode.provider_data, ProviderData)
    assert episode.available_providers() == ("MiruroHop", "MiruroBee", "MiruroSun")
    assert episode.provider_attempt_order()[0] == "MiruroHop"
    assert episode.provider_link(provider="MiruroHop") == "https://hop.example/master.m3u8"
    assert episode.provider_link(provider="MiruroBee") == "https://bee.example/master.m3u8"
    assert episode.provider_link(provider="MiruroSun") == "https://sun.example/stream.m3u8"
    assert episode._normalize_language("English Sub") == (
        Audio.JAPANESE,
        Subtitles.ENGLISH,
    )
    assert episode._normalize_language("English Dub") == (Audio.ENGLISH, Subtitles.NONE)
    assert episode.skip_times == {
        "found": True,
        "results": [
            {
                "interval": {"start_time": 1280, "end_time": 1369},
                "skip_type": "ed",
                "skip_id": "",
                "episode_length": 0,
            }
        ],
    }
    assert episode._episode_path.name == (
        "Even the Student Council Has Its Holes! S01E001.mkv"
    )


def test_season_only_lists_aired_episodes(monkeypatch):
    monkeypatch.setattr(
        "aniworld.models.miruro.series.fetch_info", lambda media_id: MEDIA
    )
    monkeypatch.setattr(
        "aniworld.models.miruro.series.search_exact",
        lambda keyword, media_id: {
            **MEDIA,
            "episode_count": 12,
            "episode_counts": {"raw": 5, "sub": 5, "dub": 1},
        },
    )
    season = MiruroSeries(SERIES_URL).seasons[0]
    assert season.episode_count == 5
    assert season.episode_languages == {
        1: ("English Sub", "English Dub"),
        2: ("English Sub",),
        3: ("English Sub",),
        4: ("English Sub",),
        5: ("English Sub",),
    }


def test_dub_episode_uses_dub_track(monkeypatch):
    _patch(monkeypatch)
    episode = MiruroEpisode(EPISODE_URL, selected_language="English Dub")
    assert episode.provider_link() == "https://hop.example/dub/master.m3u8"
    assert episode.available_providers() == ("MiruroHop",)


def test_stream_url_passes_through_extractor(monkeypatch):
    _patch(monkeypatch)
    episode = MiruroEpisode(EPISODE_URL)
    assert episode.stream_url == "https://hop.example/master.m3u8"
