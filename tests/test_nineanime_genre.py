"""Offline tests for 9anime's genre listing, the only one the Web UI offers."""

import pytest

from aniworld.models.nineanime import _api

HOME_PAGE = """
<div class="genxed">
  <a href="https://9anime.org.lv/genres/action/">Action</a>
  <a href="https://9anime.org.lv/genres/fantasy/">Fantasy &amp; Magic</a>
  <a href="https://9anime.org.lv/genres/action/">Action</a>
  <a href="https://9anime.org.lv/tags/not-a-genre/">Not a genre</a>
</div>
"""

GENRE_PAGE = """
<div class="listupd">
<article class="bs">
  <a href="https://9anime.org.lv/anime/bullbuster/" title="Bullbuster">
    <img src="https://9anime.org.lv/poster.jpg">
  </a>
</article>
<article class="bs">
  <a href="https://9anime.org.lv/some-show-episode-3/" title="An episode, not a series">
    <img src="https://9anime.org.lv/episode.jpg">
  </a>
</article>
</div>
"""

NEXT_PAGE_LINK = """
<ul class="pagination">
  <li><a href="https://9anime.org.lv/genres/mecha/page/3/">3</a></li>
</ul>
"""


def _serve(monkeypatch, html):
    seen = []

    def get_html(url, timeout=25):
        seen.append(url)
        return html

    monkeypatch.setattr(_api, "get_html", get_html)
    return seen


def test_every_genre_link_is_read_with_its_name_and_slug(monkeypatch):
    _serve(monkeypatch, HOME_PAGE)
    assert _api.fetch_genres() == [
        {"name": "Action", "slug": "action"},
        {"name": "Fantasy & Magic", "slug": "fantasy"},
    ]


def test_a_genre_announced_twice_is_only_listed_once(monkeypatch):
    _serve(monkeypatch, HOME_PAGE)
    slugs = [genre["slug"] for genre in _api.fetch_genres()]
    assert slugs.count("action") == 1


def test_only_genres_are_read_off_the_page(monkeypatch):
    _serve(monkeypatch, HOME_PAGE)
    assert "not-a-genre" not in {genre["slug"] for genre in _api.fetch_genres()}


def test_the_genre_limit_stops_the_walk_early(monkeypatch):
    seen = _serve(monkeypatch, HOME_PAGE)
    assert len(_api.fetch_genres(limit=1)) == 1
    assert seen == ["https://9anime.org.lv/"]


def test_a_homepage_that_cannot_be_fetched_is_handed_to_the_caller(monkeypatch):
    """The caller decides: _cached turns this into a 500 rather than an empty bar."""
    def fail(url, timeout=25):
        raise RuntimeError("boom")

    monkeypatch.setattr(_api, "get_html", fail)
    with pytest.raises(RuntimeError, match="boom"):
        _api.fetch_genres()


def test_a_page_without_genres_has_no_static_fallback(monkeypatch):
    _serve(monkeypatch, "<html><body>nothing here</body></html>")
    assert _api.fetch_genres() == []


def test_a_genre_page_is_requested_through_its_slug(monkeypatch):
    seen = _serve(monkeypatch, GENRE_PAGE)
    _api.fetch_genre_animes("mecha")
    assert seen == ["https://9anime.org.lv/genres/mecha/"]


def test_the_cards_on_a_genre_page_are_parsed(monkeypatch):
    _serve(monkeypatch, GENRE_PAGE)
    result = _api.fetch_genre_animes("mecha")
    assert [item["title"] for item in result["results"]] == ["Bullbuster"]
    assert result["results"][0]["url"] == "https://9anime.org.lv/anime/bullbuster/"
    assert result["results"][0]["poster_url"] == "https://9anime.org.lv/poster.jpg"


def test_every_card_carries_the_fields_the_ui_needs(monkeypatch):
    _serve(monkeypatch, GENRE_PAGE)
    for item in _api.fetch_genre_animes("mecha")["results"]:
        assert set(item) == {"title", "url", "poster_url"}


def test_later_pages_are_numbered_and_page_one_is_not(monkeypatch):
    seen = _serve(monkeypatch, GENRE_PAGE)
    _api.fetch_genre_animes("mecha", page=1)
    _api.fetch_genre_animes("mecha", page=4)
    assert seen == [
        "https://9anime.org.lv/genres/mecha/",
        "https://9anime.org.lv/genres/mecha/page/4/",
    ]


def test_a_slug_is_url_encoded(monkeypatch):
    seen = _serve(monkeypatch, GENRE_PAGE)
    _api.fetch_genre_animes("sci fi")
    assert seen == ["https://9anime.org.lv/genres/sci%20fi/"]


def test_the_next_pager_link_is_what_says_there_is_more(monkeypatch):
    _serve(monkeypatch, GENRE_PAGE + NEXT_PAGE_LINK)
    assert _api.fetch_genre_animes("mecha", page=2)["has_more"] is True


def test_the_last_page_reports_no_more(monkeypatch):
    _serve(monkeypatch, GENRE_PAGE + NEXT_PAGE_LINK)
    assert _api.fetch_genre_animes("mecha", page=3)["has_more"] is False
    assert _api.fetch_genre_animes("mecha")["has_more"] is False


def test_a_failed_fetch_is_not_swallowed(monkeypatch):
    def fail(url, timeout=25):
        raise RuntimeError("boom")

    monkeypatch.setattr(_api, "get_html", fail)
    try:
        _api.fetch_genre_animes("mecha")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("a failed fetch should raise")


def test_a_page_without_cards_is_empty_not_an_error(monkeypatch):
    _serve(monkeypatch, "<html><body>no cards</body></html>")
    assert _api.fetch_genre_animes("mecha") == {"results": [], "has_more": False}
