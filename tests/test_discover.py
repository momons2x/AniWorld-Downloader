"""Offline tests for the AniList discovery layer (My Anime -> Discover)."""

import aniworld.web.discover as dmod
from aniworld.web import discover


def _page(media, info=None):
    return {
        "data": {
            "Page": {
                "pageInfo": info
                or {"total": 1, "currentPage": 1, "lastPage": 1, "hasNextPage": False},
                "media": media,
            }
        }
    }


MEDIA = {
    "id": 20,
    "title": {"english": "Naruto", "romaji": "NARUTO"},
    "genres": ["Action", "Adventure"],
    "coverImage": {
        "extraLarge": "https://cdn.example/cover.jpg",
        "large": "https://cdn.example/small.jpg",
    },
    "averageScore": 80,
    "seasonYear": 2002,
    "episodes": 220,
    "status": "FINISHED",
    "format": "TV",
}


def test_list_anime_trending_shape(monkeypatch):
    calls = []

    def fake(query, variables):
        calls.append(query)
        return _page([MEDIA])

    monkeypatch.setattr(dmod, "_post", fake)
    result = dmod.list_anime(kind="trending")
    assert result["results"] == [
        {
            "anilist_id": 20,
            "title": "Naruto",
            "title_romaji": "NARUTO",
            "cover": "https://cdn.example/cover.jpg",
            "year": 2002,
            "genres": ["Action", "Adventure"],
            "score": 80,
            "episodes": 220,
            "status": "FINISHED",
            "format": "TV",
        }
    ]
    assert result["page"] == 1 and result["last_page"] == 1 and result["has_next"] is False
    assert "TRENDING_DESC" in calls[0]


def test_list_anime_passes_genre_and_never_adult(monkeypatch):
    def fake(query, variables):
        assert 'genre: "Fantasy"' in query
        assert "isAdult: false" in query
        return _page([MEDIA])

    monkeypatch.setattr(dmod, "_post", fake)
    dmod.list_anime(kind="top", genre="Fantasy", page=2)

    # malformed genre silently dropped rather than exploding
    def fake2(query, variables):
        assert "genre:" not in query
        return _page([MEDIA])

    monkeypatch.setattr(dmod, "_post", fake2)
    dmod.list_anime(genre="NotARealGenre")


def test_list_anime_passes_tag_and_drops_unknown(monkeypatch):
    def fake(query, variables):
        assert 'tag: "Isekai"' in query
        return _page([MEDIA])

    monkeypatch.setattr(dmod, "_post", fake)
    dmod.list_anime(genre="Fantasy", tag="Isekai")

    def fake2(query, variables):
        assert "tag:" not in query
        return _page([MEDIA])

    monkeypatch.setattr(dmod, "_post", fake2)
    dmod.list_anime(tag="NotARealTag")


def test_list_anime_random_uses_random_page(monkeypatch):
    seen_pages = []

    def fake(query, variables):
        seen_pages.append(variables.get("page"))
        return _page(
            [MEDIA],
            info={
                "total": 5000,
                "currentPage": variables.get("page") or 1,
                "lastPage": 250,
                "hasNextPage": True,
            },
        )

    monkeypatch.setattr(dmod, "_post", fake)
    result = dmod.list_anime(kind="random", page=1)
    assert 1 <= result["page"] <= 250
    assert result["page"] in seen_pages


def test_get_anime_maps_details(monkeypatch):
    def fake(query, variables):
        return {
            "data": {
                "Media": {
                    **MEDIA,
                    "description": "<p>Long ago, the Fourth Hokage...</p>",
                    "bannerImage": "https://cdn.example/banner.jpg",
                    "externalLinks": [
                        {"id": 1, "site": "Twitter", "url": "https://x", "type": "SOCIAL"}
                    ],
                }
            }
        }

    monkeypatch.setattr(dmod, "_post", fake)
    item = dmod.get_anime(20)
    assert item["anilist_id"] == 20
    assert item["title"] == "Naruto"
    assert "Long ago" in item["description"]
    assert item["banner"] == "https://cdn.example/banner.jpg"
    assert item["external_links"][0]["site"] == "Twitter"


def test_get_anime_missing_returns_none(monkeypatch):
    monkeypatch.setattr(dmod, "_post", lambda query, variables: {"data": {"Media": None}})
    assert dmod.get_anime(999999) is None


def test_current_season_windows():
    from datetime import date

    assert discover.current_season(date(2026, 1, 15)) == ("WINTER", 2026)
    assert discover.current_season(date(2026, 4, 1)) == ("SPRING", 2026)
    assert discover.current_season(date(2026, 7, 1)) == ("SUMMER", 2026)
    assert discover.current_season(date(2026, 12, 20)) == ("WINTER", 2027)


def test_discover_genres_leave_adult_out():
    assert "Hentai" not in discover.DISCOVER_GENRES
    assert discover.DISCOVER_KINDS == ("trending", "season", "top", "random")


def test_discover_tags_are_popular_anilist_tags():
    assert "Isekai" in discover.DISCOVER_TAGS
    assert "Reincarnation" in discover.DISCOVER_TAGS
    assert "Cute Girls Doing Cute Things" in discover.DISCOVER_TAGS
    assert "Boys' Love" in discover.DISCOVER_TAGS
    assert "Hentai" not in discover.DISCOVER_TAGS
    assert discover.DISCOVER_TAGS
