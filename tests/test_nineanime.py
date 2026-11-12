"""Offline tests for the 9anime (English sub/dub) provider and MegaPlay."""

import base64
import json

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from aniworld.config import Audio, Subtitles
from aniworld.extractors.provider import megaplay
from aniworld.models.common import ProviderData
from aniworld.models.nineanime import (
    NineAnimeEpisode,
    NineAnimeSeason,
    NineAnimeSeries,
)
from aniworld.providers import resolve_provider

SERIES_URL = "https://9anime.org.lv/anime/a-certain-dark-item/"
EPISODE_URL = "https://9anime.org.lv/a-certain-dark-item-episode-1/"

SERIES_PAGE = """
<h1 class="entry-title">A Certain Dark Item</h1>
<div class="mindesc"><b>Watch full episodes A Certain Dark Item</b>, download english subbed.</div>
<div class="genxed"><a href="/genres/action/">Action</a> <a href="/genres/fantasy/">Fantasy</a></div>
<span><b>Released:</b> Oct 09, 2026</span>
<meta property="og:image" content="https://9anime.org.lv/wp-content/uploads/poster.jpg" />
<ul class="epcheck">
<li data-index="0"><a href="https://9anime.org.lv/a-certain-dark-item-episode-1/">
<div class="epl-num">1</div><div class="epl-title">A Certain Dark Item Episode 1</div>
<div class="epl-sub"><span class="status Sub">Sub</span></div></a></li>
<li data-index="1"><a href="https://9anime.org.lv/a-certain-dark-item-episode-2/">
<div class="epl-num">2</div><div class="epl-title">A Certain Dark Item Episode 2</div>
<div class="epl-sub"><span class="status Sub">Sub</span></div></a></li>
</ul>
"""

SUB_EPISODE_PAGE = """
<title>A Certain Dark Item Episode 1 English Subbed - 9anime</title>
<iframe src="https://gogoanime.me.uk/newplayer.php?mal_id=61014&amp;ep=1&amp;category=sub"
    allowfullscreen="true"></iframe>
"""

DUB_EPISODE_PAGE = """
<title>A Certain Dark Item Episode 1 English Dubbed - 9anime</title>
<iframe src="https://gogoanime.me.uk/newplayer.php?mal_id=61014&amp;ep=1&amp;category=dub"
    allowfullscreen="true"></iframe>
"""


def _series(monkeypatch):
    monkeypatch.setattr(
        "aniworld.models.nineanime.series.get_html", lambda url, headers=None: SERIES_PAGE
    )
    monkeypatch.setattr(
        "aniworld.models.nineanime.season.get_html", lambda url, headers=None: SERIES_PAGE
    )
    return NineAnimeSeries(SERIES_URL)


def _episode(monkeypatch, page=SUB_EPISODE_PAGE):
    monkeypatch.setattr(
        "aniworld.models.nineanime.series.get_html", lambda url, headers=None: SERIES_PAGE
    )
    monkeypatch.setattr(
        "aniworld.models.nineanime.episode.get_html", lambda url, headers=None: page
    )
    return NineAnimeEpisode(EPISODE_URL)


def test_series_metadata(monkeypatch):
    series = _series(monkeypatch)
    assert series.title == "A Certain Dark Item"
    assert series.release_year == "2026"
    assert series.genres == ["Action", "Fantasy"]
    assert series.poster_url.endswith("poster.jpg")
    assert series.description.startswith("Watch full episodes")
    assert series.seasons[0].season_number == 1


def test_season_parses_episode_list(monkeypatch):
    series = _series(monkeypatch)
    season = NineAnimeSeason(SERIES_URL, series=series)
    assert season.episode_count == 2
    numbers = [(episode.episode_number, episode.language) for episode in season.episodes]
    assert numbers == [(1, "English Sub"), (2, "English Sub")]
    assert season.episode_languages == {
        1: ("English Sub",),
        2: ("English Sub",),
    }


def test_episode_provider_data(monkeypatch):
    episode = _episode(monkeypatch)
    assert episode.language == "English Sub"
    assert isinstance(episode.provider_data, ProviderData)
    assert episode.available_providers() == ("MegaPlay",)
    assert episode._normalize_language("English Sub") == (
        Audio.JAPANESE,
        Subtitles.ENGLISH,
    )
    assert episode._normalize_language("English Dub") == (Audio.ENGLISH, Subtitles.NONE)
    assert episode.provider_link().startswith("https://gogoanime.me.uk/newplayer.php")


