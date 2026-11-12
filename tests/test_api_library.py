"""Library, genre and page endpoints."""

import curl_cffi.requests
import niquests
import pytest

from aniworld.web import db
from aniworld.web.views import api_media


@pytest.fixture(autouse=True)
def empty_browse_cache():
    """The browse cache lives for the process, clear it between tests."""
    api_media._browse_cache.clear()
    yield
    api_media._browse_cache.clear()


@pytest.mark.parametrize("site", ["aniworld", "nineanime", "miruro"])
def test_search_errors_are_reported_without_exposing_exception_details(
    client, monkeypatch, caplog, site
):
    from aniworld.web import sitesearch

    def fail(keyword):
        raise RuntimeError("private-session-value")

    monkeypatch.setitem(sitesearch.SITE_SEARCH, site, fail)
    response = client.post("/api/search", json={"site": site, "keyword": "test"})
    assert response.status_code == 502
    assert "error" in response.get_json()
    assert "results" not in response.get_json()
    assert "private-session-value" not in response.get_data(as_text=True)
    assert "private-session-value" not in caplog.text


def test_search_distinguishes_no_matches_from_upstream_failure(client, monkeypatch):
    monkeypatch.setitem(api_media.sitesearch.SITE_SEARCH, "nineanime", lambda keyword: [])
    response = client.post("/api/search", json={"site": "nineanime", "keyword": "test"})
    assert response.status_code == 200
    assert response.get_json() == {"results": []}


def test_aggregate_search_can_still_skip_a_failed_source(monkeypatch):
    from aniworld.web import sitesearch

    def fail(keyword):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(sitesearch, "MOVIE_SITES", ("miruro", "reanime"))
    monkeypatch.setitem(sitesearch.SITE_SEARCH, "miruro", fail)
    monkeypatch.setitem(
        sitesearch.SITE_SEARCH,
        "reanime",
        lambda keyword: [
            {"title": "Example", "url": "https://re-anime.to/anime/example"}
        ],
    )
    assert sitesearch.aggregate("test", "movie")[0]["site"] == "reanime"


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------
def test_locations_are_listed(client, downloads):
    body = client.get("/api/library/locations").get_json()
    assert body["locations"][0]["path"] == str(downloads)


def test_titles_are_listed(client, episode_file):
    episode_file("Naruto", 1, 1)
    titles = client.get("/api/library/titles").get_json()["titles"]
    assert [t["folder"] for t in titles] == ["Naruto"]


