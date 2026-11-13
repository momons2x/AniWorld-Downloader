# AniWorld Downloader v5

![AniWorld Downloader banner](https://github.com/momons2x/AniWorld-Downloader/blob/models/.github/assets/aniworld-banner.png?raw=true)

![GitHub Release](https://img.shields.io/github/v/release/phoenixthrush/AniWorld-Downloader)
[![PyPI Downloads](https://static.pepy.tech/badge/aniworld)](https://pepy.tech/projects/aniworld)
![GitHub License](https://img.shields.io/github/license/phoenixthrush/AniWorld-Downloader)
![Docker Image Size](https://ghcr-badge.egpl.dev/phoenixthrush/aniworld-downloader/size)](https://github.com/phoenixthrush/AniWorld-Downloader/pkgs/container/aniworld-downloader)
![GitHub Issues or Pull Requests](https://img.shields.io/github/issues/momons2x/AniWorld-Downloader)
[![Discord](https://img.shields.io/badge/Discord-Join%20Server-5865F2?logo=discord&logoColor=white)](https://discord.gg/BfDvrKd8V5)
[![GitHub Sponsors](https://img.shields.io/badge/♥%20Sponsor-Visit-red)](https://github.com/sponsors/phoenixthrush)
![GitHub Repo stars](https://img.shields.io/github/stars/momons2x/AniWorld-Downloader)
![GitHub forks](https://img.shields.io/github/forks/momons2x/AniWorld-Downloader)

Find, download, and watch anime, movies, and series. AniWorld Downloader brings supported sites together in a browser-based Web UI, with an interactive terminal menu and a CLI for scripts and automation.

It runs on **Windows, macOS, and Linux**, with **Docker** and standalone builds available too. Free and open source, with no ads, tracking, or paid-only features.

[Getting started](#quick-start) · [Supported sites](#supported-sites) · [Docker](#docker) · [Documentation](https://www.phoenixthrush.com/AniWorld-Downloader-Docs/) · [Python examples](examples)

![Interactive terminal menu](https://github.com/momons2x/AniWorld-Downloader/blob/models/.github/assets/demo.png?raw=true)

https://github.com/user-attachments/assets/d65c4a5c-827a-45d7-a904-78977fd9aef4

## Quick Start

With **Python 3.11 or newer** installed:

```bash
python -m pip install -U aniworld
aniworld -w
```

Open [localhost:8080](http://localhost:8080) to use the Web UI. To start the interactive terminal menu instead:

```bash
aniworld
```

To install the latest development version from the `models` branch:

```bash
pip install --upgrade git+https://github.com/momons2x/AniWorld-Downloader.git@models
```

Video downloads need **FFmpeg**. Watching uses **mpv**, or **IINA** on macOS. Synchronized playback requires **Syncplay and mpv**. The app can offer portable FFmpeg, mpv, and Syncplay installation on Windows, and player installation through supported package managers on macOS/Linux. Install FFmpeg yourself on macOS/Linux; unattended deployments should provide the required tools in advance.

Prefer another installation method? Use [Docker](#docker) or download a standalone build from [GitHub Releases](https://github.com/phoenixthrush/AniWorld-Downloader/releases). See the [documentation](https://www.phoenixthrush.com/AniWorld-Downloader-Docs/) for platform-specific setup and troubleshooting.

### Run from a checkout (Windows)

If you cloned this repository, two scripts do the setup and the launch:

| Script | What it does |
| --- | --- |
| `install.bat` | Creates `.venv` and installs the package with its extras. Run once. |
| `start.bat` | Starts the Web UI and opens your browser at [localhost:8080](http://localhost:8080). Close the window to stop it. |

Double-click `install.bat`, then double-click `start.bat`. Both keep their window open on failure so you can read the error.

`start.bat` forwards extra arguments, so `start.bat --web-port 8081` or `start.bat --no-browser` work as-is. Without the scripts the equivalent is:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[all,test]"
.venv\Scripts\aniworld.exe --web-ui
```

## Features

- Download individual episodes, whole seasons or series, and movies.
- Search supported sites, track your download queue, and browse your library in the Web UI.
- Choose available audio and subtitle languages, with fallback to other stream hosters.
- Customize supported naming templates and output formats, including MKV/MP4 for video; naming support varies by backend.
- Watch through external players, with AniSkip and Anime4K support where applicable.

The Web UI also supports local accounts, optional OIDC SSO, custom CSS, and background shaders. SSO alongside local login requires both `--web-auth` and `--web-sso`; `--web-force-sso` enables SSO-only authentication. A JSON API and optional Discord request bot let you connect it to other tools. Features and language availability vary by site.

For CAPTCHA browser visibility, manual solving, timeouts, and debug logs, see [CAPTCHA configuration](https://www.phoenixthrush.com/AniWorld-Downloader-Docs/configuration#captcha-solving).

## Supported Sites

These three sites have working download providers, and each one gets its own tab on the home page with browse rows (newest, popular, trending) read straight from that site. A legacy **AniWorld** tab is still rendered, but its titles no longer resolve to a provider.

| Site | Base URL | Content | Languages |
| --- | --- | --- | --- |
| 9anime | `https://9anime.org.lv` | Anime and anime movies | English Sub, English Dub |
| Miruro | `https://miruro.tv` | Anime and anime movies | English Sub, English Dub |
| Re:Anime | `https://reanime.to` | Anime and anime movies | English Sub, English Dub |

Turn a site off in **Settings → Sites** to remove its tab and rows. See the [supported sites documentation](https://www.phoenixthrush.com/AniWorld-Downloader-Docs/supported-sites) for live-check details and IP/VPN differences.

### Stream Hosters

Sites list the titles; stream hosters provide the video links.

`VOE` · `Vidmoly` · `Vidoza` · `Doodstream` · `Filemoon` · `Gupload` · `MegaPlay` · `Vidara` · `MiruroHop` · `MiruroBee` · `MiruroSun` · `MiruroBun` · `ReAnimeHD1` · `ReAnimeHD2`

Miruro and Re:Anime also ship their own hosters (`Miruro*`, `ReAnime*`), which are what those two sites list first.

If a hoster fails, the downloader can try others in your configured fallback order, provided the title offers them in the selected language. It does not automatically switch languages. See [live provider checks](https://www.phoenixthrush.com/AniWorld-Downloader-Docs/contributing#live-provider-checks) to repeat these checks.

## CLI Usage

Replace `SUPPORTED_URL` with an episode, season, series, or movie URL from a supported site:

```bash
aniworld "SUPPORTED_URL"
```

To choose a language and provider without opening the menu:

```bash
aniworld --no-menu --language "English Sub" --provider MiruroHop "https://miruro.tv/watch/P6joTcOcZ5MAg745EfyST5b3IZR8Vs9w/even-the-student-council-has-its-holes/episode/1"
```

For all options, more examples, or your installed version:

```bash
aniworld --help
aniworld --examples
aniworld --version
```

## Docker

Save [`docker-compose.yaml`](docker-compose.yaml) in a folder on your machine. From that folder, create the download directory and start the app:

```bash
mkdir -p Downloads
docker compose up -d
```

Open [localhost:8080](http://localhost:8080) when the container is ready. The image includes FFmpeg, Chromium for captcha handling, and the EPUB extra. Chromium setup runs only when an operation needs a browser and reuses an existing installation.

> **Note:** `docker-compose.yaml` still pulls `ghcr.io/phoenixthrush/aniworld-downloader:latest` from the upstream project, so a container does **not** contain the changes in this fork. Point `image:` at your own build if you need them.

The terminal menu in the Docker image offers only Download and uses your configured download folder.

- Downloads are saved to `./Downloads` on your machine.
- The `aniworld-data` volume keeps the database, `.env`, and custom themes across container recreation.
- Use `docker compose logs -f` to view logs and `docker compose down` to stop the app.

To update the image and restart:

```bash
docker compose pull
docker compose up -d
```

The Compose file includes configuration examples. Set persistent options in its `environment:` block, or load a host `.env` file using `env_file`. Settings marked *resets after restart* in the Web UI only affect the running process.

## Configuration and Integrations

Configuration lives in `~/.aniworld/.env` by default. Set `ANIWORLD_INSTALL_FOLDER` to change the app data directory. See [`src/aniworld/.env.example`](src/aniworld/.env.example) for available settings and defaults.

Many settings can be changed in the Web UI. Those marked *resets after restart* must also be set in your `.env` or deployment environment to persist. Saved themes, database records, and Discord bot settings are stored separately and survive restarts when the app data is retained.

**Optional features:** the standard installation includes the Web UI and terminal dependencies. Install an extra for EPUB output, OIDC login, or Discord requests:

```bash
python -m pip install "aniworld[epub]"
python -m pip install "aniworld[sso]"
python -m pip install "aniworld[discord]"
```

Use `"aniworld[all]"` to install all three extras. Docker builds include them; the standalone build workflow includes the EPUB extra.

**API:** search, manage the queue, and access library and settings operations through JSON endpoints. Create a key in **Settings → API Keys**, then send it with your requests:

```bash
curl -H "X-API-Key: YOUR_API_KEY" http://localhost:8080/api/queue
```

Keys have read, read-and-download, or full-access permissions. They cannot manage other API keys. The settings page includes an endpoint reference with examples.

**Appearance:** use **Settings → Appearance** for custom CSS and background shaders. The [`themes/`](themes) directory contains a light theme and a documented CSS template. For community themes and installation instructions, see [aniworld-themes](https://github.com/SiroxCW/aniworld-themes) by SiroxCW. To recover from a theme that hides controls, open `/settings?nocss=1`.

**Python:** all public site models are available from `aniworld` or `aniworld.models`. Site search lives in `aniworld.web.sitesearch`. The [`examples/`](examples) directory demonstrates models, metadata, and downloads.

## Contributing

Bug reports, fixes, provider updates, and documentation improvements are welcome. Please check existing [issues](https://github.com/momons2x/AniWorld-Downloader/issues) first. For a bug report, include your operating system, installation method, app and Python versions, the command or steps you used, and relevant logs.

To work on the current development branch:

```bash
git clone --branch models https://github.com/momons2x/AniWorld-Downloader.git
cd AniWorld-Downloader
python -m pip install -e ".[all,test]"
pytest
```

Automated tests run offline. See the [testing guide](https://www.phoenixthrush.com/AniWorld-Downloader-Docs/contributing) for automated tests and live provider checks. CI also runs Ruff checks. Keep pull requests focused and explain what changed and how you tested it.

### Contributors

Thank you to everyone who reports issues, shares ideas, and contributes code.

<a href="https://github.com/momons2x/AniWorld-Downloader/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=momons2x/AniWorld-Downloader" alt="AniWorld Downloader contributors" />
</a>

### Credits

AniWorld Downloader uses [mpv](https://github.com/mpv-player/mpv), [IINA](https://github.com/iina/iina), [Syncplay](https://github.com/Syncplay/syncplay), [Anime4K](https://github.com/bloc97/Anime4K), [AniSkip](https://api.aniskip.com/api-docs), [flag-icons](https://github.com/lipis/flag-icons), [new-domain-check](https://github.com/Yezun-hikari/new-domain-check), and [fake-useragent](https://github.com/fake-useragent/fake-useragent).

### Other Cool Projects

- [Jellyfin-AniWorld-Downloader](https://github.com/SiroxCW/Jellyfin-AniWorld-Downloader) by SiroxCW — browse and download AniWorld content from Jellyfin.
- [aniworld-themes](https://github.com/SiroxCW/aniworld-themes) by SiroxCW — community themes and background shaders for the Web UI.
- [AniSeerr](https://github.com/Yezun-hikari/AniSeerr) by Yezun-hikari — connect Seerr requests to AniWorld Downloader.
- [AniBridge](https://github.com/Zzackllack/AniBridge) by Zzackllack — connect supported catalogues to automation tools through FastAPI.
- [AniLoader](https://github.com/WimWamWom/AniLoader) by WimWamWom — a web-based download manager with automation.

## Support and Community

For help, check the [documentation](https://www.phoenixthrush.com/AniWorld-Downloader-Docs/), open a [GitHub issue](https://github.com/momons2x/AniWorld-Downloader/issues), or join us on [Discord](https://discord.gg/BfDvrKd8V5). You can also reach me at [contact@phoenixthrush.com](mailto:contact@phoenixthrush.com).

If the project has been useful, a star, a contribution, or a [donation](https://github.com/sponsors/phoenixthrush) is always appreciated. Thanks for being part of it. <3

## Legal Disclaimer

AniWorld Downloader is a client-side tool that saves downloads to locations you choose. It does not operate a media-hosting service for third-party sites.

You are responsible for how you use it and for following the laws and terms that apply where you live. The project is provided "as is". Its maintainers are not responsible for third-party content, external links, or the availability, accuracy, legality, or reliability of outside services.

Questions about content hosted by another service should be directed to that service.

## Star History

<a href="https://www.star-history.com/?type=date&repos=momons2x%2FAniWorld-Downloader">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=momons2x/AniWorld-Downloader&type=date&theme=dark&legend=top-left&sealed_token=2w3mvLwCvYdC3Bq9vEfw-I3us7ocvtgOppVR5_etK2ZoymoZesVxuElMPDB0v_x46GEhBSkjWsN6bgleOwD5k0xC-LI-o4eh1Cq4iJAIRP-GBwweIiP7UqcOt7Vn9BjC_-Wv0iuJbxmfs8Xn2QAiwgq0TuOu5LLJkkbTleDugs-IwWF7ZYz5hvUPkc6-" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=momons2x/AniWorld-Downloader&type=date&legend=top-left&sealed_token=2w3mvLwCvYdC3Bq9vEfw-I3us7ocvtgOppVR5_etK2ZoymoZesVxuElMPDB0v_x46GEhBSkjWsN6bgleOwD5k0xC-LI-o4eh1Cq4iJAIRP-GBwweIiP7UqcOt7Vn9BjC_-Wv0iuJbxmfs8Xn2QAiwgq0TuOu5LLJkkbTleDugs-IwWF7ZYz5hvUPkc6-" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=momons2x/AniWorld-Downloader&type=date&legend=top-left&sealed_token=2w3mvLwCvYdC3Bq9vEfw-I3us7ocvtgOppVR5_etK2ZoymoZesVxuElMPDB0v_x46GEhBSkjWsN6bgleOwD5k0xC-LI-o4eh1Cq4iJAIRP-GBwweIiP7UqcOt7Vn9BjC_-Wv0iuJbxmfs8Xn2QAiwgq0TuOu5LLJkkbTleDugs-IwWF7ZYz5hvUPkc6-" />
 </picture>
</a>

## License

AniWorld Downloader is available under the [MIT License](LICENSE).
