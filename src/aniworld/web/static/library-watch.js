/* Watch page for one library title.
 *
 * Left: video player. Right: episode list. Query params carry the folder.
 */

(function () {
  const params = new URLSearchParams(window.location.search);
  const folder = params.get("folder") || "";
  const pathId = params.get("path_id") || "";
  const langFolder = params.get("lang_folder") || "";

  const video = document.getElementById("watchPlayer");
  const status = document.getElementById("watchStatus");
  const cancelBtn = document.getElementById("watchCancel");
  const crumb = document.getElementById("watchCrumbTitle");
  const titleEl = document.getElementById("watchTitle");
  const metaEl = document.getElementById("watchMeta");
  const descEl = document.getElementById("watchDesc");
  const hero = document.getElementById("watchHero");
  const cover = document.getElementById("watchCover");
  const playAll = document.getElementById("watchPlayAll");
  const autoNext = document.getElementById("watchAutoNext");
  const deleteTitle = document.getElementById("watchDeleteTitle");
  const seasonSelect = document.getElementById("watchSeasonSelect");
  const searchInput = document.getElementById("watchSearch");
  const countEl = document.getElementById("watchCount");
  const episodesEl = document.getElementById("watchEpisodes");
  const tracksBar = document.getElementById("watchTracks");
  const audioField = document.getElementById("watchAudioField");
  const audioSelect = document.getElementById("watchAudioSelect");
  const subsField = document.getElementById("watchSubsField");
  const subsSelect = document.getElementById("watchSubsSelect");

  let details = null;
  let flatEpisodes = [];
  let currentIndex = -1;
  let activeSeason = null;
  let streamJob = null; // transcode job id while the current episode is still being written
  let streamingCache = false; // current source is the still-growing cache stream
  let lastPayload = null; // last /play response (token, tracks, mode...)
  let pendingResume = null;
  let restoredThisPlay = false;
  let retriedTranscode = false;
  let autoPlayPending = false;
  let pollGeneration = 0;
  let lastProgressSave = 0;
  const prewarmed = new Set();

  crumb.textContent = folder || "Library";
  document.title = folder ? `${folder} - Library` : "Library";

  function locationQuery() {
    const query = new URLSearchParams();
    if (pathId) query.set("path_id", pathId);
    if (langFolder) query.set("lang_folder", langFolder);
    return query.toString();
  }

  function seasonLabel(key) {
    return key === "movie"
      ? t("library.movies", "Movies")
      : `${t("index.season", "Season")} ${key}`;
  }

  function episodeLabel(season, episode) {
    return season === "movie"
      ? t("library.movie", "Movie")
      : `E${String(episode).padStart(3, "0")}`;
  }

  /* Sleep between status polls. While the tab is hidden the interval stretches
     to 5s so a backgrounded player does not run a request loop, and the next
     poll fires the moment the tab becomes visible again. */
  function wait(ms) {
    return new Promise((resolve) => {
      if (!document.hidden) {
        setTimeout(resolve, ms);
        return;
      }
      let timer = null;
      const done = () => {
        if (timer) clearTimeout(timer);
        document.removeEventListener("visibilitychange", onVisible);
        resolve();
      };
      const onVisible = () => {
        if (!document.hidden) done();
      };
      timer = setTimeout(done, Math.max(ms, 5000));
      document.addEventListener("visibilitychange", onVisible);
    });
  }

  /* Error inside the live status region, optionally with a retry action. */
  function showStatus(message, onRetry) {
    status.hidden = false;
    status.textContent = message;
    if (!onRetry) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary watch-retry";
    button.textContent = t("common.retry", "Try again");
    button.addEventListener("click", onRetry);
    status.appendChild(button);
  }

  function prefKey(kind) {
    return `aniworld.watch.${kind}.${folder}`;
  }

  function getPref(kind) {
    try {
      return localStorage.getItem(prefKey(kind));
    } catch (e) {
      return null;
    }
  }

  function setPref(kind, value) {
    try {
      if (value == null || value === "") localStorage.removeItem(prefKey(kind));
      else localStorage.setItem(prefKey(kind), String(value));
    } catch (e) {
      /* storage can be disabled; preferences are optional */
    }
  }

  function preferredAudio() {
    const pref = getPref("audio");
    if (pref == null || pref === "") return undefined;
    const ordinal = Number(pref);
    return Number.isInteger(ordinal) ? ordinal : undefined;
  }

  function statusText(state) {
    if (state.stage === "probing") {
      return t("library.probing", "Checking video...");
    }
    if (state.stage === "finalizing") {
      return t("library.finalizing", "Finishing up...");
    }
    const pct = Math.round(state.progress || 0);
    if (pct <= 0) {
      return t("library.preparing", "Preparing video, this can take a moment...");
    }
    return t("library.preparing_pct", "Preparing video... {pct}%").replace(
      "{pct}",
      String(pct)
    );
  }

  function streamUrl(token) {
    const query = new URLSearchParams();
    if (pathId) query.set("path_id", pathId);
    if (langFolder) query.set("lang_folder", langFolder);
    return `/api/library/stream?token=${encodeURIComponent(token)}${query.toString() ? `&${query.toString()}` : ""}`;
  }

  function clearTextTracks() {
    video.querySelectorAll("track[data-watch-track]").forEach((el) => el.remove());
  }

  function applySubtitleSelection() {
    clearTextTracks();
    if (!lastPayload) return;
    const value = subsSelect.value;
    const track = (lastPayload.subtitle_tracks || []).find(
      (item) => String(item.index) === value
    );
    if (!track) {
      for (const textTrack of video.textTracks) textTrack.mode = "disabled";
      return;
    }
    const element = document.createElement("track");
    element.kind = "subtitles";
    element.label = track.label;
    element.srclang = track.language || "und";
    element.src = track.url;
    element.default = true;
    element.dataset.watchTrack = "1";
    element.addEventListener("load", () => {
      for (const textTrack of video.textTracks) textTrack.mode = "showing";
    });
    video.appendChild(element);
    setTimeout(() => {
      for (const textTrack of video.textTracks) textTrack.mode = "showing";
    }, 0);
  }

  function renderTracks(payload) {
    lastPayload = payload;
    const audioTracks = payload.audio_tracks || [];
    const subtitleTracks = payload.subtitle_tracks || [];

    audioField.hidden = audioTracks.length <= 1;
    audioSelect.innerHTML = audioTracks
      .map(
        (track) =>
          `<option value="${track.ordinal}">${esc(track.label)}${track.channels > 2 ? ` (${track.channels}ch)` : ""}</option>`
      )
      .join("");
    const preferred = preferredAudio();
    const preferredMatch = audioTracks.find(
      (track) => preferred != null && track.ordinal === preferred
    );
    const selected =
      preferredMatch ||
      audioTracks.find((track) => track.ordinal === payload.selected_audio) ||
      audioTracks[0];
    if (selected) audioSelect.value = String(selected.ordinal);

    subsField.hidden = subtitleTracks.length === 0;
    const prefSubs = getPref("subs");
    subsSelect.innerHTML =
      `<option value="">${esc(t("library.subtitles_off", "Off"))}</option>` +
      subtitleTracks
        .map(
          (track) =>
            `<option value="${track.index}">${esc(track.label)}</option>`
        )
        .join("");
    const subMatch =
      subtitleTracks.find((track) => String(track.index) === prefSubs) ||
      subtitleTracks.find((track) => track.default) ||
      null;
    subsSelect.value = subMatch ? String(subMatch.index) : "";

    tracksBar.hidden = audioField.hidden && subsField.hidden;
    applySubtitleSelection();
  }

  async function watchJob(jobId, token, generation) {
    let failures = 0;
    for (;;) {
      await wait(failures ? Math.min(5000, 600 * 2 ** failures) : 600);
      if (generation !== pollGeneration) return;
      let state;
      try {
        state = await apiFetch(
          `/api/library/play/status?job=${encodeURIComponent(jobId)}`
        );
        failures = 0;
      } catch (e) {
        failures += 1;
        if (e.status === 400 || e.status === 404 || failures >= 6) {
          const gone = e.status === 400 || e.status === 404;
          streamJob = null;
          streamingCache = false;
          cancelBtn.hidden = true;
          showStatus(
            e.message || t(
              "library.play_failed",
              "This video could not be played."
            ),
            gone
              ? null
              : () => {
                  // Transient network trouble: the job may still be alive
                  // server-side, so pick the poll back up where it left off.
                  if (generation !== pollGeneration) return;
                  streamJob = jobId;
                  streamingCache = true;
                  cancelBtn.hidden = false;
                  status.hidden = true;
                  watchJob(jobId, token, generation);
                }
          );
          return;
        }
        continue;
      }
      if (generation !== pollGeneration) return;
      if (state.status === "cancelling") {
        status.hidden = false;
        status.textContent = t("library.cancelling", "Cancelling...");
        cancelBtn.hidden = true;
        continue;
      }
      if (state.status === "error" || state.status === "cancelled") {
        streamJob = null;
        streamingCache = false;
        cancelBtn.hidden = true;
        status.hidden = false;
        status.textContent = state.error || t(
          "library.cancelled",
          "Preparation cancelled."
        );
        return;
      }
      if (state.status === "ready") {
        const wasStreaming = streamingCache;
        const wasPaused = video.paused;
        streamJob = null;
        streamingCache = false;
        cancelBtn.hidden = true;
        if (!lastPayload) return;
        const durationUnknown =
          !Number.isFinite(video.duration) || video.duration <= 0;
        if (durationUnknown || video.ended || wasStreaming) {
          const resume = video.currentTime > 0 ? video.currentTime : undefined;
          attachStream(
            { ...lastPayload, token: state.token || token, job: null },
            { resume, autoplay: !wasStreaming || !wasPaused }
          );
        } else {
          tryAutoPlay();
          status.hidden = true;
        }
        return;
      }
      status.hidden = false;
      status.textContent = statusText(state);
    }
  }

  function tryAutoPlay() {
    if (!autoPlayPending) return;
    autoPlayPending = false;
    video.play()
      .then(() => {
        status.hidden = true;
        prewarmNext();
      })
      .catch(() => {
        status.textContent = t("library.press_play", "Press play to start.");
      });
  }

  function attachStream(payload, options) {
    options = options || {};
    lastPayload = payload;
    streamJob = payload.job || null;
    streamingCache = !!payload.job;
    cancelBtn.hidden = !streamJob;
    cancelBtn.disabled = false;
    pendingResume = Number.isFinite(options.resume) ? options.resume : null;
    restoredThisPlay = pendingResume != null;
    autoPlayPending = options.autoplay !== false;
    pollGeneration += 1;
    const generation = pollGeneration;
    video.removeAttribute("src");
    clearTextTracks();
    video.load();
    video.src = streamUrl(payload.token);
    renderTracks(payload);
    if (autoPlayPending) {
      video.play()
        .then(() => {
          autoPlayPending = false;
          status.hidden = true;
          prewarmNext();
        })
        .catch(() => {
          if (video.readyState >= 3) tryAutoPlay();
        });
    }
    if (streamJob) watchJob(streamJob, payload.token, generation);
    const item = flatEpisodes[currentIndex];
    if (item) syncUrl(item.season, item.episode);
  }

  function prewarmNext() {
    const key = `${video.dataset.season || ""}:${video.dataset.episode || ""}`;
    if (prewarmed.has(key)) return;
    prewarmed.add(key);
    const next = flatEpisodes
      .slice(currentIndex + 1)
      .find((item) => item.is_video);
    if (!next) return;
    const audio = preferredAudio();
    apiSend("/api/library/play", "POST", {
      folder,
      season: next.season === "movie" ? next.season : Number(next.season),
      episode: Number(next.episode),
      ...(pathId ? { custom_path_id: pathId } : {}),
      ...(langFolder ? { lang_folder: langFolder } : {}),
      ...(audio != null ? { audio } : {}),
    }).catch(() => {});
  }

  function savedPosition() {
    const season = String(video.dataset.season || "");
    const episode = String(video.dataset.episode || "");
    return details && details.positions && details.positions[season]
      ? details.positions[season][episode]
      : undefined;
  }

  function savePosition(force, keepalive) {
    const now = Date.now();
    if (!force && now - lastProgressSave < 10000) return;
    lastProgressSave = now;
    const season = video.dataset.season;
    const episode = video.dataset.episode;
    if (!season || !episode) return;
    const position = Math.round(video.currentTime || 0);
    apiSend(
      "/api/library/progress",
      "POST",
      {
        folder,
        season: season === "movie" ? season : Number(season),
        episode: Number(episode),
        position,
        ...(pathId ? { custom_path_id: pathId } : {}),
        ...(langFolder ? { lang_folder: langFolder } : {}),
      },
      keepalive ? { keepalive: true } : undefined
    ).catch(() => {});
    if (details && details.positions) {
      details.positions[season] = details.positions[season] || {};
      details.positions[season][episode] = position;
    }
  }

  video.addEventListener("canplay", tryAutoPlay);
  video.addEventListener("playing", () => {
    autoPlayPending = false;
    status.hidden = true;
  });

  video.addEventListener("loadedmetadata", () => {
    if (restoredThisPlay) {
      const pending = pendingResume;
      pendingResume = null;
      if (pending != null && pending > 0) {
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        if (!duration || pending < duration - 1) video.currentTime = pending;
      }
      return;
    }
    restoredThisPlay = true;
    const position = savedPosition();
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (position && position > 10 && (!duration || position < duration - 10)) {
      video.currentTime = position;
    }
  });

  video.addEventListener("timeupdate", () => {
    if (!video.paused && !video.ended) savePosition(false);
  });
  video.addEventListener("pause", () => savePosition(true));
  window.addEventListener("pagehide", () => savePosition(true, true));
  video.addEventListener("ended", () => {
    if (streamJob) return; // the cache is still growing; not a real ending
    cancelBtn.hidden = true;
    savePosition(true);
    const season = video.dataset.season;
    const episode = video.dataset.episode;
    if (details && details.positions && details.positions[season]) {
      delete details.positions[season][episode];
    }
  });

  cancelBtn.addEventListener("click", async () => {
    const jobId = streamJob;
    if (!jobId) return;
    cancelBtn.disabled = true;
    status.hidden = false;
    status.textContent = t("library.cancelling", "Cancelling...");
    try {
      await apiSend("/api/library/play/cancel", "POST", { job: jobId });
      cancelBtn.hidden = true;
    } catch (error) {
      cancelBtn.disabled = false;
      status.hidden = false;
      status.textContent = error.message;
    }
  });

  video.addEventListener("error", async () => {
    if (streamJob) {
      status.hidden = false;
      status.textContent = t(
        "library.preparing",
        "Preparing video, this can take a moment..."
      );
      return; // watchJob reattaches once the file is complete
    }
    if (!lastPayload || retriedTranscode || lastPayload.mode === "transcode") {
      if (lastPayload) {
        status.hidden = false;
        status.textContent = t(
          "library.play_failed",
          "This video could not be played."
        );
      }
      return;
    }
    retriedTranscode = true;
    const item = flatEpisodes[currentIndex];
    if (!item) return;
    const resume = video.currentTime > 0 ? video.currentTime : undefined;
    status.hidden = false;
    status.textContent = t(
      "library.retrying",
      "Playback failed, trying a full transcode..."
    );
    try {
      await play(item.season, item.episode, currentIndex, {
        forceTranscode: true,
        resume,
      });
    } catch (error) {
      status.textContent = error.message;
    }
  });

  async function play(season, episode, index, options) {
    options = options || {};
    const sameEpisode =
      video.dataset.season === String(season) &&
      video.dataset.episode === String(episode);
    if (!sameEpisode) retriedTranscode = false;
    savePosition(true);
    currentIndex = index;
    markActive();

    if (
      sameEpisode &&
      video.src &&
      options.audio == null &&
      !options.forceTranscode
    ) {
      if (video.paused) {
        await video.play().catch(() => {});
      } else {
        video.pause();
      }
      return;
    }

    const audio =
      options.audio != null ? Number(options.audio) : preferredAudio();
    status.hidden = false;
    cancelBtn.hidden = true;
    status.textContent = t(
      "library.preparing",
      "Preparing video, this can take a moment..."
    );
    try {
      const data = await apiSend("/api/library/play", "POST", {
        folder,
        season: season === "movie" ? season : Number(season),
        episode: Number(episode),
        ...(pathId ? { custom_path_id: pathId } : {}),
        ...(langFolder ? { lang_folder: langFolder } : {}),
        ...(audio != null ? { audio } : {}),
        ...(options.forceTranscode ? { force_transcode: true } : {}),
      });
      // Only now does this episode become "the current one": until the server
      // answers, the player still holds the previous video and its progress
      // must keep saving under the previous episode's key.
      video.dataset.season = String(season);
      video.dataset.episode = String(episode);
      attachStream(data, { resume: options.resume });
    } catch (error) {
      showStatus(error.message, () => play(season, episode, index, options));
    }
  }

  function syncUrl(season, episode) {
    const query = new URLSearchParams(window.location.search);
    query.set("season", season);
    query.set("episode", String(episode));
    history.replaceState(null, "", `${window.location.pathname}?${query.toString()}`);
  }

  function markActive() {
    episodesEl.querySelectorAll(".watch-ep").forEach((row) => {
      row.classList.toggle("active", Number(row.dataset.index) === currentIndex);
    });
    const active = episodesEl.querySelector(".watch-ep.active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function renderEpisodes() {
    const term = (searchInput.value || "").trim().toLowerCase();
    let visible = flatEpisodes;
    if (term) {
      visible = flatEpisodes.filter(
        (item) =>
          item.file.toLowerCase().includes(term) ||
          String(item.episode).includes(term) ||
          episodeLabel(item.season, item.episode).toLowerCase().includes(term)
      );
    }
    countEl.textContent = visible.length;

    if (!visible.length) {
      episodesEl.innerHTML = `<div class="empty-state">${esc(t("library.no_episodes", "No episodes found."))}</div>`;
      return;
    }

    let group = "";
    const html = [];
    visible.forEach((item) => {
      const label = seasonLabel(item.season);
      if (label !== group) {
        group = label;
        html.push(`<div class="watch-season-head">${esc(label)}</div>`);
      }
      const index = flatEpisodes.indexOf(item);
      html.push(`
        <div class="watch-ep ${index === currentIndex ? "active" : ""}" data-index="${index}" data-season="${esc(item.season)}" data-episode="${item.episode}">
          <span class="watch-ep-thumb">
            ${item.is_video ? "&#9654;" : ""}
          </span>
          <span class="watch-ep-body">
            <span class="watch-ep-num">${esc(episodeLabel(item.season, item.episode))}</span>
            <span class="watch-ep-file" title="${esc(item.file)}">${esc(item.file)}</span>
            <span class="watch-ep-size">${formatSize(item.size)}</span>
          </span>
          <button class="icon-btn watch-ep-delete" data-delete-episode title="${t("common.delete", "Delete")}">&times;</button>
        </div>`);
    });
    episodesEl.innerHTML = html.join("");
  }

  function buildFlatEpisodes() {
    flatEpisodes = [];
    const keys = Object.keys(details.seasons).sort((a, b) => {
      if (a === "movie") return 1;
      if (b === "movie") return -1;
      return Number(a) - Number(b);
    });
    keys.forEach((key) => {
      (details.seasons[key] || []).forEach((episode) => {
        flatEpisodes.push({ season: key, ...episode });
      });
    });
  }

  function renderSeasons() {
    const keys = Object.keys(details.seasons).sort((a, b) => {
      if (a === "movie") return 1;
      if (b === "movie") return -1;
      return Number(a) - Number(b);
    });
    if (keys.length <= 1) return;
    seasonSelect.hidden = false;
    seasonSelect.innerHTML = keys
      .map((key) => `<option value="${esc(key)}">${esc(seasonLabel(key))}</option>`)
      .join("");
    activeSeason = keys[0];
    seasonSelect.value = keys[0];
  }

  function renderMeta(meta) {
    if (!meta) {
      titleEl.textContent = folder;
      return;
    }
    if (meta.cover) {
      cover.src = meta.cover;
      cover.hidden = false;
      video.poster = meta.cover;
    }
    if (meta.banner) {
      hero.style.backgroundImage = `url('${meta.banner}')`;
    }
    const displayTitle = meta.title_en || meta.title_romaji || folder;
    titleEl.textContent = displayTitle;
    crumb.textContent = displayTitle;
    document.title = `${displayTitle} - Library`;
    const parts = [];
    if (meta.year) parts.push(meta.year);
    if (meta.score) parts.push(`&#9733; ${meta.score}`);
    if (meta.genres && meta.genres.length) parts.push(meta.genres.slice(0, 4).join(", "));
    metaEl.innerHTML = parts.join(" &middot; ");
    descEl.textContent = meta.description || "";
  }

  async function deleteEpisode(row) {
    if (!window.confirm(t("library.confirm_episode", "Really delete this episode?"))) return;
    const season = row.dataset.season;
    const episode = Number(row.dataset.episode);
    try {
      await apiSend("/api/library/delete", "POST", {
        folder,
        season: season === "movie" ? season : Number(season),
        episode,
        ...(pathId ? { custom_path_id: pathId } : {}),
        ...(langFolder ? { lang_folder: langFolder } : {}),
      });
      showToast(t("library.deleted", "Deleted"));
      await loadTitle();
    } catch (error) {
      showToast(error.message);
    }
  }

  async function loadTitle() {
    const query = locationQuery();
    const encoded = encodeURIComponent(folder);
    episodesEl.innerHTML = `<div class="empty-state">${esc(t("common.loading", "Loading..."))}</div>`;
    try {
      details = await apiFetch(
        `/api/library/title?folder=${encoded}${query ? `&${query}` : ""}`
      );
    } catch (error) {
      episodesEl.innerHTML = `<div class="empty-state" role="alert">
        <div>${esc(error.message || t("common.failed", "Failed"))}</div>
        <button type="button" class="btn btn-secondary empty-state-action" data-action="retry-title">
          ${esc(t("common.retry", "Try again"))}
        </button>
      </div>`;
      return false;
    }
    buildFlatEpisodes();
    renderSeasons();
    renderEpisodes();
    return true;
  }

  /* Jump straight to ?season=&episode= when the URL asks for it, but only if
     nothing is already playing (a retry after a failed load must not restart
     the episode the user is watching). */
  async function resumeFromUrl() {
    if (currentIndex >= 0) return;
    const seasonParam = params.get("season");
    const episodeParam = params.get("episode");
    if (!seasonParam || !episodeParam) return;
    const index = flatEpisodes.findIndex(
      (item) => item.season === seasonParam && String(item.episode) === episodeParam
    );
    const item = flatEpisodes[index];
    if (item && item.is_video) await play(item.season, item.episode, index);
  }

  episodesEl.addEventListener("click", async (event) => {
    const retryBtn = event.target.closest('[data-action="retry-title"]');
    if (retryBtn) {
      retryBtn.disabled = true;
      if (await loadTitle()) resumeFromUrl();
      return;
    }
    const deleteBtn = event.target.closest("[data-delete-episode]");
    if (deleteBtn) {
      event.stopPropagation();
      await deleteEpisode(deleteBtn.closest(".watch-ep"));
      return;
    }
    const row = event.target.closest(".watch-ep");
    if (!row) return;
    const index = Number(row.dataset.index);
    const item = flatEpisodes[index];
    if (!item || !item.is_video) return;
    await play(item.season, item.episode, index);
  });

  searchInput.addEventListener("input", renderEpisodes);

  audioSelect.addEventListener("change", () => {
    const value = Number(audioSelect.value);
    if (!Number.isInteger(value)) return;
    setPref("audio", value);
    const item = flatEpisodes[currentIndex];
    if (!item) return;
    const resume = video.currentTime > 0 ? video.currentTime : undefined;
    play(item.season, item.episode, currentIndex, { audio: value, resume });
  });

  subsSelect.addEventListener("change", () => {
    setPref("subs", subsSelect.value);
    applySubtitleSelection();
  });

  seasonSelect.addEventListener("change", () => {
    const chapter = seasonSelect.value;
    activeSeason = chapter;
    const first = flatEpisodes.find((item) => item.season === chapter && item.is_video);
    if (first) play(first.season, first.episode, flatEpisodes.indexOf(first));
  });

  playAll.addEventListener("click", () => {
    const first = flatEpisodes.find((item) => item.is_video);
    if (first) play(first.season, first.episode, flatEpisodes.indexOf(first));
  });

  video.addEventListener("ended", () => {
    if (streamJob || !autoNext.checked) return;
    const next = flatEpisodes
      .slice(currentIndex + 1)
      .find((item) => item.is_video);
    if (next) play(next.season, next.episode, flatEpisodes.indexOf(next));
  });

  deleteTitle.addEventListener("click", async () => {
    const name = folder;
    const message = t("library.confirm_title", 'Really delete all of "{name}"?', { name });
    if (!window.confirm(message)) return;
    try {
      await apiSend("/api/library/delete", "POST", {
        folder,
        ...(pathId ? { custom_path_id: pathId } : {}),
        ...(langFolder ? { lang_folder: langFolder } : {}),
      });
      window.location.href = "/library";
    } catch (error) {
      showToast(error.message);
    }
  });

  async function boot() {
    if (!folder) {
      episodesEl.innerHTML = `<div class="empty-state">${esc(t("library.no_titles", "This folder is empty."))}</div>`;
      return;
    }
    await loadTitle();

    const meta = await apiFetch(
      `/api/library/meta?folder=${encodeURIComponent(folder)}${locationQuery() ? `&${locationQuery()}` : ""}`
    ).catch(() => null);
    renderMeta(meta);

    await resumeFromUrl();
  }

  boot();
})();
