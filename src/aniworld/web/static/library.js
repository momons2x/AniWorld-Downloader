/* Library grid.
 *
 * Every downloaded title as a poster card; the card is a real link to the
 * watch page so keyboard, middle-click and "open in new tab" all work.
 */

(function () {
  const tree = document.getElementById("libraryTree");
  const refreshBtn = document.getElementById("libraryRefresh");
  const showHiddenCheckbox = document.getElementById("libraryShowHidden");

  let locations = [];
  let showHidden = false;
  let loadToken = 0;
  let metaObserver = null;
  const metaCache = {};
  const metaRequests = {};

  function locationQuery(location) {
    if (!location) return "";
    const params = new URLSearchParams();
    if (location.custom_path_id) params.set("path_id", location.custom_path_id);
    if (location.lang_folder) params.set("lang_folder", location.lang_folder);
    return params.toString();
  }

  function locationName(location) {
    return location.lang_folder
      ? `${location.label} / ${location.lang_folder}`
      : location.label;
  }

  /* The same folder name can exist under several locations, so every cache
     key and DOM hook carries the location with it. */
  function metaKey(folder, location) {
    return `${(location && location.custom_path_id) || ""}|${
      (location && location.lang_folder) || ""
    }|${folder}`;
  }

  function watchHref(folder, location) {
    const params = new URLSearchParams();
    params.set("folder", folder);
    if (location && location.custom_path_id) params.set("path_id", location.custom_path_id);
    if (location && location.lang_folder) params.set("lang_folder", location.lang_folder);
    return `/library/watch?${params.toString()}`;
  }

  function message(text) {
    return `<div class="empty-state">${esc(text)}</div>`;
  }

  /* esc() leaves quotes alone, which is fine in text but not inside an
     attribute; titles come from disk and can contain anything. */
  function attr(value) {
    return esc(value == null ? "" : String(value))
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function errorState(text) {
    return `<div class="empty-state" role="alert">
      <div>${esc(text)}</div>
      <button type="button" class="btn btn-secondary empty-state-action" data-action="retry-library">
        ${esc(t("common.retry", "Try again"))}
      </button>
    </div>`;
  }

  async function fetchMeta(folder, location) {
    const key = metaKey(folder, location);
    if (metaCache[key] !== undefined) return metaCache[key];
    const query = locationQuery(location);
    try {
      const data = await apiFetch(
        `/api/library/meta?folder=${encodeURIComponent(folder)}${query ? `&${query}` : ""}`
      );
      return (metaCache[key] = data);
    } catch (e) {
      return (metaCache[key] = null);
    }
  }

  function cardSubtitle(meta) {
    if (!meta || !meta.genres || !meta.genres.length) return "";
    return meta.genres.slice(0, 2).join(", ");
  }

  /* The titles payload already carries whatever the DB knows; seed the cache
     from it so cached titles render without a second request per card. */
  function seedMeta(entry, location) {
    if (!entry || typeof entry !== "object" || entry.hidden) return;
    const hasMeta =
      entry.poster_url || entry.score || entry.year || (entry.genres || []).length;
    if (!hasMeta) return;
    const key = metaKey(entry.folder, location);
    if (metaCache[key] !== undefined) return;
    metaCache[key] = {
      cover: entry.poster_url || null,
      score: entry.score || null,
      year: entry.year || null,
      genres: entry.genres || [],
    };
  }

  function renderCard(entry, location) {
    const folder = typeof entry === "string" ? entry : entry.folder;
    const hidden = Boolean(typeof entry === "object" && entry && entry.hidden);
    const title = (typeof entry === "object" && entry && entry.title) || folder;
    const key = metaKey(folder, location);
    const meta = hidden ? {} : metaCache[key] || {};
    const cover = meta.cover;
    const score = meta.score || null;
    const subtitle = hidden ? t("library.hidden", "Hidden") : cardSubtitle(meta);
    const hideLabel = hidden
      ? t("library.unhide", "Unhide")
      : t("library.hide", "Hide");

    let poster;
    if (hidden) {
      poster = '<div class="lib-hidden-cover" aria-hidden="true">🙈</div>';
    } else if (cover) {
      poster = `<img src="${esc(cover)}" alt="" loading="lazy" decoding="async" />`;
    } else {
      poster = '<div class="lib-card-placeholder" aria-hidden="true">&#127916;</div>';
    }

    return `
    <div class="poster-card lib-card${hidden ? " is-hidden" : ""}"
         data-folder="${attr(folder)}" data-meta-key="${attr(key)}">
      <a class="lib-card-link" href="${attr(watchHref(folder, location))}">
        <div class="lib-poster">
          ${poster}
          <span class="lib-card-score" ${score ? "" : "hidden"}>${score ? `&#9733; ${esc(String(score))}` : ""}</span>
        </div>
        <span class="downloaded-badge" aria-hidden="true">&#10003;</span>
        <span class="lib-card-play" aria-hidden="true">&#9654;</span>
        <div class="info">
          <div class="title" title="${attr(title)}">${esc(title)}</div>
          <div class="subtitle" ${subtitle ? "" : "hidden"}>${esc(subtitle)}</div>
        </div>
      </a>
      <button
        class="lib-hide-btn"
        type="button"
        data-action="toggle-hidden"
        data-hidden="${hidden ? "1" : "0"}"
        title="${attr(hideLabel)}"
        aria-label="${attr(`${hideLabel} ${title}`)}"
      >${hidden ? "👁" : "🙈"}</button>
    </div>`;
  }

  /* Meta (AniList lookups) trickle in after the cards are already on screen:
     refresh the card in place as each lookup lands. */
  function updateCard(key) {
    const meta = metaCache[key];
    if (!meta) return;
    const card = tree.querySelector(`.lib-card[data-meta-key="${CSS.escape(key)}"]`);
    if (!card) return;
    const poster = card.querySelector(".lib-poster");
    if (poster && meta.cover) {
      let image = poster.querySelector("img");
      if (!image) {
        image = document.createElement("img");
        image.alt = "";
        image.loading = "lazy";
        image.decoding = "async";
        poster.prepend(image);
      }
      image.src = meta.cover;
      const placeholder = poster.querySelector(".lib-card-placeholder");
      if (placeholder) placeholder.hidden = true;
    }
    const subtitle = card.querySelector(".subtitle");
    if (subtitle) {
      subtitle.textContent = cardSubtitle(meta);
      subtitle.hidden = !subtitle.textContent;
    }
    const score = card.querySelector(".lib-card-score");
    if (score) {
      score.textContent = meta.score ? `★ ${meta.score}` : "";
      score.hidden = !meta.score;
    }
  }

  function requestMeta(key, card) {
    const request = metaRequests[key];
    if (!request) return;
    fetchMeta(request.folder, request.location).then(() => updateCard(key));
  }

  function scheduleMeta(entries) {
    // Without IntersectionObserver, fall back to firing everything at once.
    const supportsLazy = "IntersectionObserver" in window;
    if (supportsLazy && !metaObserver) {
      metaObserver = new IntersectionObserver(
        (observed) => {
          observed.forEach((item) => {
            if (!item.isIntersecting) return;
            metaObserver.unobserve(item.target);
            requestMeta(item.target.dataset.metaKey, item.target);
          });
        },
        { rootMargin: "300px 0px" }
      );
    }
    entries.forEach(({ folder, location }) => {
      const key = metaKey(folder, location);
      metaRequests[key] = { folder, location };
      if (metaCache[key] !== undefined) return;
      const card = tree.querySelector(`.lib-card[data-meta-key="${CSS.escape(key)}"]`);
      if (!card) return;
      if (metaObserver) metaObserver.observe(card);
      else requestMeta(key, card);
    });
  }

  function renderTitles(entries, location) {
    return `<div class="browse-grid">${entries
      .map((entry) => renderCard(entry, location))
      .join("")}</div>`;
  }

  async function load() {
    const token = ++loadToken;
    const scrollY = window.scrollY;
    if (metaObserver) metaObserver.disconnect();
    tree.setAttribute("aria-busy", "true");
    tree.innerHTML = message(t("common.loading", "Loading..."));
    try {
      let data;
      try {
        data = await apiFetch("/api/library/locations");
      } catch (error) {
        tree.innerHTML = errorState(t("library.load_failed", "Failed to load library."));
        return;
      }
      if (token !== loadToken) return;
      locations = data.locations || [];

      if (!locations.length) {
        tree.innerHTML = message(t("library.empty", "No downloaded content found."));
        return;
      }

      const titlesQuery = (location) => {
        const query = locationQuery(location);
        return `/api/library/titles?${query}${showHidden ? `${query ? "&" : ""}include_hidden=1` : ""}`;
      };

      const perLocation = await Promise.all(
        locations.map(async (location) => {
          try {
            const titles = await apiFetch(titlesQuery(location));
            return titles.titles || [];
          } catch (error) {
            return null;
          }
        })
      );
      if (token !== loadToken) return;

      const failedLocations = perLocation.filter((titles) => titles === null).length;
      const foldersPerLocation = perLocation.map((titles) => {
        if (!titles) return [];
        const series = titles.filter((entry) => (entry.categories || []).includes("series"));
        const movies = titles.filter((entry) => (entry.categories || []).includes("movies"));
        return [...series, ...movies];
      });

      if (!foldersPerLocation.some((folders) => folders.length)) {
        tree.innerHTML = failedLocations
          ? errorState(t("library.load_failed", "Failed to load library."))
          : message(t("library.no_titles", "This folder is empty."));
        return;
      }

      // Titles whose meta is already in the DB paint fully on first render.
      locations.forEach((location, index) => {
        foldersPerLocation[index].forEach((entry) => seedMeta(entry, location));
      });

      const showHeaders = locations.length > 1;
      tree.innerHTML = locations
        .map((location, index) => {
          const folders = foldersPerLocation[index];
          if (!folders.length) return "";
          return `
        <div class="library-node" data-location="${index}">
          ${showHeaders ? `<div class="lib-section-title">${esc(locationName(location))}</div>` : ""}
          ${renderTitles(folders, location)}
        </div>`;
        })
        .join("");

      if (failedLocations) {
        tree.insertAdjacentHTML(
          "afterbegin",
          `<div class="library-load-error" role="alert">
            ${esc(t("library.load_failed", "Failed to load library."))}
            <button type="button" class="btn btn-secondary" data-action="retry-library">
              ${esc(t("common.retry", "Try again"))}
            </button>
          </div>`
        );
      }

      // One lookup per card, only once the card is near the viewport; each
      // fills its own card in as it lands.
      const requests = [];
      locations.forEach((location, index) => {
        foldersPerLocation[index]
          .filter((entry) => !entry.hidden)
          .forEach((entry) => requests.push({ folder: entry.folder, location }));
      });
      scheduleMeta(requests);

      // Keep the reader where they were instead of snapping to the top.
      requestAnimationFrame(() => {
        if (token === loadToken && scrollY > 0) window.scrollTo(0, scrollY);
      });
    } finally {
      if (token === loadToken) tree.setAttribute("aria-busy", "false");
    }
  }

  async function toggleHidden(button) {
    const card = button.closest(".lib-card");
    if (!card) return;
    const locationNode = card.closest("[data-location]");
    const location = locations[Number(locationNode && locationNode.dataset.location)];
    const hidden = button.dataset.hidden !== "1";
    const payload = { folder: card.dataset.folder, hidden };
    if (location && location.custom_path_id) payload.custom_path_id = location.custom_path_id;
    if (location && location.lang_folder) payload.lang_folder = location.lang_folder;
    button.disabled = true;
    try {
      await apiFetch("/api/library/hide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      delete metaCache[card.dataset.metaKey];
      load();
    } catch (error) {
      button.disabled = false;
      showToast(error.message);
    }
  }

  tree.addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]");
    if (!action) return;
    if (action.dataset.action === "retry-library") {
      event.preventDefault();
      load();
      return;
    }
    if (action.dataset.action === "toggle-hidden") {
      event.preventDefault();
      toggleHidden(action);
    }
  });

  // Broken posters fall back to the placeholder without an inline handler.
  tree.addEventListener(
    "error",
    (event) => {
      const image = event.target;
      if (!image || image.tagName !== "IMG") return;
      const poster = image.closest(".lib-poster");
      if (!poster) return;
      image.remove();
      const placeholder = poster.querySelector(".lib-card-placeholder");
      if (placeholder) placeholder.hidden = false;
    },
    true
  );

  if (refreshBtn) refreshBtn.addEventListener("click", load);
  if (showHiddenCheckbox) {
    showHiddenCheckbox.addEventListener("change", () => {
      showHidden = showHiddenCheckbox.checked;
      load();
    });
  }
  load();
})();
