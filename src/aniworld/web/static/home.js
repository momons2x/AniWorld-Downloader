/* Home page: site switch, search, browse rows and the download modal. */

(function () {
  const el = (id) => document.getElementById(id);

  const searchInput = el("searchInput");
  const searchBtn = el("searchBtn");
  const randomBtn = el("randomBtn");
  const searchSpinner = el("searchSpinner");
  const resultsGrid = el("results");
  const browse = el("browse");
  const genreBar = el("genreBar");
  const genreList = el("genreList");
  const genreMore = el("genreMore");
  const genreMoreBtn = el("genreMoreBtn");
  const myAnimeMode = el("myAnimeMode");
  const myAnimeModeList = el("myAnimeModeList");
  const discoverControls = el("discoverControls");
  const discoverKindList = el("discoverKindList");
  const discoverGenreBar = el("discoverGenreBar");
  const discoverGenreList = el("discoverGenreList");
  const discoverTagBar = el("discoverTagBar");
  const discoverTagList = el("discoverTagList");
  const discoverMore = el("discoverMore");
  const discoverMoreBtn = el("discoverMoreBtn");
  const discoverOverlay = el("discoverOverlay");
  const discoverBody = el("discoverBody");

  const seriesOverlay = el("seriesOverlay");
  const seriesLoading = el("seriesLoading");
  const seriesContent = el("seriesContent");
  const languageSelect = el("languageSelect");
  const providerSelect = el("providerSelect");
  const customPathRow = el("customPathRow");
  const customPathSelect = el("customPathSelect");
  const autosyncRow = el("autosyncRow");
  const autosyncExclude = el("autosyncExclude");
  const accordion = el("seasonAccordion");
  const episodeSpinner = el("episodeSpinner");
  const selectAll = el("selectAll");
  const downloadSelectedBtn = el("downloadSelectedBtn");
  const downloadAllBtn = el("downloadAllBtn");

  const thumb = el("segmentedThumb");
  const siteButtons = Array.from(document.querySelectorAll(".segmented-btn"));

  // With only one site enabled the tab bar is just decoration, tuck it away
  if (siteButtons.length <= 1) {
    const siteSwitch = el("siteSwitch");
    if (siteSwitch) siteSwitch.hidden = true;
  }

  // Browse rows are cached per process on the server, refresh occasionally here
  const BROWSE_REFRESH_MS = 300000;
  // Provider scrapes get more headroom than the shared apiFetch default.
  const SCRAPE_TIMEOUT_MS = 60000;

  const SEARCH_PLACEHOLDERS = {
    aniworld: "Search AniWorld...",
    nineanime: "Search 9anime...",
    miruro: "Search Miruro...",
    reanime: "Search Re:Anime...",
    local: "Search your anime..."
  };

  const LANGUAGE_BADGES = {
    "English Sub": { flags: ["jp", "gb"], text: "Sub", css: "badge-english-sub" },
    "English Dub": { flags: ["gb"], text: "Dub", css: "badge-english-dub" },
    "Indonesian Sub": { flags: ["jp"], text: "Sub", css: "badge-english-sub" },
    Japanese: { flags: ["jp"], text: "Dub", css: "badge-english-dub" }
  };

  // Any site can be switched off in the settings, so the page opens on the
  // first tab that is actually there rather than on a fixed one
  const firstSite = () => (siteButtons[0] ? siteButtons[0].dataset.site : "aniworld");

  let currentSite = firstSite();
  let downloadedFolders = [];
  let customPaths = [];

  /* ===== My Anime (local library + AniList discovery) ===== */
  const DISCOVER_GENRES = [
    "Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy",
    "Horror", "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological",
    "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"
  ];
  const DISCOVER_TAGS = [
    "Isekai", "Reincarnation", "Detective", "Vampire", "Zombie", "Cyberpunk",
    "Female Protagonist", "Male Protagonist", "Super Power", "Space",
    "Survival", "Dystopian", "Post-Apocalyptic", "Tragedy", "Military",
    "School", "Magic", "Historical", "Educational", "Found Family", "Gore",
    "Boys' Love", "Yuri", "Cute Girls Doing Cute Things", "Otaku Culture",
    "Cosplay", "Idol", "Food", "Travel", "Photography", "School Club",
    "Martial Arts", "Boxing", "Cycling", "Baseball", "Football", "Tennis",
    "Swimming", "Camping", "Fishing", "Astronomy", "Writing", "Fashion",
    "Robots", "Artificial Intelligence", "Gambling", "Shogi", "Poker",
    "Mafia", "Yakuza", "Religion", "Cult"
  ];
  let localLocations = [];
  let localEntries = [];
  let localGenre = "";
  let localLoading = false;
  let localMode = "library";
  const localMetaPending = new Set();

  const discover = { kind: "trending", genre: "", tag: "", page: 1, hasNext: false, loading: false };

  const isLocalSite = () => currentSite === "local";
  const isDiscoverMode = () => currentSite === "local" && localMode === "discover";

  function localLocationQuery(location) {
    if (!location) return "";
    const params = new URLSearchParams();
    if (location.custom_path_id) params.set("path_id", location.custom_path_id);
    if (location.lang_folder) params.set("lang_folder", location.lang_folder);
    return params.toString();
  }

  function localLocationName(location) {
    return location.lang_folder
      ? `${location.label} / ${location.lang_folder}`
      : location.label;
  }

  function localWatchUrl(entry) {
    const params = new URLSearchParams({ folder: entry.folder });
    if (entry.location && entry.location.custom_path_id) {
      params.set("path_id", entry.location.custom_path_id);
    }
    if (entry.location && entry.location.lang_folder) {
      params.set("lang_folder", entry.location.lang_folder);
    }
    return `/library/watch?${params.toString()}`;
  }

  function localAllGenres() {
    const genres = new Set();
    localEntries.forEach((entry) => {
      (entry.genres || []).forEach((genre) => genre && genres.add(genre));
    });
    return Array.from(genres).sort((a, b) => a.localeCompare(b));
  }

  function renderLocalGenres() {
    const genres = localAllGenres();
    genreBar.hidden = !genres.length;
    if (!genres.length) return;
    genreList.innerHTML = genres
      .map(
        (genre) =>
          `<button type="button" class="genre-chip" role="listitem" data-genre="${esc(genre)}">${esc(genre)}</button>`
      )
      .join("");
    genreList.querySelectorAll(".genre-chip").forEach((chip) => {
      chip.classList.toggle("active", chip.dataset.genre === localGenre);
    });
  }

  function localMatches(entry) {
    const query = searchInput.value.trim().toLowerCase();
    const haystack = [
      entry.folder,
      entry.title,
      (entry.genres || []).join(" "),
      localLocationName(entry.location)
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return (!query || haystack.includes(query)) &&
      (!localGenre || (entry.genres || []).includes(localGenre));
  }

  function renderLocalAnime() {
    const filtered = localEntries
      .filter(localMatches)
      .sort((a, b) => a.title.localeCompare(b.title));
    if (!filtered.length) {
      resultsGrid.innerHTML = `<div class="empty-state">${t("index.no_results", "No results found.")}</div>`;
      return;
    }
    resultsGrid.innerHTML = filtered
      .map((entry) => {
        const title = entry.title || entry.folder;
        const subtitle = (entry.genres || []).length
          ? entry.genres.slice(0, 2).join(", ")
          : localLocationName(entry.location);
        const metaLine = [entry.year, entry.score ? `★ ${entry.score}` : ""]
          .filter(Boolean)
          .join(" · ");
        return `
          <div class="poster-card" data-watch-url="${esc(localWatchUrl(entry))}">
            <span class="downloaded-badge">&#10003;</span>
            <img src="${esc(entry.poster_url || "")}" alt="" loading="lazy" onerror="this.style.display='none'" />
            <div class="info">
              <div class="title" title="${esc(title)}">${esc(title)}</div>
              ${subtitle ? `<div class="subtitle">${esc(subtitle)}</div>` : ""}
              ${metaLine ? `<div class="subtitle">${esc(metaLine)}</div>` : ""}
            </div>
          </div>`;
      })
      .join("");
  }

  async function fetchLocalMeta(entry) {
    if (localMetaPending.has(entry.folder)) return;
    if (entry.genres && entry.genres.length && entry.poster_url) return;
    localMetaPending.add(entry.folder);
    try {
      const query = localLocationQuery(entry.location);
      const data = await apiFetch(
        `/api/library/meta?folder=${encodeURIComponent(entry.folder)}${query ? `&${query}` : ""}`
      );
      if (data && (data.title_en || data.title_romaji || data.cover || data.genres?.length)) {
        entry.title = data.title_en || data.title_romaji || entry.title;
        entry.poster_url = entry.poster_url || data.cover || "";
        entry.genres = data.genres || entry.genres || [];
        entry.year = entry.year || data.year || null;
        entry.score = entry.score ?? data.score ?? null;
        if (currentSite === "local") {
          renderLocalGenres();
          renderLocalAnime();
        }
      }
    } catch (e) {
      /* the folder name is enough while AniList is unavailable */
    } finally {
      localMetaPending.delete(entry.folder);
    }
  }

  async function loadLocalAnime() {
    if (localLoading && localEntries.length) return;
    localLoading = true;
    try {
      const locationsData = await apiFetch("/api/library/locations");
      localLocations = locationsData.locations || [];
      const perLocation = await Promise.all(
        localLocations.map(async (location) => {
          try {
            const query = localLocationQuery(location);
            const data = await apiFetch(`/api/library/titles?${query}`);
            return { location, titles: data.titles || [] };
          } catch (e) {
            return { location, titles: [] };
          }
        })
      );
      localEntries = perLocation.flatMap(({ location, titles }) =>
        titles.map((title) => ({ ...title, location }))
      );
      renderLocalGenres();
      renderLocalAnime();
      localEntries.forEach((entry) => fetchLocalMeta(entry));
    } catch (error) {
      resultsGrid.innerHTML = `<div class="empty-state" role="alert">${esc(error.message)}</div>`;
    } finally {
      localLoading = false;
    }
  }

  // Open series state
  let openToken = 0;
  let seriesUrl = "";
  let seriesTitle = "";
  let seasons = [];
  let episodeCache = {};
  let episodeLoads = {};
  let availableProviders = null;

  function moveThumb() {
    const active = siteButtons.find((btn) => btn.classList.contains("active"));
    if (!active || !thumb) return;
    thumb.style.width = `${active.offsetWidth}px`;
    // offsetLeft already starts inside the track padding, same origin as the
    // thumb, so it goes straight in without correcting for the padding
    thumb.style.transform = `translateX(${active.offsetLeft}px)`;
  }

  function switchSite(site) {
    currentSite = site;
    document.body.dataset.site = site;
    siteButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.site === site));
    moveThumb();
    searchInput.placeholder = SEARCH_PLACEHOLDERS[site] || "Search...";
    randomBtn.hidden = site !== "aniworld";
    resultsGrid.innerHTML = "";
    searchInput.value = "";
    resetGenre();
    if (site === "local") {
      localGenre = "";
      browse.hidden = true;
      genreMore.hidden = true;
      if (myAnimeMode) myAnimeMode.hidden = false;
      syncLocalMode();
      return;
    }
    if (myAnimeMode) myAnimeMode.hidden = true;
    discoverControls.hidden = true;
    updateGenreBar();
    showBrowseRows();
  }

  siteButtons.forEach((btn) => {
    btn.addEventListener("click", () => switchSite(btn.dataset.site));
  });
  window.addEventListener("resize", moveThumb);
  // The buttons are measured before Inter is done loading, so they grow a
  // couple of pixels afterwards and the thumb would stay at the old width
  if (document.fonts) document.fonts.ready.then(moveThumb);

  /* ===== Browse rows ===== */
  const browseState = {};

  function visibleSections() {
    return Array.from(browse.querySelectorAll(".browse-section")).filter((section) =>
      section.dataset.sites.split(",").includes(currentSite)
    );
  }

  function showBrowseRows() {
    browse.hidden = false;
    browse.querySelectorAll(".browse-section").forEach((section) => {
      section.hidden = !section.dataset.sites.split(",").includes(currentSite);
    });
    visibleSections().forEach((section) => loadRow(section));
  }

  async function loadRow(section, force) {
    const key = section.dataset.row;
    const state = browseState[key] || (browseState[key] = { loadedAt: 0, pending: null });
    if (state.pending) return state.pending;
    if (!force && Date.now() - state.loadedAt < BROWSE_REFRESH_MS) return;

    const grid = section.querySelector(".browse-grid");
    if (!grid.children.length) {
      grid.innerHTML = `<div class="empty-state">${t("common.loading", "Loading...")}</div>`;
    }

    state.pending = apiFetch(`/api/${key.replace(/_/g, "-")}`, {
      timeoutMs: SCRAPE_TIMEOUT_MS
    })
      .then((data) => {
        state.loadedAt = Date.now();
        renderCards(grid, data.results || []);
      })
      .catch((error) => {
        grid.innerHTML = `
          <div class="empty-state" role="alert">${esc(error.message)}</div>
          <button type="button" class="btn btn-secondary">${t("common.retry", "Try again")}</button>`;
        grid.querySelector("button").addEventListener("click", () => loadRow(section, true));
      })
      .finally(() => {
        state.pending = null;
      });
    return state.pending;
  }

  /* This has to stay the same rule as folder_matches_title on the server, or
     the badge on a card and the ticks inside it end up disagreeing. */
  const DECORATION_OPENERS = "([{";
  const DECORATION_SEPARATORS = "-\u2013\u2014_~.";

  function normalizeTitle(value) {
    return decodeEntities(value || "")
      .replace(/[\u2018\u2019]/g, "'")
      .replace(/[\u201C\u201D]/g, '"')
      // the downloader strips these from folder names
      .replace(/[<>:"/\\|?*]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function isDecoration(rest) {
    if (!rest) return true;
    if (DECORATION_OPENERS.includes(rest[0])) return true;
    if (DECORATION_SEPARATORS.includes(rest[0])) {
      const tail = rest.slice(1).trimStart();
      return Boolean(tail) && (/[0-9]/.test(tail[0]) || DECORATION_OPENERS.includes(tail[0]));
    }
    return false;
  }

  function isDownloaded(title) {
    const normalized = normalizeTitle(title);
    if (!normalized) return false;
    return downloadedFolders.some((folder) => {
      const name = normalizeTitle(folder);
      if (!name.startsWith(normalized)) return false;
      return isDecoration(name.slice(normalized.length).trimStart());
    });
  }

  function renderCards(grid, items) {
    if (!items.length) {
      grid.innerHTML = `<div class="empty-state">${t("index.no_results", "No results found.")}</div>`;
      return;
    }

    grid.innerHTML = items
      .map((item) => {
        const title = decodeEntities(item.title);
        const badge = isDownloaded(title)
          ? '<span class="downloaded-badge">&#10003;</span>'
          : "";
        const subtitle = item.genre
          ? `<div class="subtitle">${esc(item.genre)}</div>`
          : "";
        return `
          <div class="poster-card" data-url="${esc(item.url)}">
            ${badge}
            <img src="${esc(item.poster_url || "")}" alt="" loading="lazy" />
            <div class="info">
              <div class="title" title="${esc(title)}">${esc(title)}</div>
              ${subtitle}
            </div>
          </div>`;
      })
      .join("");
  }

  document.addEventListener("click", (event) => {
    const discoverCard = event.target.closest("[data-anilist]");
    if (discoverCard && isDiscoverMode()) {
      openDiscoverDetails(Number(discoverCard.dataset.anilist));
      return;
    }
    const card = event.target.closest(".poster-card");
    if (card && card.dataset.watchUrl) {
      window.location.href = card.dataset.watchUrl;
      return;
    }
    if (card && card.dataset.url) openSeries(card.dataset.url);
  });

  /* ===== Genres =====
     Every site that has genre pages of its own gets the chip bar, GENRE_SITES
     says which those are. Picking a genre takes over the results grid so the
     chips stay reachable and you can hop straight to the next one. */
  const genreChips = {};
  let activeGenre = null;
  let activeGenreSite = null;
  let genrePage = 1;
  let genreItems = [];
  let genreLoading = false;

  function updateGenreBar() {
    genreList.innerHTML = "";
    if (isLocalSite()) {
      if (isDiscoverMode()) {
        discoverGenreBar.hidden = true;
        renderDiscoverGenres();
      } else {
        discoverGenreBar.hidden = true;
        renderLocalGenres();
      }
      return;
    }
    genreBar.hidden = !(window.GENRE_SITES || []).includes(currentSite);
    if (!genreBar.hidden) loadGenres(currentSite);
  }

  async function loadGenres(site) {
    if (genreChips[site]) {
      genreList.innerHTML = genreChips[site];
      return;
    }

    genreList.innerHTML =
      `<span class="genre-hint">${t("common.loading", "Loading...")}</span>`;
    try {
      const data = await apiFetch(`/api/genres?site=${encodeURIComponent(site)}`);
      genreChips[site] = (data.genres || [])
        .map(
          (genre) =>
            `<button type="button" class="genre-chip" role="listitem"
               data-slug="${esc(genre.slug)}">${esc(genre.name)}</button>`
        )
        .join("");
    } catch (error) {
      // the bar is a shortcut, not the only way in, so it steps aside quietly
      if (site === currentSite) {
        genreList.innerHTML = "";
        genreBar.hidden = true;
      }
      return;
    }
    // a slow list can land after the user already moved on to another site
    if (site === currentSite) genreList.innerHTML = genreChips[site];
  }

  function resetGenre() {
    activeGenre = null;
    activeGenreSite = null;
    genreItems = [];
    genreMore.hidden = true;
    genreList
      .querySelectorAll(".genre-chip.active")
      .forEach((chip) => chip.classList.remove("active"));
  }

  // Back to the site's default view: no genre open, chips and browse rows back
  function clearGenre() {
    resetGenre();
    resultsGrid.innerHTML = "";
    updateGenreBar();
    showBrowseRows();
  }

  genreList.addEventListener("click", (event) => {
    const chip = event.target.closest(".genre-chip");
    if (!chip) return;
    if (chip.dataset.kind) {
      discover.kind = chip.dataset.kind;
      discover.page = 1;
      discoverKindList
        .querySelectorAll(".genre-chip")
        .forEach((item) => item.classList.toggle("active", item === chip));
      loadDiscover(true);
      return;
    }
    if (isLocalSite()) {
      if (isDiscoverMode()) {
        discover.genre = discover.genre === chip.dataset.genre ? "" : chip.dataset.genre;
        renderDiscoverGenres();
        loadDiscover(true);
        return;
      }
      localGenre = localGenre === chip.dataset.genre ? "" : chip.dataset.genre;
      renderLocalGenres();
      renderLocalAnime();
      return;
    }
    // clicking the open genre again goes back to the browse rows
    if (chip.dataset.slug === activeGenre) {
      clearGenre();
      return;
    }
    resetGenre();
    activeGenre = chip.dataset.slug;
    activeGenreSite = currentSite;
    chip.classList.add("active");
    searchInput.value = "";
    browse.hidden = true;
    resultsGrid.innerHTML = "";
    loadGenrePage(1);
  });

  genreMoreBtn.addEventListener("click", () => loadGenrePage(genrePage + 1));

  async function loadGenrePage(page) {
    if (genreLoading || !activeGenre) return;
    const slug = activeGenre;
    const site = activeGenreSite;
    genreLoading = true;
    genreMoreBtn.disabled = true;
    if (page === 1) searchSpinner.classList.add("active");

    try {
      const data = await apiFetch(
        `/api/genre?site=${encodeURIComponent(site)}` +
          `&slug=${encodeURIComponent(slug)}&page=${page}`,
        { timeoutMs: SCRAPE_TIMEOUT_MS }
      );
      // a slow page 1 can land after the user already picked another genre
      if (slug !== activeGenre || site !== activeGenreSite) return;
      genrePage = page;
      genreItems = genreItems.concat(data.results || []);
      renderCards(resultsGrid, genreItems);
      loadMissingPosters();
      genreMore.hidden = !data.has_more;
    } catch (error) {
      showToast(`${t("browse.genre_failed", "Could not load genre")}: ${error.message}`);
      if (page === 1) clearGenre();
    } finally {
      genreLoading = false;
      genreMoreBtn.disabled = false;
      searchSpinner.classList.remove("active");
    }
  }

  /* ===== Search ===== */
  async function doSearch() {
    if (isDiscoverMode()) {
      discover.page = 1;
      loadDiscover(true);
      return;
    }
    if (isLocalSite()) {
      renderLocalAnime();
      return;
    }
    const keyword = searchInput.value.trim();
    // submitting an empty box is the way back out of a search
    if (!keyword) {
      clearGenre();
      return;
    }

    resetGenre();
    // the chips discover what a site has, a hit list is not that, so they go
    // with the browse rows and come back with them
    genreBar.hidden = true;
    searchBtn.disabled = true;
    searchSpinner.classList.add("active");
    resultsGrid.innerHTML = "";
    browse.hidden = true;

    try {
    const data = await apiSend("/api/search", "POST", { keyword, site: currentSite }, {
      timeoutMs: SCRAPE_TIMEOUT_MS
    });
      renderCards(resultsGrid, data.results || []);
      loadMissingPosters();
    } catch (error) {
      showToast(`${t("index.search_failed", "Search failed")}: ${error.message}`);
    } finally {
      searchBtn.disabled = false;
      searchSpinner.classList.remove("active");
    }
  }

  // Some sites give no posters, fetch them per card. One request per card is
  // enough of a round trip to keep the answers around: a genre's "Load more"
  // re-renders every card that is already on screen.
  const posterCache = {};

  function loadMissingPosters() {
    resultsGrid.querySelectorAll(".poster-card").forEach(async (card) => {
      const image = card.querySelector("img");
      if (image.getAttribute("src")) return;
      const url = card.dataset.url;
      if (posterCache[url]) {
        image.src = posterCache[url];
        return;
      }
      try {
        const data = await apiFetch(`/api/series?url=${encodeURIComponent(url)}`, {
          timeoutMs: SCRAPE_TIMEOUT_MS
        });
        if (data.poster_url) {
          posterCache[url] = data.poster_url;
          image.src = data.poster_url;
        }
      } catch (e) {
        /* a missing poster is not worth reporting */
      }
    });
  }

  searchBtn.addEventListener("click", doSearch);
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") doSearch();
  });
  searchInput.addEventListener("input", () => {
    if (isDiscoverMode()) {
      discover.page = 1;
      loadDiscover(true);
    } else if (isLocalSite()) {
      renderLocalAnime();
    }
  });

  randomBtn.addEventListener("click", async () => {
    randomBtn.disabled = true;
    try {
      const data = await apiFetch(`/api/random?site=${currentSite}`, {
        timeoutMs: SCRAPE_TIMEOUT_MS
      });
      openSeries(data.url);
    } catch (error) {
      showToast(error.message);
    } finally {
      randomBtn.disabled = false;
    }
  });

  /* ===== Custom paths ===== */
  async function loadCustomPaths() {
    try {
      const data = await apiFetch("/api/custom-paths");
      customPaths = data.paths || [];
    } catch (e) {
      customPaths = [];
    }

    customPathRow.hidden = customPaths.length === 0;
    customPathSelect.innerHTML = `<option value="">${t("index.default", "Default")}</option>`;
    customPaths.forEach((path) => {
      const option = document.createElement("option");
      option.value = String(path.id);
      option.textContent = path.name;
      customPathSelect.appendChild(option);
    });

    // Pre-select a path that was marked as the default for this site
    const preferred = customPaths.find((path) =>
      (path.default_sites || "").split(",").includes(currentSite)
    );
    customPathSelect.value = preferred ? String(preferred.id) : "";
  }

  async function loadDownloadedFolders() {
    try {
      const data = await apiFetch("/api/downloaded-folders");
      downloadedFolders = data.folders || [];
    } catch (e) {
      downloadedFolders = [];
    }
  }

  /* ===== Series modal ===== */
  // Skeleton while loading, real content only once everything is in
  function showSkeleton(loading) {
    seriesLoading.hidden = !loading;
    seriesContent.hidden = loading;
  }

  function resetModal() {
    el("seriesPoster").removeAttribute("src");
    el("seriesTitle").textContent = "";
    el("seriesGenres").textContent = "";
    el("seriesYear").textContent = "";
    el("seriesDesc").textContent = "";
    accordion.innerHTML = "";
    selectAll.checked = false;
    episodeCache = {};
    episodeLoads = {};
    availableProviders = null;
  }

  function rebuildLanguageOptions() {
    const languages = (
      window.SITE_LANGUAGES[currentSite] || window.SITE_LANGUAGES.aniworld
    ).filter((language) => !(window.ENGLISH_SUB_DISABLED && language === "English Sub"));
    languageSelect.innerHTML = languages
      .map((language) => `<option value="${esc(language)}">${esc(language)}</option>`)
      .join("");
    if (languages.includes(window.DEFAULT_LANGUAGE)) {
      languageSelect.value = window.DEFAULT_LANGUAGE;
    }
  }

  function fillProviderSelect(providers) {
    providerSelect.innerHTML = providers
      .map((name) => `<option value="${esc(name)}">${esc(name)}</option>`)
      .join("");
    providerSelect.value = providers.includes("VOE") ? "VOE" : providers[0] || "";
  }

  function updateProviderSelect() {
    const forLanguage = availableProviders && availableProviders[languageSelect.value];
    if (forLanguage && forLanguage.length) {
      fillProviderSelect(forLanguage);
    } else {
      fillProviderSelect(window.STATIC_PROVIDERS);
    }
  }

  // Hide language options the title does not actually offer. The probe only
  // sees the first episode, so this can be switched off in the settings.
  function restrictLanguages() {
    if (!availableProviders || window.SHOW_ALL_LANGUAGES) return;
    const offered = Object.keys(availableProviders);
    if (!offered.length) return;

    const previous = languageSelect.value;
    Array.from(languageSelect.options).forEach((option) => {
      option.hidden = !offered.includes(option.value);
    });

    const visible = Array.from(languageSelect.options).filter((option) => !option.hidden);
    if (visible.length && !visible.some((option) => option.value === previous)) {
      languageSelect.value = visible[0].value;
    }
  }

  async function fetchProviders(episodeUrl) {
    try {
      const data = await apiFetch(`/api/providers?url=${encodeURIComponent(episodeUrl)}`, {
        timeoutMs: SCRAPE_TIMEOUT_MS
      });
      availableProviders = data.providers || null;
      restrictLanguages();
      updateProviderSelect();
    } catch (e) {
      // keep the static provider list when the probe fails
    }
  }

  languageSelect.addEventListener("change", updateProviderSelect);

  async function openSeries(url) {
    const token = ++openToken;
    seriesUrl = url;
    seriesTitle = "";
    resetModal();
    showSkeleton(true);
    openModal("seriesOverlay");

    rebuildLanguageOptions();
    fillProviderSelect(window.STATIC_PROVIDERS);
    loadCustomPaths();
    loadAutosyncExclusion(url);

    try {
      const [series, seasonData] = await Promise.all([
        apiFetch(`/api/series?url=${encodeURIComponent(url)}`, { timeoutMs: SCRAPE_TIMEOUT_MS }),
        apiFetch(`/api/seasons?url=${encodeURIComponent(url)}`, { timeoutMs: SCRAPE_TIMEOUT_MS })
      ]);
      if (token !== openToken) return;

      seriesTitle = series.title || "Unknown";
      el("seriesTitle").innerHTML = `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(seriesTitle)}</a>`;
      if (series.poster_url) el("seriesPoster").src = series.poster_url;
      el("seriesGenres").textContent = (series.genres || []).join(", ");
      el("seriesYear").textContent = series.release_year || "";
      el("seriesDesc").textContent = series.description || "";

      seasons = seasonData.seasons || [];
      // Wait for the first season's episodes so the list is populated on reveal
      const firstEpisodes = await buildAccordion(token);
      if (token !== openToken) return;

      // The provider probe narrows the language/provider selects, so resolve it
      // before revealing or the dropdowns would visibly change afterwards
      if (firstEpisodes.length && !fixed) {
        await fetchProviders(firstEpisodes[0].url);
        if (token !== openToken) return;
      }

      showSkeleton(false);
    } catch (error) {
      if (token !== openToken) return;
      showSkeleton(false);
      // Leaving the modal blank would look like a hang
      el("seriesTitle").textContent = t("index.load_failed", "Failed to load title");
      el("seriesDesc").textContent = error.message;
      accordion.innerHTML = "";
      episodeSpinner.classList.remove("active");
      showToast(`${t("index.load_failed", "Failed to load title")}: ${error.message}`);
    }
  }

  // Only meaningful for aniworld titles, that is all Auto-Sync looks at
  async function loadAutosyncExclusion(url) {
    if (!autosyncRow) return;
    autosyncRow.hidden = true;
    if (!window.AUTOSYNC_ENABLED || !url.includes("aniworld.to/")) return;
    try {
      const data = await apiFetch(
        `/api/autosync/excluded?url=${encodeURIComponent(url)}`
      );
      autosyncExclude.checked = Boolean(data.excluded);
      autosyncRow.hidden = false;
    } catch (e) {
      autosyncRow.hidden = true;
    }
  }

  if (autosyncExclude) {
    autosyncExclude.addEventListener("change", async () => {
      try {
        await apiSend("/api/autosync/excluded", "POST", {
          series_url: seriesUrl,
          title: seriesTitle,
          excluded: autosyncExclude.checked
        });
        showToast(
          autosyncExclude.checked
            ? t("index.autosync_excluded", "Excluded from Auto-Sync")
            : t("index.autosync_included", "Included in Auto-Sync again")
        );
      } catch (error) {
        showToast(error.message);
        autosyncExclude.checked = !autosyncExclude.checked;
      }
    });
  }

  function seasonLabel(season, count) {
    const shown = typeof count === "number" ? count : season.episode_count;
    const name = season.are_movies
      ? t("index.movies", "Movies")
      : `${t("index.season", "Season")} ${season.season_number}`;
    return typeof shown === "number" ? `${name} (${shown})` : name;
  }

  // Returns the first season's episodes so the caller can wait for them
  async function buildAccordion(token) {
    accordion.innerHTML = "";
    if (!seasons.length) {
      accordion.innerHTML = `<div class="accordion-message">${t("index.no_episodes", "No episodes available.")}</div>`;
      return [];
    }

    seasons.forEach((season, index) => {
      const section = document.createElement("div");
      section.className = "season-section";
      section.dataset.index = String(index);
      section.innerHTML = `
        <div class="season-header${index === 0 ? " expanded" : ""}">
          <span class="season-label"><span class="arrow">&#9654;</span> ${esc(seasonLabel(season))}</span>
          <label class="checkbox" style="margin:0">
            <input type="checkbox" data-season-all="${index}" />
            <span>${t("index.all", "All")}</span>
          </label>
        </div>
        <div class="season-body${index === 0 ? " expanded" : ""}" id="seasonBody-${index}">
          <div class="accordion-message">${t("index.loading_episodes", "Loading episodes...")}</div>
        </div>`;
      accordion.appendChild(section);
    });

    return loadEpisodes(0, token);
  }

  function episodesUrl(season) {
    let url = `/api/episodes?url=${encodeURIComponent(season.url || seriesUrl)}`;
    if (!season.url) {
      url += `&series_url=${encodeURIComponent(seriesUrl)}`;
    }
    return url;
  }

  function loadEpisodes(index, token = openToken) {
    if (episodeCache[index]) return Promise.resolve(episodeCache[index]);
    if (episodeLoads[index]) return episodeLoads[index];

    const season = seasons[index];
    const body = el(`seasonBody-${index}`);
    if (!season || !body) return Promise.resolve([]);

    episodeLoads[index] = apiFetch(episodesUrl(season), { timeoutMs: SCRAPE_TIMEOUT_MS })
      .then((data) => {
        if (token !== openToken) return [];
        const episodes = data.episodes || [];
        episodeCache[index] = episodes;
        renderEpisodes(index, episodes);
        return episodes;
      })
      .catch(() => {
        if (token === openToken) {
          body.innerHTML = `<div class="accordion-message">${t("index.episodes_failed", "Failed to load episodes.")}</div>`;
        }
        return [];
      })
      .finally(() => {
        delete episodeLoads[index];
      });

    return episodeLoads[index];
  }

  function renderBadges(labels) {
    if (!labels || !labels.length) return "";
    const badges = labels
      .map((label) => {
        const badge = LANGUAGE_BADGES[label];
        if (!badge) {
          return `<span class="lang-badge">${esc(label)}</span>`;
        }
        const flags = badge.flags
          .map((flag) => `<img src="/static/flags/${flag}.svg" alt="" aria-hidden="true" />`)
          .join("");
        return `<span class="lang-badge ${badge.css}" title="${esc(label)}">${flags}${badge.text}</span>`;
      })
      .join("");
    return `<span class="lang-badges">${badges}</span>`;
  }

  function renderEpisodes(index, episodes) {
    const body = el(`seasonBody-${index}`);
    const section = accordion.querySelector(`[data-index="${index}"]`);
    if (!body || !section) return;

    if (!episodes.length) {
      body.innerHTML = `<div class="accordion-message">${t("index.no_episodes", "No episodes available.")}</div>`;
      return;
    }

    body.innerHTML = episodes
      .map((episode) => {
        const name = episode.title_en;
        const title = name
          ? `<span class="ep-title">${esc(name)}</span>`
          : `<span class="ep-title ep-title-missing">${t("index.not_available", "[Not available]")}</span>`;
        const done = episode.downloaded ? '<span class="ep-done">&#10003;</span>' : "";
        const value = episode.url;

        return `
          <div class="episode-item">
            <input type="checkbox" value="${esc(value)}" data-season="${index}" />
            <span class="ep-num">E${episode.episode_number}</span>
            ${done}
            <div class="ep-main">${title}${renderBadges(episode.available_languages)}</div>
          </div>`;
      })
      .join("");

    // Refresh the header now that the real episode count is known
    const label = section.querySelector(".season-label");
    const allDone = episodes.every((episode) => episode.downloaded);
    label.innerHTML =
      `<span class="arrow">&#9654;</span> ${esc(seasonLabel(seasons[index], episodes.length))}` +
      (allDone ? ' <span class="ep-done">&#10003;</span>' : "");
  }

  accordion.addEventListener("click", async (event) => {
    if (event.target.closest(".checkbox")) return;
    const header = event.target.closest(".season-header");
    if (!header) return;

    const section = header.closest(".season-section");
    const index = Number(section.dataset.index);
    const body = section.querySelector(".season-body");
    if (!body.classList.contains("expanded") && !episodeCache[index]) {
      await loadEpisodes(index);
    }
    header.classList.toggle("expanded");
    body.classList.toggle("expanded");
  });

  accordion.addEventListener("change", async (event) => {
    const seasonAll = event.target.dataset.seasonAll;
    if (seasonAll !== undefined) {
      const index = Number(seasonAll);
      if (!episodeCache[index]) await loadEpisodes(index);
      el(`seasonBody-${index}`)
        .querySelectorAll("input[type=checkbox]")
        .forEach((box) => {
          box.checked = event.target.checked;
        });
    }
    syncSelectAll();
  });

  function syncSelectAll() {
    const boxes = accordion.querySelectorAll(".episode-item input[type=checkbox]");
    selectAll.checked = boxes.length > 0 && Array.from(boxes).every((box) => box.checked);
  }

  selectAll.addEventListener("change", async () => {
    if (selectAll.checked) await loadAllSeasons();
    accordion.querySelectorAll("input[type=checkbox]").forEach((box) => {
      box.checked = selectAll.checked;
    });
  });

  async function loadAllSeasons() {
    // Keep enough parallelism for a responsive modal without starting one
    // source-site request per season at the same time as a background download.
    let nextIndex = 0;
    async function worker() {
      while (nextIndex < seasons.length) {
        const index = nextIndex++;
        await loadEpisodes(index);
      }
    }
    const workerCount = Math.min(2, seasons.length);
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
  }

  /* ===== Download ===== */
  function selector(all) {
    return all
      ? ".episode-item input[type=checkbox]"
      : ".episode-item input[type=checkbox]:checked";
  }

  function collectEpisodes(all) {
    const boxes = Array.from(accordion.querySelectorAll(selector(all)));
    return boxes.map((box) => box.value);
  }

  async function startDownload(all) {
    if (all) {
      episodeSpinner.classList.add("active");
      await loadAllSeasons();
      episodeSpinner.classList.remove("active");
    }

    const episodes = collectEpisodes(all);
    if (!episodes.length) {
      showToast(
        all
          ? t("index.no_episodes", "No episodes available.")
          : t("index.no_episodes_selected", "No episodes selected.")
      );
      return;
    }

    const body = {
      episodes,
      title: seriesTitle,
      series_url: seriesUrl,
      language: languageSelect.value,
      provider: providerSelect.value
    };
    if (customPathSelect.value) body.custom_path_id = Number(customPathSelect.value);

    downloadAllBtn.disabled = true;
    downloadSelectedBtn.disabled = true;
    try {
      await apiSend("/api/download", "POST", body);
      showToast(t("index.queued", "Added to download queue"));
      if (window.refreshQueue) window.refreshQueue();
    } catch (error) {
      showToast(error.message);
    } finally {
      downloadAllBtn.disabled = false;
      downloadSelectedBtn.disabled = false;
    }
  }

  downloadSelectedBtn.addEventListener("click", () => startDownload(false));
  downloadAllBtn.addEventListener("click", () => startDownload(true));

  seriesOverlay.addEventListener("modal-closed", () => {
    openToken += 1;
  });

  /* ===== Boot ===== */
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) visibleSections().forEach((section) => loadRow(section));
  });

  loadDownloadedFolders().then(() => {
    switchSite(firstSite());
  });

  /* ===== My Anime: Discover (AniList) =====
     The local tab has two modes. "Library" shows your downloads (parsed from
     /api/library/*); "Discover" browses the AniList catalogue and hands the
     download off to the providers you already use. */
  function syncLocalMode() {
    if (myAnimeMode) myAnimeMode.hidden = !isLocalSite();
    myAnimeModeList
      .querySelectorAll("[data-mode]")
      .forEach((btn) => btn.classList.toggle("active", btn.dataset.mode === localMode));
    discoverKindList
      .querySelectorAll("[data-kind]")
      .forEach((btn) => btn.classList.toggle("active", btn.dataset.kind === discover.kind));
    discoverControls.hidden = !(isLocalSite() && localMode === "discover");
    document.body.classList.toggle("discover-mode", isDiscoverMode());
    if (isDiscoverMode()) {
      discoverGenreBar.hidden = !DISCOVER_GENRES.length;
      renderDiscoverGenres();
      renderDiscoverTags();
      loadDiscover(true);
    } else {
      discoverGenreBar.hidden = true;
      discoverTagBar.hidden = true;
      discoverMore.hidden = true;
      loadLocalAnime();
    }
  }

  myAnimeModeList.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-mode]");
    if (!btn || !isLocalSite()) return;
    localMode = btn.dataset.mode;
    syncLocalMode();
  });

  discoverKindList.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-kind]");
    if (!btn || !isDiscoverMode()) return;
    discover.kind = btn.dataset.kind;
    discover.page = 1;
    discoverKindList
      .querySelectorAll("[data-kind]")
      .forEach((item) => item.classList.toggle("active", item === btn));
    loadDiscover(true);
  });

  discoverGenreList.addEventListener("click", (event) => {
    const chip = event.target.closest(".genre-chip");
    if (!chip || !isDiscoverMode()) return;
    discover.genre = discover.genre === chip.dataset.genre ? "" : chip.dataset.genre;
    renderDiscoverGenres();
    discover.page = 1;
    loadDiscover(true);
  });

  discoverTagList.addEventListener("click", (event) => {
    const chip = event.target.closest(".genre-chip");
    if (!chip || !isDiscoverMode()) return;
    discover.tag = discover.tag === chip.dataset.tag ? "" : chip.dataset.tag;
    renderDiscoverTags();
    discover.page = 1;
    loadDiscover(true);
  });

  function renderDiscoverGenres() {
    if (!isDiscoverMode()) return;
    const genres = DISCOVER_GENRES;
    discoverGenreBar.hidden = !genres.length;
    discoverGenreList.innerHTML = genres
      .map(
        (genre) =>
          `<button type="button" class="genre-chip" role="listitem" data-genre="${esc(genre)}">${esc(genre)}</button>`
      )
      .join("");
    discoverGenreList
      .querySelectorAll(".genre-chip")
      .forEach((chip) => chip.classList.toggle("active", chip.dataset.genre === discover.genre));
  }

  function renderDiscoverTags() {
    if (!isDiscoverMode()) return;
    discoverTagBar.hidden = !DISCOVER_TAGS.length;
    discoverTagList.innerHTML = DISCOVER_TAGS.map(
      (tag) =>
        `<button type="button" class="genre-chip" role="listitem" data-tag="${esc(tag)}">${esc(tag)}</button>`
    ).join("");
    discoverTagList
      .querySelectorAll(".genre-chip")
      .forEach((chip) => chip.classList.toggle("active", chip.dataset.tag === discover.tag));
  }

  function discoverCard(item) {
    const title = item.title || item.title_romaji || "";
    const meta = [item.year, item.score ? `★ ${item.score}` : ""]
      .filter(Boolean)
      .join(" · ");
    const subtitle = (item.genres || []).slice(0, 2).join(", ");
    return `
      <div class="poster-card" data-anilist="${item.anilist_id}">
        <img src="${esc(item.cover || "")}" alt="" loading="lazy" onerror="this.style.display='none'" />
        <div class="info">
          <div class="title" title="${esc(title)}">${esc(title)}</div>
          ${subtitle ? `<div class="subtitle">${esc(subtitle)}</div>` : ""}
          ${meta ? `<div class="subtitle">${esc(meta)}</div>` : ""}
        </div>
      </div>`;
  }

  async function loadDiscover(reset) {
    if (discover.loading) return;
    discover.loading = true;
    if (reset) {
      discover.page = 1;
      discover.items = [];
      resultsGrid.innerHTML = `<div class="empty-state">${t("common.loading", "Loading...")}</div>`;
      discoverMore.hidden = true;
    }
    discoverMoreBtn.disabled = true;
    const params = new URLSearchParams({
      kind: discover.kind,
      page: String(discover.page),
    });
    if (discover.genre) params.set("genre", discover.genre);
    if (discover.tag) params.set("tag", discover.tag);
    const query = searchInput.value.trim();
    if (query) params.set("q", query);
    try {
      const data = await apiFetch(`/api/discover?${params.toString()}`);
      const before = discover.items.length;
      discover.items = discover.items.concat(data.results || []);
      discover.hasNext = Boolean(data.has_next);
      discover.page = Number(data.page || discover.page) + (data.has_next ? 1 : 0);
      if (!discover.items.length) {
        resultsGrid.innerHTML = `<div class="empty-state">${t("index.no_results", "No results found.")}</div>`;
      } else if (reset) {
        resultsGrid.innerHTML = `<div class="browse-grid">${discover.items.map(discoverCard).join("")}</div>`;
      } else {
        const fresh = discover.items.slice(before);
        const grid = resultsGrid.querySelector(".browse-grid");
        if (grid) {
          grid.insertAdjacentHTML("beforeend", fresh.map(discoverCard).join(""));
        } else {
          resultsGrid.innerHTML = `<div class="browse-grid">${discover.items.map(discoverCard).join("")}</div>`;
        }
      }
      discoverMore.hidden = !discover.hasNext;
    } catch (error) {
      if (reset) {
        resultsGrid.innerHTML = `<div class="empty-state" role="alert">${esc(error.message)}</div>`;
        discoverMore.hidden = true;
      }
    } finally {
      discover.loading = false;
      discoverMoreBtn.disabled = false;
    }
  }

  discoverMoreBtn.addEventListener("click", () => loadDiscover(false));

  /* ===== Discover details + provider bridge ===== */
  function openDiscoverDetails(anilistId) {
    discoverBody.innerHTML = `<div class="empty-state">${t("common.loading", "Loading...")}</div>`;
    openModal("discoverOverlay");
    apiFetch(`/api/discover/anime/${anilistId}`)
      .then((item) => renderDiscoverDetails(item))
      .catch((error) => {
        discoverBody.textContent = error.message;
      });
  }

  function renderDiscoverDetails(item) {
    const title = item.title || item.title_romaji || "";
    const metaLine = [
      item.year,
      item.format,
      item.status ? String(item.status).toLowerCase() : "",
      item.score ? `★ ${item.score}` : "",
      item.episodes ? `${item.episodes} ${t("library.episodes", "ep")}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    const providers = Array.from(siteButtons)
      .filter((btn) => btn.dataset.site !== "local")
      .map((btn) => btn.dataset.site);
    const locallyDownloaded = isDownloaded(title);
    discoverBody.innerHTML = `
      <div class="series-header">
        <img id="discoverPoster" alt="" ${item.cover ? `src="${esc(item.cover)}"` : ""} />
        <div class="series-meta">
          <h2>${esc(title)}</h2>
          <div class="series-genres">${esc((item.genres || []).join(", "))}</div>
          <div class="series-year">${esc(metaLine)}</div>
          <div class="series-desc">${esc(item.description || "")}</div>
        </div>
      </div>
      <div class="control-row">
        <span class="field-label">${t("index.provider", "Provider")}</span>
        <div class="genre-list">
          ${providers
            .map(
              (site) =>
                `<button type="button" class="genre-chip" data-provider="${esc(site)}">${esc(site)}</button>`
            )
            .join("")}
        </div>
      </div>
      <div class="modal-actions">
        ${locallyDownloaded ? `<button class="btn btn-secondary" data-discover-watch>${t("myanime.watch_library", "Watch in library")}</button>` : ""}
        <button class="btn btn-primary" data-discover-download>${t("index.download_selected", "Download")}</button>
      </div>`;
    const providerBtns = discoverBody.querySelectorAll("[data-provider]");
    let chosen = providerBtns.length ? providerBtns[0].dataset.provider : null;
    providerBtns.forEach((btn, index) => {
      btn.classList.toggle("active", index === 0);
      btn.addEventListener("click", () => {
        providerBtns.forEach((item) => item.classList.remove("active"));
        btn.classList.add("active");
        chosen = btn.dataset.provider;
      });
    });
    const watchBtn = discoverBody.querySelector("[data-discover-watch]");
    if (watchBtn) {
      watchBtn.addEventListener("click", () => {
        window.location.href = `/library?folder=${encodeURIComponent(title)}`;
      });
    }
    discoverBody.querySelector("[data-discover-download]").addEventListener("click", async () => {
      if (!chosen) {
        showToast(t("myanime.pick_provider", "Pick a provider first"));
        return;
      }
      const downloadBtn = discoverBody.querySelector("[data-discover-download]");
      downloadBtn.disabled = true;
      try {
        const data = await apiSend("/api/search", "POST", { keyword: title, site: chosen }, {
          timeoutMs: SCRAPE_TIMEOUT_MS
        });
        const results = data.results || [];
        const target =
          results.find((r) => (r.title || "").toLowerCase() === title.toLowerCase()) ||
          results[0];
        if (!target) throw new Error(t("myanime.no_results", "No results on this site."));
        closeModal("discoverOverlay");
        await openSeries(target.url);
      } catch (error) {
        showToast(`${t("myanime.search_failed", "That provider did not answer")}: ${error.message}`);
      } finally {
        downloadBtn.disabled = false;
      }
    });
  }

  discoverOverlay.addEventListener("click", (event) => {
    if (event.target.closest("[data-discover-close]") || event.target === discoverOverlay) {
      closeModal("discoverOverlay");
    }
  });
})();

