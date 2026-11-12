/* Small helpers shared by every page. */

function esc(value) {
  const node = document.createElement("div");
  node.textContent = value == null ? "" : String(value);
  return node.innerHTML;
}

// Search results arrive HTML-escaped from the sites, decode before re-escaping
function decodeEntities(value) {
  const node = document.createElement("textarea");
  node.innerHTML = value == null ? "" : String(value);
  return node.value;
}

function showToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.remove("show"), 4000);
}

/* Every request gets a deadline unless the caller opts out with
   timeoutMs: 0. A poller that never settles is worse than one that fails: the
   failure can be shown and retried, the hang just leaves the page on
   "Loading..." for good. Slow provider scrapes pass a longer explicit value. */
const API_DEFAULT_TIMEOUT_MS = 30000;

async function apiFetch(url, options) {
  const {
    timeoutMs = API_DEFAULT_TIMEOUT_MS,
    signal: callerSignal,
    ...init
  } = options || {};
  let timer = null;
  let timedOut = false;
  let timeoutController = null;
  let detachCallerAbort = null;

  if (timeoutMs) {
    timeoutController = new AbortController();
    timer = setTimeout(() => {
      timedOut = true;
      timeoutController.abort();
    }, timeoutMs);
  }

  // Keep a caller-supplied abort signal working alongside the timeout.
  if (timeoutController && callerSignal) {
    if (typeof AbortSignal !== "undefined" && AbortSignal.any) {
      init.signal = AbortSignal.any([callerSignal, timeoutController.signal]);
    } else if (callerSignal.aborted) {
      timeoutController.abort();
      init.signal = timeoutController.signal;
    } else {
      const forward = () => timeoutController.abort();
      callerSignal.addEventListener("abort", forward, { once: true });
      detachCallerAbort = () => callerSignal.removeEventListener("abort", forward);
      init.signal = timeoutController.signal;
    }
  } else if (timeoutController) {
    init.signal = timeoutController.signal;
  } else if (callerSignal) {
    init.signal = callerSignal;
  }

  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if (error.name === "AbortError" && timedOut) {
      throw new Error(t("common.timed_out", "The server did not answer in time"));
    }
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
    if (detachCallerAbort) detachCallerAbort();
  }

  let data = null;
  try {
    data = await response.json();
  } catch (e) {
    data = null;
  }
  if (!response.ok) {
    const message = (data && data.error) || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return data || {};
}

function apiSend(url, method, body, options) {
  return apiFetch(url, {
    ...options,
    method: method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
}

function formatSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/* ===== Modals ===== */

/* Themes read body[data-modal] to react to a dialog being up, which beats
   every theme writing its own :has(.overlay.open) and breaking when a class
   gets renamed. Kept in one place so it cannot drift from reality. */
function syncModalState() {
  document.body.dataset.modal = document.querySelector(".overlay.open")
    ? "open"
    : "closed";
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])"
].join(",");

function focusableIn(root) {
  return Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR)).filter((el) => {
    if (el.closest("[hidden]")) return false;
    if (el.getAttribute("aria-hidden") === "true") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0;
  });
}

function modalDialogOf(overlay) {
  return overlay.querySelector(".modal") || overlay;
}

/* Open dialogs stack so a dialog opened on top of another restores focus back
   into the one underneath instead of dropping it on the page behind. */
const modalStack = [];

function topModal() {
  while (modalStack.length) {
    const top = modalStack[modalStack.length - 1];
    if (top.overlay.classList.contains("open")) return top;
    modalStack.pop();
  }
  return null;
}

function openModal(id) {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  if (!overlay.classList.contains("open")) {
    overlay.hidden = false;
    overlay.classList.add("open");
    modalStack.push({ overlay, previous: document.activeElement });
    const dialog = modalDialogOf(overlay);
    dialog.setAttribute("role", dialog.getAttribute("role") || "dialog");
    dialog.setAttribute("aria-modal", "true");
    if (!dialog.hasAttribute("aria-label") && !dialog.hasAttribute("aria-labelledby")) {
      dialog.setAttribute("aria-label", t("common.dialog", "Dialog"));
    }
    if (!dialog.hasAttribute("tabindex")) dialog.setAttribute("tabindex", "-1");
    const first = focusableIn(overlay)[0];
    (first || dialog).focus({ preventScroll: true });
  }
  syncModalState();
}