def test_dub_episode_language(monkeypatch):
    episode = _episode(monkeypatch, page=DUB_EPISODE_PAGE)
    assert episode.language == "English Dub"
    assert episode._normalize_language(episode.selected_language) == (
        Audio.ENGLISH,
        Subtitles.NONE,
    )


def test_unavailable_provider_falls_back(monkeypatch):
    episode = _episode(monkeypatch)
    episode.selected_provider = "VOE"
    assert episode.provider_link() == episode.provider_link(
        provider="MegaPlay"
    )


def test_naming_uses_template_without_imdb(monkeypatch):
    episode = _episode(monkeypatch)
    assert episode._episode_path.parent.name == "Season 01"
    assert episode._episode_path.name == "A Certain Dark Item S01E001.mkv"
    assert "[imdbid-]" not in str(episode._episode_path)


def test_resolve_provider_distinguishes_series_and_episode():
    series = resolve_provider(SERIES_URL)
    episode = resolve_provider(EPISODE_URL)
    assert series is not None and series.name == "9anime"
    assert episode is not None and episode.name == "9anime"
    assert series.series_pattern.match(SERIES_URL)
    assert not series.series_pattern.match(EPISODE_URL)
    assert episode.episode_pattern.match(EPISODE_URL)


def _encrypt(payload):
    padder = padding.PKCS7(128).padder()
    data = padder.update(json.dumps(payload).encode()) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(megaplay._KEY), modes.CBC(megaplay._IV)
    ).encryptor()
    encrypted = encryptor.update(data) + encryptor.finalize()
    return base64.urlsafe_b64encode(encrypted).decode().rstrip("=")


def test_megaplay_decrypts_source_payload():
    token = _encrypt({"file": "https://cdn.example/master.m3u8"})
    assert megaplay._decrypt_payload({"enc": token, "tracks": []}) == {
        "file": "https://cdn.example/master.m3u8"
    }


def test_megaplay_resolves_wrapper_chain(monkeypatch):
    pages = {
        "https://gogoanime.me.uk/newplayer.php?mal_id=1&ep=2&category=sub": (
            '<iframe src="https://megaplay.buzz/stream/mal/1/2/sub?autostart=true">'
        ),
        "https://megaplay.buzz/stream/mal/1/2/sub?autostart=true": (
            '<div id="megaplay-player" data-id="4242"></div>'
        ),
        "https://megaplay.buzz/stream/getSourcesNew?id=4242": json.dumps(
            {"enc": _encrypt({"file": "https://cdn.example/master.m3u8"})}
        ),
    }
    monkeypatch.setattr(megaplay, "_get", lambda url, referer=None: pages[url])
    assert (
        megaplay.get_direct_link_from_megaplay(
            "https://gogoanime.me.uk/newplayer.php?mal_id=1&ep=2&category=sub"
        )
        == "https://cdn.example/master.m3u8"
    )


def test_megaplay_falls_back_to_plain_payload(monkeypatch):
    pages = {
        "https://megaplay.buzz/stream/mal/1/2/sub": (
            '<div id="megaplay-player" data-id="4242"></div>'
        ),
        "https://megaplay.buzz/stream/getSourcesNew?id=4242": json.dumps(
            {"file": "https://cdn.example/plain.m3u8"}
        ),
    }
    monkeypatch.setattr(megaplay, "_get", lambda url, referer=None: pages[url])
    assert (
        megaplay.get_direct_link_from_megaplay(
            "https://megaplay.buzz/stream/mal/1/2/sub"
        )
        == "https://cdn.example/plain.m3u8"
    )


def test_best_hls_variant_prefers_highest_bandwidth():
    from aniworld.models.common.common import _best_hls_variant

    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,NAME="480p"
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5500000,RESOLUTION=1920x1080,NAME="1080p"
high.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,NAME="720p"
mid.m3u8
"""
    assert _best_hls_variant(master, "https://cdn.example/master.m3u8") == (
        "https://cdn.example/high.m3u8"
    )
