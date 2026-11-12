"""Dispatch passes the same normalized URLs to provider lookup and models."""

import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import provider_check as checks
import pytest

from aniworld import entry, menu
from aniworld.providers import resolve_provider
from aniworld.web import db, discord_bot, worker
from aniworld.web.views import api_media

SERIES = "https://9anime.org.lv/anime/example"
SEASON = SERIES
EPISODE = SERIES + "-episode-1"


@pytest.fixture
def site_provider():
    series = Mock(
        return_value=SimpleNamespace(title="Example", seasons=[], download=Mock())
    )
    season = Mock(
        return_value=SimpleNamespace(episodes=[], episode_languages={}, download=Mock())
    )
    episode = Mock(return_value=SimpleNamespace(provider_data={}, download=Mock()))
    provider = replace(
        resolve_provider(SERIES),
        series_cls=series,
        season_cls=season,
        episode_cls=episode,
    )
    return SimpleNamespace(
        provider=provider, series=series, season=season, episode=episode
    )


def test_model_dispatch_preserves_episode_selectors(monkeypatch, url, expected):
    factory = Mock()
    provider = replace(
        resolve_provider(url),
        episode_cls=factory,
        season_cls=factory,
        series_cls=factory,
    )
    lookup = Mock(return_value=provider)
    monkeypatch.setattr(entry, "resolve_provider", lookup)

    assert entry.model_for_url(url) is factory.return_value

    lookup.assert_called_once_with(expected)
    factory.assert_called_once_with(url=expected)


test_model_dispatch_preserves_episode_selectors = pytest.mark.parametrize(
    "url,expected",
    [
        (
            " https://9anime.org.lv/anime/example/ ",
            "https://9anime.org.lv/anime/example",
        ),
        (
            " https://9anime.org.lv/anime/example-episode-1/ ",
            "https://9anime.org.lv/anime/example-episode-1",
        ),
        (
            " https://miruro.tv/info/example/some-title/ ",
            "https://miruro.tv/info/example/some-title",
        ),
    ],
)(test_model_dispatch_preserves_episode_selectors)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/serie/stream/example",
        "https://miruro.tv/watch/example",
    ],
)
def test_model_dispatch_still_rejects_unsupported_urls(url):
    with pytest.raises(ValueError):
        entry.model_for_url(url)


@pytest.mark.parametrize("mode", ["interactive", "no-menu", "episode-file"])
@pytest.mark.parametrize(
    "kind,suffix",
    # Every surviving site uses one pattern for both its series and its season
    # page, so model_for_url answers a series URL with the season model.
    [("season", ""), ("episode", "-episode-1")],
)
def test_cli_dispatch_uses_normalized_urls(
    monkeypatch, tmp_path, site_provider, mode, kind, suffix
):
    raw = f"  {SERIES}{suffix}/  "
    if mode == "episode-file":
        episode_file = tmp_path / "urls.txt"
        episode_file.write_text(raw + "\n")
        argv = ["--episode-file", str(episode_file)]
    elif mode == "no-menu":
        argv = ["--no-menu", raw]
    else:
        argv = [raw]
    monkeypatch.setattr(sys, "argv", ["aniworld", *argv])
    lookup = Mock(return_value=site_provider.provider)
    monkeypatch.setattr(entry, "resolve_provider", lookup)
    show_menu = Mock(return_value=None)
    monkeypatch.setattr(menu, "app", show_menu)

    result = entry.aniworld()

    expected = SERIES + suffix
    assert lookup.called
    assert all(call.args == (expected,) for call in lookup.call_args_list)
    if mode == "interactive" and kind == "series":
        assert result == 130
        show_menu.assert_called_once_with(url=expected)
    else:
        assert result == 0
        factory = getattr(site_provider, kind)
        factory.assert_called_once_with(url=expected)
        factory.return_value.download.assert_called_once_with()
        show_menu.assert_not_called()


@pytest.mark.parametrize(
    "endpoint,kind,suffix",
    [
        ("series", "series", ""),
        ("seasons", "series", ""),
        ("episodes", "season", ""),
        ("providers", "episode", "-episode-1"),
    ],
)
def test_web_dispatch_uses_normalized_urls(
    client, monkeypatch, site_provider, endpoint, kind, suffix
):
    lookup = Mock(return_value=site_provider.provider)
    monkeypatch.setattr(api_media, "resolve_provider", lookup)
    monkeypatch.setattr(api_media.media, "downloaded_episodes", lambda series: set())

    response = client.get(
        f"/api/{endpoint}",
        query_string={"url": f"  {SERIES}{suffix}/  ", "series_url": f" {SERIES}/ "},
    )

    assert response.status_code == 200
    lookup.assert_called_once_with(SERIES + suffix)
    if endpoint == "episodes":
        site_provider.series.assert_called_once_with(url=SERIES)
        site_provider.season.assert_called_once_with(
            url=SEASON, series=site_provider.series.return_value
        )
    else:
        getattr(site_provider, kind).assert_called_once_with(url=SERIES + suffix)


@pytest.mark.parametrize("as_dict", [False, True])
def test_worker_dispatch_uses_normalized_urls(
    queue_item, monkeypatch, site_provider, as_dict
):
    raw = f"  {EPISODE}/  "
    queued = {"url": raw} if as_dict else raw
    lookup = Mock(return_value=site_provider.provider)
    monkeypatch.setattr(worker, "resolve_provider", lookup)
    queue_id = queue_item(episodes=[queued])

    worker._process(db.get_queue_item(queue_id))

    assert db.get_queue_item(queue_id)["status"] == "completed"
    lookup.assert_called_once_with(EPISODE)
    assert site_provider.episode.call_args.kwargs["url"] == EPISODE
    site_provider.episode.return_value.download.assert_called_once_with()


def test_menu_normalizes_urls_before_loading_models():
    assert menu.MenuApp(f" {SERIES}/ ").url == SERIES


def test_discord_dispatch_normalizes_series_urls(monkeypatch, site_provider):
    site_provider.series.return_value.seasons = [
        SimpleNamespace(episodes=[SimpleNamespace(url=EPISODE)])
    ]
    lookup = Mock(return_value=site_provider.provider)
    monkeypatch.setattr(discord_bot, "resolve_provider", lookup)

    discord_bot._enqueue(
        "Example", f" {SERIES}/ ", "series", "German Dub", "VOE", "user", "42"
    )

    lookup.assert_called_once_with(SERIES)
    site_provider.series.assert_called_once_with(url=SERIES)
    item = db.get_queue()[0]
    assert item["series_url"] == SERIES
    assert json.loads(item["episodes"]) == [EPISODE]


def test_manual_provider_dispatch_normalizes_urls(monkeypatch, site_provider):
    from aniworld import providers

    site_provider.series.return_value.episodes = [object()]
    provider = replace(site_provider.provider, episode_cls=None)
    monkeypatch.setattr(providers, "resolve_provider", lambda url: provider)

    assert (
        checks.first_episode(f" {SERIES}/ ")
        is site_provider.series.return_value.episodes[0]
    )
    site_provider.series.assert_called_once_with(url=SERIES)
