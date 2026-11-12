# Dev orientation — AniWorld-Downloader fork

Local dev: `.venv/Scripts/aniworld.exe -w` → http://localhost:8080
Tests: `.venv/Scripts/python.exe -m pytest` · Lint: `.venv/Scripts/python.exe -m ruff check .`
Baseline: 1970 passed / 6 skipped, ruff clean (Python 3.14 + windows-curses).

## 1. Subtitle handling

Subtitles are modeled as **baked-in video variants**, not sidecar files.

| Concern | Location |
|---|---|
| `Audio` / `Subtitles` enums, `LANG_KEY_MAP` ("1".."4"), `LANG_LABELS`, `LANG_CODE_MAP` | `src/aniworld/config.py:330-386` |
| Core download: picks audio_code + sub_video_code, skips/muxes via ffmpeg | `src/aniworld/models/common/common.py:1175+` (`download()`, language resolution at ~1233-1240) |
| ProviderData keyed by `(Audio, Subtitles)` | `src/aniworld/models/aniworld_to/episode.py:693` (AniWorld lang_key → map) |
| Serienstream has its own `Subtitles` enum | `src/aniworld/models/s_to/episode.py:42` |
| Web language labels / availability probe | `src/aniworld/web/media.py:163-192` |
| "English Sub disabled" setting gate | `src/aniworld/web/views/api_queue.py:54-60`, `web/settings_store.py` |
| Frontend language/provider selects + badges | `src/aniworld/web/static/home.js:61, 493-551` |

Key insight: for AniWorld, "English Sub" = Japanese audio + English-subtitled **video track** selected by ffmpeg (`sub_video_code`). Improving subtitle handling = either new `Subtitles` enum values + LANG maps, or adding sidecar download/mux.

## 2. Queue / library

| Concern | Location |
|---|---|
| SQLite schema + `add_to_queue` / queue CRUD / ordering | `src/aniworld/web/db.py` (queue section ~417-600; `add_to_queue` :420) |
| Background worker (single thread, claim → build episode → `episode.download()`) | `src/aniworld/web/worker.py` |
| Queue REST API (`POST /api/download`, cancel/retry/move/captcha) | `src/aniworld/web/views/api_queue.py` |
| Library filesystem browse/delete | `src/aniworld/web/library.py`, `views/api_library.py` |
| Output path resolution / custom paths / lang folders | `src/aniworld/web/paths.py` |
| Filename templates | `src/aniworld/models/common/naming.py` (`template_path`, `%title%` style) |
| Season batch loop (fail-one-continue) | `src/aniworld/models/common/batch.py` |
| Auto-sync (schedule missing episodes) | `src/aniworld/web/autosync.py` |
| Frontend queue page | `static/queue.js`, `templates/queue.html` |
| Frontend library page | `static/library.js`, `templates/library.html` |

Flow: UI → `POST /api/download` → `db.add_to_queue` → `worker.ensure_started()` → `_process()` resolves provider via `providers.resolve_provider(url)` → `episode.download()`.

Library playback (watch page):

| Concern | Location |
|---|---|
| ffprobe wrapper, browser compatibility, ffmpeg argv, moov duration patch | `src/aniworld/web/playback_media.py` |
| Sessions/tokens, job queue, `.stream` cache, WebVTT extraction | `src/aniworld/web/playback.py` |
| `/api/library/play`, `/play/status`, `/play/cancel`, `/stream`, `/subtitles` | `src/aniworld/web/views/api_library.py` |
| Watch player, track pickers, cancel button, retry-on-error | `static/library-watch.js`, `templates/library_watch.html` |

Pipeline: probe → direct (browser-safe MP4/WebM) / remux (H.264 copied) / transcode (H.264+AAC) → fragmented MP4 served while ffmpeg writes → moov duration patched in place. Text subtitle tracks are extracted to sidecar WebVTT on demand. Encoder auto-detection prefers NVENC/QSV/AMF/VideoToolbox and falls back to libx264.

While a job is still writing, `/api/library/stream` returns a chunked response without `Content-Length` and keeps following the growing file; placeholder `global_sidx` boxes are hidden from the virtual view (they would make Chromium jump to the end). Completed files keep their sidx and use normal byte ranges, and the watch page reattaches when the job becomes ready so seeking is restored.

Reliability: each ffmpeg `Popen` is tracked per job, `POST /api/library/play/cancel` stops it (SIGTERM, then kill after 5 s), and a 30 s watchdog cancels jobs that report no progress for 30 min. Cancelling removes the partial cache and sidecars. `start_web_ui` registers `atexit`/SIGTERM hooks so a server exit stops active ffmpeg children instead of leaving them running.

## 3. Web UI

| Concern | Location |
|---|---|
| App factory, auth, CSRF, security headers, `start_web_ui` | `src/aniworld/web/app.py` |
| Blueprint registration (pages vs `api.` prefix) | `src/aniworld/web/views/__init__.py` |
| Page routes (index/queue/library/settings/login) | `src/aniworld/web/views/pages.py` |
| JSON endpoints | `views/api_media.py` (search/title/episodes, ~30KB — the big one), `api_queue.py`, `api_settings.py`, `api_library.py`, `api_autosync.py`, `api_keys.py` |
| Templates (Jinja) | `web/templates/*.html` (`index.html` home, `settings.html` 45KB) |
| Frontend JS (vanilla, no build step) | `web/static/*.js` (`home.js` main UX, `queue.js`, `settings.js`, `i18n.js`) |
| Styles | `web/static/style.css` (61KB), themes in `/themes` + custom CSS via settings |
| Settings persistence | `web/settings_store.py`, `.env` at `~/.aniworld/.env` |

No bundler — edit JS/CSS directly, hard-refresh browser.

## 4. Providers / sites

| Concern | Location |
|---|---|
| `Provider` dataclass + `PROVIDERS` registry + `resolve_provider(url)` | `src/aniworld/providers.py` |
| URL patterns per site | `src/aniworld/config.py` (`ANIWORLD_*_PATTERN`, etc.) |
| Site models (Series/Season/Episode) | `src/aniworld/models/<site>/` (e.g. `aniworld_to/`, `s_to/`, `mangafire_to/`) |
| Shared download/HLS/HTTP helpers | `src/aniworld/models/common/{common,hls,http,extraction,provider_map}.py` |
| Hoster extractors (VOE, Filemoon, …) | `src/aniworld/extractors/provider/*.py` |
| Search across sites | `src/aniworld/search.py` (2092 lines), `common/search.py` |
| Web search/title endpoints | `web/views/api_media.py`, `web/sitesearch.py` |
| Exports | `src/aniworld/models/__init__.py` |

**To add a site:** config pattern → `models/<site>/` package → export in `models/__init__` → register `Provider(...)` in `providers.py` → wire search in `search.py` → optional extractor in `extractors/provider/` → tests under `tests/`.

## Test layout

`tests/test_*.py` (pytest, offline). Manual live-provider scripts excluded via `tests/conftest.py`.
