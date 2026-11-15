"""Common public imports and search options work across site backends."""

import aniworld
from aniworld import models
from aniworld.web import sitesearch


def test_every_public_model_is_available_at_the_package_root():
    for name in models.__all__:
        assert getattr(aniworld, name) is getattr(models, name)


def test_genre_discovery_uses_name_and_slug_for_every_site(monkeypatch):
    monkeypatch.setitem(
        sitesearch.GENRE_LISTS,
        "nineanime",
        lambda: [{"name": "New Tag", "slug": "New Tag"}],
    )
    assert sitesearch.genres("nineanime") == [{"name": "New Tag", "slug": "New Tag"}]


def test_sites_without_a_genre_backend_answer_with_nothing():
    for site in ("miruro", "reanime", "aniworld", ""):
        assert sitesearch.genres(site) == []


def test_genre_sites_are_exactly_the_sites_with_a_backend():
    assert sitesearch.GENRE_SITES == tuple(sitesearch.GENRE_LISTS)
    assert "nineanime" in sitesearch.GENRE_SITES
