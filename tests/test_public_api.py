"""Common public imports and search options work across site backends."""

import aniworld
from aniworld import models, search
from aniworld.web import sitesearch


def test_every_public_model_is_available_at_the_package_root():
    for name in models.__all__:
        assert getattr(aniworld, name) is getattr(models, name)


def test_genre_discovery_uses_name_and_slug_for_every_site(monkeypatch):
    monkeypatch.setattr(
        search, "fetch_genres", lambda: [{"name": "New Tag", "slug": "New Tag"}]
    )
    assert sitesearch.genres("aniworld") == [{"name": "New Tag", "slug": "New Tag"}]


def test_aniworld_genre_query_follows_pages_and_stops_at_limit(monkeypatch):
    calls = []

    def fetch(slug, page=1):
        calls.append((slug, page))
        return {"results": [{"title": str(page)}], "has_more": True}

    monkeypatch.setattr(search, "fetch_genre_animes", fetch)
    assert search.query_aniworld(genre="action", limit=2) == [
        {"title": "1"},
        {"title": "2"},
    ]
    assert calls == [("action", 1), ("action", 2)]