function closeModal(id) {
  const overlay = typeof id === "string" ? document.getElementById(id) : id;
  if (!overlay || !overlay.classList.contains("open")) return;
  const index = modalStack.findIndex((entry) => entry.overlay === overlay);
  const entry = index >= 0 ? modalStack.splice(index, 1)[0] : null;
  overlay.classList.remove("open");
  overlay.hidden = true;
  syncModalState();
  const previous = entry && entry.previous;
  if (previous && typeof previous.focus === "function" && document.contains(previous)) {
    previous.focus({ preventScroll: true });
  }
  overlay.dispatchEvent(new CustomEvent("modal-closed"));
}

document.addEventListener("click", (event) => {
  const overlay = event.target.closest("[data-close-on-backdrop]");
  if (overlay && event.target === overlay) {
    closeModal(overlay);
    return;
  }

  const closeBtn = event.target.closest("[data-close-modal]");
  if (closeBtn) {
    const parent = closeBtn.closest(".overlay");
    if (parent) closeModal(parent);
  }
});

document.addEventListener("keydown", (event) => {
  const top = topModal();
  if (!top) return;

  if (event.key === "Escape") {
    event.preventDefault();
    closeModal(top.overlay);
    return;
  }
  if (event.key !== "Tab") return;

  const items = focusableIn(top.overlay);
  const dialog = modalDialogOf(top.overlay);
  if (!items.length) {
    event.preventDefault();
    dialog.focus({ preventScroll: true });
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!top.overlay.contains(active)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus({ preventScroll: true });
    return;
  }
  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus({ preventScroll: true });
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus({ preventScroll: true });
  }
});

/* Focus that escapes a dialog (browser chrome, programmatic focus, a stray
   click on the backdrop) is pulled straight back into it. */
document.addEventListener("focusin", (event) => {
  const top = topModal();
  if (!top || top.overlay.contains(event.target)) return;
  const items = focusableIn(top.overlay);
  (items[0] || modalDialogOf(top.overlay)).focus({ preventScroll: true });
});

/* ===== Mobile navigation ===== */
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("navToggle");
  const nav = document.getElementById("mainNav");
  if (!toggle || !nav) return;
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
});

/* ===== Update Checker ===== */
async function checkUpdate() {
  const badge = document.getElementById("appVersionBadge");
  if (!badge) return;

  const current = badge.dataset.currentVersion;
  if (!current) return;

  const cacheKey = "aniworld_latest_release";
  const cacheTimeKey = "aniworld_latest_release_time";

  const now = Date.now();
  let latest = localStorage.getItem(cacheKey);
  const lastCheck = localStorage.getItem(cacheTimeKey);

  if (!latest || !lastCheck || now - parseInt(lastCheck) > 24 * 60 * 60 * 1000) {
    try {
      const res = await fetch("https://api.github.com/repos/phoenixthrush/AniWorld-Downloader/releases/latest");
      if (res.ok) {
        const data = await res.json();
        latest = data.tag_name.replace(/^v\.?/, "");
        localStorage.setItem(cacheKey, latest);
      }
    } catch (e) {
      console.error("Failed to check for updates", e);
    } finally {
      // Always update the check time so we don't spam the API on failures or rate limits
      localStorage.setItem(cacheTimeKey, now.toString());
    }
  }

  if (latest && isNewerVersion(latest, current)) {
    badge.innerHTML = `v${current} <span class="update-available-badge">Update Available</span>`;
  }
}

function isNewerVersion(latest, current) {
  const l = latest.split('.').map(Number);
  const c = current.split('.').map(Number);
  for (let i = 0; i < Math.max(l.length, c.length); i++) {
    const lPart = l[i] || 0;
    const cPart = c[i] || 0;
    if (lPart > cPart) return true;
    if (lPart < cPart) return false;
  }
  return false;
}

document.addEventListener("DOMContentLoaded", checkUpdate);
