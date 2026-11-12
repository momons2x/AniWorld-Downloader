from types import SimpleNamespace

import pytest

from aniworld import search

QUERIES = [
    (search.fetch_genre_animes, {"slug": "action"}),
    (search.query_aniworld, {"genre": "action"}),
]


@pytest.mark.parametrize(
    "query,kwargs",
    QUERIES,
)
@pytest.mark.parametrize("limit", [-1, 1.5, "10", True])
def test_invalid_limits_fail_before_fetch(query, kwargs, limit):
    with pytest.raises(ValueError, match="limit must be"):
        query(**kwargs, limit=limit)


@pytest.mark.parametrize(
    "query,kwargs",
    QUERIES,
)
def test_zero_limit_skips_fetch(query, kwargs):
    result = query(**kwargs, limit=0)
    assert (result["results"] if isinstance(result, dict) else result) == []


@pytest.mark.parametrize(
    "query,kwargs",
    [(search.fetch_genre_animes, {"slug": "action"}), (search.query_aniworld, {"genre": "action"})],
)
def test_limits_apply_to_each_backend(monkeypatch, query, kwargs):
    rows = "".join(
        f'<div data-group="shows"><a href="/serie/item-{n}"></a><h6 class="show-title">Item {n}</h6></div>'
        f'<article class="liste"><h2><a href="/stream/item-{n}">Item {n}</a></h2></article>'
        f'<a href="/movies/item-{n}"><h3 class="movie-poster-grid-card__title">Item {n}</h3></a>'
        f'<td class="Title"><a href="/Stream/item-{n}.html">Item {n}</a></td>'
        for n in range(5)
    )
    response = SimpleNamespace(text=rows, raise_for_status=lambda: None)
    monkeypatch.setattr(search.GLOBAL_SESSION, "get", lambda *a, **k: response)
    monkeypatch.setattr(
        search, "_parse_cover_items", lambda *a: [{"title": str(n)} for n in range(5)]
    )
    for limit in (2, None):
        result = query(**kwargs, limit=limit)
        assert len(result["results"] if isinstance(result, dict) else result) == (
            2 if limit else 5
        )
