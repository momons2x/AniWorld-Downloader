"""Offline tests for the terminal search prompt entry.aniworld falls into."""

import pytest

from aniworld import search


def test_only_the_live_sites_are_searched():
    from aniworld.web.media import SITE_KEYS

    assert search.SEARCH_SITES == SITE_KEYS
    assert "aniworld" not in search.SEARCH_SITES


def test_hits_from_every_site_are_merged_and_tagged(monkeypatch):
    def fake_search(site, keyword):
        if site == "miruro":
            return [{"title": "Naruto", "url": "https://miruro.tv/info/x/naruto"}]
        if site == "reanime":
            return [
                {"title": "Naruto", "url": "https://reanime.to/anime/naruto-xyz"},
                {"title": "Bleach", "url": "https://reanime.to/anime/bleach"},
            ]
        return []

    monkeypatch.setattr("aniworld.web.sitesearch.search", fake_search)
    merged = search._search_all("naruto")
    assert [item["site"] for item in merged] == ["miruro", "reanime", "reanime"]
    assert [item["title"] for item in merged] == ["Naruto", "Naruto", "Bleach"]


def test_the_same_series_on_two_sites_is_offered_once(monkeypatch):
    def fake_search(site, keyword):
        url = "https://example.test/shared"
        return (
            [{"title": f"From {site}", "url": url}]
            if site in ("nineanime", "miruro")
            else []
        )

    monkeypatch.setattr("aniworld.web.sitesearch.search", fake_search)
    merged = search._search_all("shared")
    assert [item["site"] for item in merged] == ["nineanime"]


def test_a_site_that_fails_does_not_cost_the_whole_query(monkeypatch):
    """A dead mirror is skipped by the site adapter, so the others still answer."""
    from aniworld.web import sitesearch

    def fail(keyword):
        raise RuntimeError("mirror down")

    monkeypatch.setitem(sitesearch.SITE_SEARCH, "miruro", fail)
    monkeypatch.setitem(
        sitesearch.SITE_SEARCH,
        "nineanime",
        lambda keyword: [
            {"title": "Naruto", "url": "https://9anime.org.lv/anime/naruto/"}
        ],
    )
    monkeypatch.setitem(sitesearch.SITE_SEARCH, "reanime", lambda keyword: [])
    assert [item["site"] for item in search._search_all("naruto")] == ["nineanime"]


def test_random_mode_answers_with_one_live_series(monkeypatch):
    monkeypatch.setenv("ANIWORLD_RANDOM_ANIME", "1")
    monkeypatch.setattr(search, "_random_series", lambda: "https://x/series")
    assert search.search() == "https://x/series"


def test_random_mode_gives_up_when_no_site_answered(monkeypatch):
    monkeypatch.setenv("ANIWORLD_RANDOM_ANIME", "1")
    monkeypatch.setattr(search, "_random_series", lambda: None)
    assert search.search() is None


def test_an_empty_prompt_aborts_without_asking_the_sites(monkeypatch):
    monkeypatch.delenv("ANIWORLD_RANDOM_ANIME", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "   ")
    search_sites = []
    monkeypatch.setattr(search, "_search_all", search_sites.append)
    assert search.search() is None
    assert search_sites == []


def test_a_single_hit_is_returned_without_opening_a_menu(monkeypatch):
    monkeypatch.delenv("ANIWORLD_RANDOM_ANIME", raising=False)
    prompts = iter(["naruto", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr(
        search,
        "_search_all",
        lambda keyword: [
            {
                "title": "Naruto",
                "url": "https://9anime.org.lv/anime/naruto/",
                "site": "nineanime",
            }
        ],
    )
    assert search.search() == "https://9anime.org.lv/anime/naruto/"


def test_several_hits_are_chosen_from_a_menu(monkeypatch):
    monkeypatch.delenv("ANIWORLD_RANDOM_ANIME", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "naruto")
    monkeypatch.setattr(
        search,
        "_search_all",
        lambda keyword: [
            {
                "title": "Naruto",
                "url": "https://9anime.org.lv/anime/naruto/",
                "site": "nineanime",
            },
            {
                "title": "Naruto",
                "url": "https://miruro.tv/info/x/naruto",
                "site": "miruro",
            },
        ],
    )
    picked = {}

    def fake_menu(stdscr, options):
        picked["options"] = options
        return options[1]

    monkeypatch.setattr(search, "_curses_menu", fake_menu)
    monkeypatch.setattr("curses.start_color", lambda: None)
    monkeypatch.setattr("curses.init_pair", lambda *args: None)
    monkeypatch.setattr("curses.wrapper", lambda fn: fn(None))

    assert search.search() == "https://miruro.tv/info/x/naruto"
    assert [option["title"] for option in picked["options"]] == [
        "[nineanime] Naruto",
        "[miruro] Naruto",
    ]


def test_a_keyword_that_finds_nothing_asks_again(monkeypatch):
    monkeypatch.delenv("ANIWORLD_RANDOM_ANIME", raising=False)
    prompts = iter(["nothing", "naruto"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr(
        search,
        "_search_all",
        lambda keyword: (
            []
            if keyword == "nothing"
            else [
                {
                    "title": "Naruto",
                    "url": "https://9anime.org.lv/anime/naruto/",
                    "site": "nineanime",
                }
            ]
        ),
    )
    assert search.search() == "https://9anime.org.lv/anime/naruto/"


@pytest.mark.parametrize(
    "url",
    [
        "https://9anime.org.lv/anime/naruto/",
        "https://miruro.tv/info/x/naruto",
        "https://reanime.to/anime/naruto-xyz",
    ],
)
def test_what_comes_back_is_a_url_the_providers_recognise(url):
    from aniworld.providers import resolve_provider

    assert resolve_provider(url) is not None
