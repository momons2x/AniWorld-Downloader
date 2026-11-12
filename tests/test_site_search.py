"""Offline tests for the per-site search adapters wired into sitesearch.

Each test stubs the site's own HTTP helper, so nothing here reaches the net.
"""

import pytest

from aniworld.web import sitesearch

NINEANIME_HTML = """
<div class="listupd">
<article class="bs" itemscope="itemscope">
	<div class="bsx">
		<a href="https://9anime.org.lv/anime/naruto/" itemprop="url"
		   title="Naruto" class="tip" rel="1">
			<div class="limit"><img src="https://9anime.org.lv/poster.jpg"></div>
		</a>
	</div>
</article>
<article class="bs" itemscope="itemscope">
	<div class="bsx">
		<a href="https://9anime.org.lv/naruto-episode-1/" itemprop="url"
		   title="Naruto Episode 1" class="tip" rel="2">
			<div class="limit"><img src="https://9anime.org.lv/episode.jpg"></div>
		</a>
	</div>
</article>
</div>
"""


def test_nineanime_search_keeps_series_links_only(monkeypatch):
    monkeypatch.setattr(
        "aniworld.models.nineanime._api.get_html", lambda url, timeout=20: NINEANIME_HTML
    )
    assert sitesearch.search("nineanime", "naruto") == [
        {
            "title": "Naruto",
            "url": "https://9anime.org.lv/anime/naruto/",
            "poster": "https://9anime.org.lv/poster.jpg",
        }
    ]


def test_nineanime_search_survives_a_failed_page(monkeypatch):
    def fail(url, timeout=20):
        raise RuntimeError("boom")

    monkeypatch.setattr("aniworld.models.nineanime._api.get_html", fail)
    assert sitesearch.search("nineanime", "naruto") == []
    with pytest.raises(RuntimeError):
        sitesearch.search("nineanime", "naruto", raise_errors=True)


def test_miruro_search_builds_series_urls(monkeypatch):
    monkeypatch.setattr(
        "aniworld.models.miruro._api.api_get",
        lambda path, params=None: {
            "data": [
                {
                    "id": "abc123",
                    "title": {"romaji": "NARUTO", "english": "Naruto"},
                    "cover_url": "https://img.example/naruto.jpg",
                }
            ]
        },
    )
    assert sitesearch.search("miruro", "naruto") == [
        {
            "title": "Naruto",
            "url": "https://miruro.tv/info/abc123/naruto",
            "poster": "https://img.example/naruto.jpg",
        }
    ]


def test_reanime_search_builds_series_urls(monkeypatch):
    monkeypatch.setattr(
        "aniworld.models.reanime._api.api_get",
        lambda path, params=None: {
            "results": [
                {
                    "anime_id": "naruto-xyz",
                    "title": {"romaji": "NARUTO", "english": "Naruto"},
                    "cover_image": {
                        "extra_large": "https://img.example/xl.jpg",
                        "large": "https://img.example/l.jpg",
                    },
                }
            ]
        },
    )
    assert sitesearch.search("reanime", "naruto") == [
        {
            "title": "Naruto",
            "url": "https://reanime.to/anime/naruto-xyz",
            "poster": "https://img.example/xl.jpg",
        }
    ]


def test_every_site_tab_has_a_search_backend():
    assert set(sitesearch.SITE_SEARCH) >= {
        "aniworld",
        "nineanime",
        "miruro",
        "reanime",
    }