def test_titles_of_a_custom_path(client, episode_file, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    path_id = db.add_custom_path("Other", str(other))
    episode_file("Naruto", 1, 1, base=other)
    body = client.get(f"/api/library/titles?path_id={path_id}").get_json()
    assert [t["folder"] for t in body["titles"]] == ["Naruto"]


def test_a_bad_path_id_is_a_400(client):
    assert client.get("/api/library/titles?path_id=4242").status_code == 400


def test_a_non_numeric_path_id_is_treated_as_the_default(client, episode_file):
    episode_file("Naruto", 1, 1)
    assert client.get("/api/library/titles?path_id=abc").status_code == 200


def test_one_title_is_read(client, episode_file):
    episode_file("Naruto", 1, 1)
    body = client.get("/api/library/title?folder=Naruto").get_json()
    assert body["total_episodes"] == 1
    assert body["seasons"]["1"][0]["episode"] == 1


def test_reading_needs_a_folder(client):
    assert client.get("/api/library/title").status_code == 400


def test_reading_a_missing_title_is_a_400(client):
    assert client.get("/api/library/title?folder=Nope").status_code == 400


@pytest.mark.parametrize("folder", ["../../etc", "..", "a/b"])
def test_reading_refuses_traversal(client, folder):
    assert client.get(f"/api/library/title?folder={folder}").status_code == 400


def test_a_title_can_be_deleted(client, episode_file, downloads):
    episode_file("Naruto", 1, 1)
    body = client.post("/api/library/delete", json={"folder": "Naruto"}).get_json()
    assert body == {"ok": True, "deleted": 1}
    assert not (downloads / "Naruto").exists()


def test_an_episode_can_be_deleted(client, episode_file):
    episode_file("Naruto", 1, 1)
    episode_file("Naruto", 1, 2)
    response = client.post(
        "/api/library/delete", json={"folder": "Naruto", "season": 1, "episode": 1}
    )
    assert response.get_json()["deleted"] == 1
    assert (
        client.get("/api/library/title?folder=Naruto").get_json()["total_episodes"] == 1
    )


def test_deleting_needs_a_folder(client):
    assert client.post("/api/library/delete", json={}).status_code == 400


def test_deleting_something_missing_is_a_400(client):
    assert (
        client.post("/api/library/delete", json={"folder": "Nope"}).status_code == 400
    )


def test_every_library_endpoint_closes_when_the_library_is_off(client, monkeypatch):
    monkeypatch.setenv("ANIWORLD_ENABLE_LIBRARY", "0")
    assert client.get("/api/library/locations").status_code == 404
    assert client.get("/api/library/titles").status_code == 404
    assert client.get("/api/library/title?folder=x").status_code == 404
    assert client.post("/api/library/delete", json={"folder": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# Genres
# ---------------------------------------------------------------------------
@pytest.fixture
def genres(monkeypatch):
    from aniworld.web import sitesearch

    listing = [
        {"name": "Action", "slug": "action"},
        {"name": "Mecha", "slug": "mecha"},
    ]
    monkeypatch.setattr(sitesearch, "genres", lambda site: listing)

    pages = {}

    def fetch(slug, page=1):
        return pages.get((slug, page))

    monkeypatch.setattr(api_media, "fetch_genre_animes", fetch)
    return pages


def test_the_genre_list_is_served(client, genres):
    body = client.get("/api/genres").get_json()
    assert [genre["slug"] for genre in body["genres"]] == ["action", "mecha"]


def test_a_genre_page_is_served_with_proxied_posters(client, genres):
    genres[("mecha", 1)] = {
        "results": [
            {
                "title": "BULLBUSTER",
                "url": "https://aniworld.to/anime/stream/bullbuster",
                "genre": "Mecha",
                "poster_url": "https://aniworld.to/p.jpg",
            }
        ],
        "has_more": True,
    }
    body = client.get("/api/genre?slug=mecha").get_json()
    assert body["page"] == 1
    assert body["has_more"] is True
    assert body["results"][0]["title"] == "BULLBUSTER"
    assert body["results"][0]["poster_url"].startswith("/api/proxy-image?url=")


def test_a_later_page_is_requested(client, genres):
    genres[("mecha", 2)] = {"results": [], "has_more": False}
    assert client.get("/api/genre?slug=mecha&page=2").get_json()["page"] == 2


@pytest.mark.parametrize("slug", ["", "../../etc/passwd", "action/../mecha"])
def test_invalid_genre_path_is_rejected(client, genres, slug):
    assert client.get(f"/api/genre?slug={slug}").status_code == 400


def test_genre_missing_from_discovery_can_be_requested(client, genres):
    genres[("new-genre", 1)] = {"results": [], "has_more": False}
    assert client.get("/api/genre?slug=new-genre").status_code == 200


def test_site_404_is_reported(client, monkeypatch):
    import niquests

    def fetch(slug, page=1):
        response = niquests.Response()
        response.status_code = 404
        response.raise_for_status()

    monkeypatch.setattr(api_media, "fetch_genre_animes", fetch)
    response = client.get("/api/genre?slug=missing")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Genre not available: missing"


def test_a_non_numeric_page_is_a_400(client, genres):
    assert client.get("/api/genre?slug=mecha&page=abc").status_code == 400


@pytest.mark.parametrize("page", ["0", "-5"])
def test_a_page_below_one_is_clamped(client, genres, page):
    genres[("mecha", 1)] = {"results": [], "has_more": False}
    assert client.get(f"/api/genre?slug=mecha&page={page}").get_json()["page"] == 1


def test_a_failed_genre_fetch_is_a_500(client, genres):
    assert client.get("/api/genre?slug=mecha").status_code == 500


def test_genre_pages_are_cached(client, genres, monkeypatch):
    calls = []

    def fetch(slug, page=1):
        calls.append((slug, page))
        return {"results": [], "has_more": False}

    monkeypatch.setattr(api_media, "fetch_genre_animes", fetch)
    client.get("/api/genre?slug=mecha")
    client.get("/api/genre?slug=mecha")
    assert calls == [("mecha", 1)], "the second call comes from the cache"


def test_different_pages_are_cached_separately(client, genres, monkeypatch):
    calls = []

    def fetch(slug, page=1):
        calls.append((slug, page))
        return {"results": [], "has_more": False}

    monkeypatch.setattr(api_media, "fetch_genre_animes", fetch)
    client.get("/api/genre?slug=mecha&page=1")
    client.get("/api/genre?slug=mecha&page=2")
    assert calls == [("mecha", 1), ("mecha", 2)]


def test_an_unknown_site_offers_no_genres(client):
    assert client.get("/api/genres?site=unknown").get_json() == {"genres": []}
    assert client.get("/api/genre?site=unknown&slug=action").status_code == 400


@pytest.mark.parametrize("library", [niquests, curl_cffi.requests])
@pytest.mark.parametrize("status", [404, 422, 503])
def test_genre_http_errors_return_json(client, monkeypatch, library, status):
    def fetch(slug, page):
        response = library.Response()
        response.status_code = status
        raise library.exceptions.HTTPError("upstream error", response=response)

    monkeypatch.setattr(api_media, "fetch_genre_animes", fetch)
    response = client.get("/api/genre?site=aniworld&slug=Action")
    assert response.status_code == (404 if status == 404 else 502)
    assert response.get_json() == {
        "error": "Genre not available: Action"
        if status == 404
        else "Failed to fetch genre Action"
    }
    assert "genre:aniworld:Action:1" not in api_media._browse_cache


@pytest.mark.parametrize("library", [niquests, curl_cffi.requests])
def test_genre_timeouts_return_json(client, monkeypatch, library):
    def fetch(slug, page):
        raise library.exceptions.Timeout("upstream timed out")

    monkeypatch.setattr(api_media, "fetch_genre_animes", fetch)
    response = client.get("/api/genre?site=aniworld&slug=Action")
    assert response.status_code == 502
    assert response.get_json() == {"error": "Failed to fetch genre Action"}
    assert "genre:aniworld:Action:1" not in api_media._browse_cache


def test_the_genre_list_is_cached_per_site(client, monkeypatch):
    from aniworld.web import sitesearch

    calls = []

    def listing(site):
        calls.append(site)
        return [{"name": "Horror", "slug": "horror"}]

    monkeypatch.setattr(sitesearch, "GENRE_SITES", ("miruro", "reanime"))
    monkeypatch.setattr(sitesearch, "genres", listing)
    client.get("/api/genres?site=miruro")
    client.get("/api/genres?site=miruro")
    client.get("/api/genres?site=reanime")
    assert calls == ["miruro", "reanime"], "only the second miruro call is cached"


@pytest.fixture
def flat_listing(monkeypatch):
    """Every site but aniworld hands back one list instead of pages."""
    from aniworld.web import sitesearch

    monkeypatch.setattr(sitesearch, "GENRE_SITES", ("miruro",))
    calls = []
    titles = [
        {"title": f"Movie {n}", "url": f"https://kinox.to/{n}.html", "poster": ""}
        for n in range(100)
    ]

    def results(site, genre, limit):
        calls.append((site, genre, limit))
        page = titles[:limit]
        return page, len(page) >= limit

    monkeypatch.setattr(sitesearch, "genre_results", results)
    return calls


def test_a_flat_listing_is_sliced_into_pages(client, flat_listing):
    body = client.get("/api/genre?site=miruro&slug=Horror&page=2").get_json()
    # one title past the page, which is what settles has_more
    assert flat_listing == [("miruro", "Horror", 61)]
    assert [item["title"] for item in body["results"]] == [
        f"Movie {n}" for n in range(30, 60)
    ]
    assert body["has_more"] is True


def test_the_end_of_a_flat_listing_reports_no_more(client, monkeypatch):
    from aniworld.web import sitesearch

    monkeypatch.setattr(sitesearch, "GENRE_SITES", ("miruro",))
    monkeypatch.setattr(
        sitesearch,
        "genre_results",
        lambda site, genre, limit: (
            [{"title": "Nosferatu", "url": "https://x/1", "poster": "https://x/p.jpg"}],
            False,
        ),
    )
    body = client.get("/api/genre?site=miruro&slug=horror").get_json()
    assert body["has_more"] is False
    assert body["results"][0]["poster_url"].startswith("/api/proxy-image?url=")


def test_a_full_page_the_site_could_not_fill_still_offers_more(client, monkeypatch):
    """A title normalising away must not read as the end of the listing."""
    from aniworld.web import sitesearch

    monkeypatch.setattr(sitesearch, "GENRE_SITES", ("miruro",))
    monkeypatch.setattr(
        sitesearch,
        "genre_results",
        lambda site, genre, limit: (
            [
                {"title": f"Show {n}", "url": f"https://x/{n}", "poster": ""}
                for n in range(limit - 1)
            ],
            True,
        ),
    )
    body = client.get("/api/genre?site=miruro&slug=horror").get_json()
    assert len(body["results"]) == 30
    assert body["has_more"] is True


def test_a_genre_a_site_does_not_have_is_a_404(client, monkeypatch):
    from aniworld.web import sitesearch

    monkeypatch.setattr(sitesearch, "GENRE_SITES", ("miruro",))
    def results(site, genre, limit):
        raise ValueError(f"Genre not available: {genre}")

    monkeypatch.setattr(sitesearch, "genre_results", results)
    response = client.get("/api/genre?site=miruro&slug=missing")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Genre not available: missing"


# ---------------------------------------------------------------------------
# Browse rows
# ---------------------------------------------------------------------------
def test_a_browse_row_is_served_from_the_cache(client):
    """The row views hold their fetch function directly, so the cache is
    seeded here rather than stubbing the fetch."""
    import time

    api_media._browse_cache["new_animes"] = (
        time.time(),
        [{"title": "Naruto", "url": "https://x", "poster_url": "https://x/p.jpg"}],
    )
    body = client.get("/api/new-animes").get_json()
    assert body["results"][0]["title"] == "Naruto"
    assert body["results"][0]["poster_url"].startswith("/api/proxy-image?url=")


def test_a_browse_row_that_fails_is_a_500(client, monkeypatch):
    monkeypatch.setattr(api_media, "_cached", lambda key, fetch, **kwargs: None)
    assert client.get("/api/new-animes").status_code == 500


def test_a_failed_fetch_is_not_cached(monkeypatch):
    """A blip must not blank the row for the next hour."""
    calls = []

    def flaky():
        calls.append(1)
        raise RuntimeError("upstream down")

    assert api_media._cached("row", flaky) is None
    assert api_media._cached("row", flaky) is None
    assert len(calls) == 2, "it is tried again rather than served from a cached failure"


def test_a_good_fetch_is_cached(monkeypatch):
    calls = []

    def once():
        calls.append(1)
        return [{"title": "Naruto"}]

    assert api_media._cached("row", once) == [{"title": "Naruto"}]
    assert api_media._cached("row", once) == [{"title": "Naruto"}]
    assert len(calls) == 1


def test_a_stale_cache_entry_is_refetched(monkeypatch):
    import time

    calls = []

    def fetch():
        calls.append(1)
        return ["fresh"]

    api_media._browse_cache["row"] = (time.time() - api_media.BROWSE_TTL - 1, ["stale"])
    assert api_media._cached("row", fetch) == ["fresh"]
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def test_the_home_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"AniWorld" in response.data


def test_the_settings_page_renders(client):
    assert client.get("/settings").status_code == 200


def test_the_library_page_renders(client):
    assert client.get("/library").status_code == 200


def test_the_autosync_page_is_hidden_until_enabled(client, monkeypatch):
    assert client.get("/autosync").status_code == 404
    monkeypatch.setenv("ANIWORLD_ENABLE_AUTOSYNC", "1")
    assert client.get("/autosync").status_code == 200


def test_adult_sites_are_never_rendered(client):
    body = client.get("/").data
    assert b'data-site="htv"' not in body
    assert b'data-site="hentaihaven"' not in body


def test_the_genre_bar_is_on_the_home_page(client):
    assert b'id="genreList"' in client.get("/").data


def test_the_favicon_is_served(client):
    assert client.get("/favicon.ico").status_code == 200


def test_an_unknown_page_is_a_404(client):
    assert client.get("/nope").status_code == 404


# ---------------------------------------------------------------------------
# Playback + AniList metadata endpoints
# ---------------------------------------------------------------------------
def test_meta_endpoint_returns_anilist_metadata(client, episode_file, monkeypatch):
    episode_file("Naruto", 1, 1)

    def fake_get(folder, refresh=False):
        return {
            "anilist_id": 20,
            "title_en": "Naruto",
            "genres": ["Action"],
            "score": 78,
            "year": 2002,
            "format": "TV",
            "status": "FINISHED",
            "cover": "https://cdn.example/cover.jpg",
            "banner": "https://cdn.example/banner.jpg",
        }

    monkeypatch.setattr("aniworld.web.views.api_library.anilist.get_meta", fake_get)
    response = client.get("/api/library/meta?folder=Naruto")
    assert response.status_code == 200
    data = response.get_json()
    assert data["title_en"] == "Naruto"
    assert data["score"] == 78


def test_meta_endpoint_404_without_a_match(client, episode_file, monkeypatch):
    episode_file("Naruto", 1, 1)
    monkeypatch.setattr(
        "aniworld.web.views.api_library.anilist.get_meta", lambda folder, refresh=False: None
    )
    assert client.get("/api/library/meta?folder=Naruto").status_code == 404


def test_play_endpoint_mints_token_and_streams(client, episode_file, monkeypatch):
    episode_file("Naruto", 1, 1)
    from aniworld.web import playback

    def fake_start_async(folder, season, episode, custom_path_id=None, lang_folder=None):
        src, _ = __import__("aniworld.web.library", fromlist=["find_video"]).find_video(
            folder, season, episode
        )
        return {"status": "ready", "token": playback.register(src, source_path=src)}

    monkeypatch.setattr(playback, "start_async", fake_start_async)
    response = client.post(
        "/api/library/play",
        json={"folder": "Naruto", "season": 1, "episode": 1},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ready"
    assert data["token"]
    assert "Naruto S01E001.mkv" in data["label"]

    stream = client.get(f"/api/library/stream?token={data['token']}")
    assert stream.status_code in (200, 206)
    assert stream.headers.get("Accept-Ranges") == "bytes"


def test_play_endpoint_passes_audio_and_force_transcode(client, episode_file, monkeypatch):
    src = episode_file("Naruto", 1, 1)
    from aniworld.web import playback

    captured = {}

    def fake_start_async(
        folder,
        season,
        episode,
        custom_path_id=None,
        lang_folder=None,
        audio_ordinal=None,
        force_transcode=False,
    ):
        captured.update(
            audio_ordinal=audio_ordinal, force_transcode=force_transcode
        )
        token = playback.register(
            src,
            source_path=src,
            audio_tracks=[
                {"ordinal": 0, "language": "ja", "label": "Japanese", "codec": "aac", "channels": 2, "default": False}
            ],
            subtitle_tracks=[
                {"index": 2, "language": "en", "label": "English", "codec": "ass", "default": True, "forced": False}
            ],
            selected_audio=1,
            mode="remux",
        )
        return {"status": "ready", "token": token}

    monkeypatch.setattr(playback, "start_async", fake_start_async)
    response = client.post(
        "/api/library/play",
        json={
            "folder": "Naruto",
            "season": 1,
            "episode": 1,
            "audio": 1,
            "force_transcode": True,
        },
    )
    assert response.status_code == 200
    data = response.get_json()
    assert captured == {"audio_ordinal": 1, "force_transcode": True}
    assert data["selected_audio"] == 1
    assert data["mode"] == "remux"
    assert data["audio_tracks"][0]["ordinal"] == 0
    assert data["subtitle_tracks"][0]["url"].startswith(
        "/api/library/subtitles?token="
    )


def test_play_endpoint_rejects_invalid_audio(client, episode_file):
    episode_file("Naruto", 1, 1)
    response = client.post(
        "/api/library/play",
        json={"folder": "Naruto", "season": 1, "episode": 1, "audio": "nope"},
    )
    assert response.status_code == 400


def test_subtitle_endpoint_validates_token_and_track(client):
    from aniworld.web import playback

    token = playback.register("x.mp4", source_path="x.mp4")
    assert client.get(f"/api/library/subtitles?token={token}&track=1").status_code == 400
    assert client.get("/api/library/subtitles?token=nope&track=1").status_code == 400
    assert client.get("/api/library/subtitles").status_code == 400


def test_stream_endpoint_uses_session_mime(client, tmp_path):
    from aniworld.web import playback

    path = tmp_path / "clip.webm"
    path.write_bytes(b"x" * 16)
    token = playback.register(path, source_path=path, mime="video/webm")
    response = client.get(f"/api/library/stream?token={token}")
    assert response.status_code in (200, 206)
    assert response.mimetype == "video/webm"


def test_play_endpoint_returns_job_while_preparing(client, episode_file, monkeypatch):
    episode_file("Naruto", 1, 1)
    from aniworld.web import playback

    monkeypatch.setattr(
        playback, "start_async", lambda *a, **k: {"status": "preparing", "job": "abc"}
    )
    response = client.post(
        "/api/library/play",
        json={"folder": "Naruto", "season": 1, "episode": 1},
    )
    assert response.status_code == 202
    assert response.get_json() == {"status": "preparing", "job": "abc"}


def test_play_status_unknown_job(client):
    response = client.get("/api/library/play/status?job=nope")
    assert response.status_code == 400
    response = client.get("/api/library/play/status")
    assert response.status_code == 400


def test_stream_endpoint_rejects_bad_tokens(client):
    response = client.get("/api/library/stream?token=nope")
    assert response.status_code == 400
    response = client.get("/api/library/stream")
    assert response.status_code == 400


def test_play_endpoint_validates_input(client):
    response = client.post("/api/library/play", json={})
    assert response.status_code == 400
    response = client.post(
        "/api/library/play", json={"folder": "Naruto", "season": 1, "episode": 9}
    )
    assert response.status_code == 400


def test_hide_endpoint_filters_titles(client, episode_file):
    episode_file("Naruto", 1, 1)
    episode_file("Secret", 1, 1)
    response = client.post(
        "/api/library/hide", json={"folder": "Secret", "hidden": True}
    )
    assert response.get_json() == {"ok": True, "hidden": True}
    titles = client.get("/api/library/titles").get_json()["titles"]
    assert [title["folder"] for title in titles] == ["Naruto"]
    shown = client.get("/api/library/titles?include_hidden=1").get_json()["titles"]
    assert shown == [
        {
            "folder": "Naruto",
            "title": "Naruto",
            "poster_url": "",
            "year": None,
            "score": None,
            "genres": [],
            "categories": ["series"],
            "hidden": False,
        },
        {
            "folder": "Secret",
            "title": "Secret",
            "poster_url": "",
            "year": None,
            "score": None,
            "genres": [],
            "categories": ["series"],
            "hidden": True,
        },
    ]


def test_hide_endpoint_needs_a_folder(client):
    assert client.post("/api/library/hide", json={}).status_code == 400


def test_hide_endpoint_closes_when_the_library_is_off(client, monkeypatch):
    monkeypatch.setenv("ANIWORLD_ENABLE_LIBRARY", "0")
    assert client.post("/api/library/hide", json={"folder": "x"}).status_code == 404


def test_titles_include_cached_anilist_metadata(client, episode_file):
    episode_file("Naruto", 1, 1)
    db.set_library_meta(
        "Naruto",
        {
            "anilist_id": 20,
            "title_en": "Naruto",
            "title_romaji": "NARUTO",
            "cover": "https://cdn.example/cover.jpg",
            "genres": ["Action", "Adventure"],
            "score": 82,
            "year": 2002,
        },
    )
    title = client.get("/api/library/titles").get_json()["titles"][0]
    assert title["genres"] == ["Action", "Adventure"]
    assert title["poster_url"] == "https://cdn.example/cover.jpg"
    assert title["year"] == 2002
    assert title["score"] == 82
