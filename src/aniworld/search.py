import html as html_module
import os
import random
import re
from urllib.parse import quote

from .common.search import (
    limit_reached,
    limit_results,
    validate_limit,
)

try:
    from .ascii import display_ascii_art
    from .config import GLOBAL_SESSION, logger
except ImportError:
    from aniworld.ascii import display_ascii_art
    from aniworld.config import GLOBAL_SESSION, logger

SEARCH_URL = "https://aniworld.to/ajax/search"
RANDOM_URL = "https://aniworld.to/ajax/randomGeneratorSeries"
NEW_EPISODES_URL = "https://aniworld.to/neue-episoden"
HOME_URL = "https://aniworld.to"
MAX_PAGES = 15

# A genre page lists 30 animes and pages are /genre/<slug>/<n>.
GENRE_PAGE_SIZE = 30

_homepage_cache = None
_series_html_content = None


def random_anime():
    """Fetch a random anime series from Aniworld and return its URL."""
    data = {"productionStart": "all", "productionEnd": "all", "genres[]": "all"}

    try:
        response = GLOBAL_SESSION.post(RANDOM_URL, data=data)
        response.raise_for_status()
        result = response.json()

        if not result:
            logger.error("Random anime response is empty")
            return None

        # Pick a random anime from the list
        series = random.choice(result)
        link = series.get("link")
        if link:
            return f"https://aniworld.to/anime/stream/{link}"
        else:
            logger.error("No link found in selected random anime")
            return None

    except Exception as e:
        logger.error(f"Failed to fetch random anime: {e}")
        return None


def query(keyword):
    """Send a search request to Aniworld with given keyword."""
    response = GLOBAL_SESSION.post(SEARCH_URL, data={"keyword": keyword})
    try:
        return response.json()  # Returns a list of dicts
    except ValueError:
        return None


def _relevance_score(title: str, keyword: str) -> int:
    t = title.lower()
    k = keyword.lower()
    if t == k:
        return 0
    if t.startswith(k):
        return 1
    words = t.split()
    if k in words:
        return 2
    if k in t:
        return 3
    # No direct match on the whole phrase: rank by how many of the keyword's
    # individual words are missing from the title, so partial matches (e.g.
    # "F1 - Der Film" for "F1 Movie") beat wholly unrelated results.
    tokens = [tok for tok in re.split(r"\W+", k) if tok]
    if tokens:
        title_tokens = set(re.split(r"\W+", t))
        missing = sum(1 for tok in tokens if tok not in title_tokens and tok not in t)
        return 4 + missing
    return 4


def fetch_new_episodes():
    """Fetch the latest episodes from aniworld.to/neue-episoden.

    Returns a deduplicated list of episode dicts (grouped by URL, languages merged),
    or None on error.
    """
    try:
        response = GLOBAL_SESSION.get(NEW_EPISODES_URL)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        logger.error(f"Failed to fetch new episodes: {e}")
        return None

    # Try to narrow scope to the newEpisodeList block
    block_match = re.search(r'class="newEpisodeList">(.*)', html, re.DOTALL)
    search_html = block_match.group(1) if block_match else html

    seen = {}
    ordered_urls = []

    # Process each episode row individually to prevent matching flags from adjacent episodes
    rows = re.finditer(
        r'<div class="col-md-12">(.*?)</div>\s*</div>\s*</div>', search_html, re.DOTALL
    )

    for row_match in rows:
        row_html = row_match.group(1)

        # 1. Extract link, season, and episode
        link_match = re.search(
            r'<a\s+[^>]*?href="(?:https://aniworld\.to)?(/anime/stream/[^"]+/staffel-(\d+)/episode-(\d+))"[^>]*>',
            row_html,
        )
        if not link_match:
            continue

        path, season_str, episode_str = link_match.groups()
        url = f"https://aniworld.to{path}"
        season = int(season_str)
        episode = int(episode_str)

        # 2. Extract title
        title_match = re.search(r"<strong>(.*?)</strong>", row_html, re.DOTALL)
        import html as html_module

        title = (
            " ".join(html_module.unescape(title_match.group(1)).split())
            if title_match
            else ""
        )

        # 3. Extract date
        date_match = re.search(
            r'<span[^>]*class="[^"]*elementFloatRight[^"]*"[^>]*>(.*?)</span>', row_html
        )
        date = date_match.group(1).strip() if date_match else ""

        if url not in seen:
            seen[url] = {
                "title": title,
                "url": url,
                "season": season,
                "episode": episode,
                "date": date,
                "languages": [],
            }
            ordered_urls.append(url)

        # 4. Extract all language flags in this specific row block
        flags = re.finditer(
            r'<img[^>]+(?:src|data-src)="[^"]*?/(\w[\w-]*)\.svg"[^>]*>', row_html
        )
        for flag_match in flags:
            lang = flag_match.group(1)
            if lang and lang not in seen[url]["languages"]:
                seen[url]["languages"].append(lang)

    return [seen[url] for url in ordered_urls]


def _fetch_homepage():
    """Fetch the homepage HTML, using a simple module-level cache."""
    global _homepage_cache
    if _homepage_cache is not None:
        return _homepage_cache

    try:
        response = GLOBAL_SESSION.get(HOME_URL)
        response.raise_for_status()
        _homepage_cache = response.text
        return _homepage_cache
    except Exception as e:
        logger.error(f"Failed to fetch homepage: {e}")
        return None


def _parse_cover_items(section_html):
    """Extract the anime cards out of a homepage section or a genre listing.

    Both use the same cover markup, so the genre pages reuse this.
    """
    # Extract items — anchor on /anime/stream/ links with cover structure
    item_pattern = re.compile(
        r'<a\s+href="(/anime/stream/[^"]+)"[^>]*title="([^"]*)"[^>]*>'
        r"(.*?)</a>",
        re.DOTALL,
    )

    results = []
    seen_urls = set()

    for m in item_pattern.finditer(section_html):
        path, link_title, inner = m.groups()
        url = f"https://aniworld.to{path}"

        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Title from <h3> (strip inner tags), unescaped so entities like
        # &#039; do not end up on screen as-is
        h3_match = re.search(r"<h3>(.*?)</h3>", inner, re.DOTALL)
        raw_title = h3_match.group(1) if h3_match else link_title
        title = html_module.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()

        # Genre from <small>
        small_match = re.search(r"<small>(.*?)</small>", inner, re.DOTALL)
        genre = (
            html_module.unescape(re.sub(r"<[^>]+>", "", small_match.group(1))).strip()
            if small_match
            else ""
        )

        # Poster from data-src on img
        img_match = re.search(r'data-src="([^"]+)"', inner)
        poster_url = ""
        if img_match:
            poster_path = img_match.group(1)
            poster_url = (
                poster_path
                if poster_path.startswith("http")
                else f"https://aniworld.to{poster_path}"
            )

        results.append(
            {
                "title": title,
                "url": url,
                "genre": genre,
                "poster_url": poster_url,
            }
        )

    return results


def _extract_cover_list(html, heading):
    """Extract a list of anime cover items from a homepage section.

    Finds the section identified by the <h2> heading text, then extracts
    coverListItem entries until the next section.
    """
    # Find the heading position
    heading_pattern = re.compile(rf"<h2>\s*{re.escape(heading)}\s*</h2>", re.IGNORECASE)
    heading_match = heading_pattern.search(html)
    if not heading_match:
        logger.warning(f"Homepage section '{heading}' not found")
        return []

    # Slice from heading to the next <h2> or end
    start = heading_match.end()
    next_h2 = re.search(r"<h2>", html[start:])
    section_html = html[start : start + next_h2.start()] if next_h2 else html[start:]
    return _parse_cover_items(section_html)


def fetch_genres():
    """Genre names and slugs, read off the genre list at the end of the homepage.

    Returns the genre names and slugs currently present on the site.
    """
    html = _fetch_homepage()
    genres = []

    if html:
        block = re.search(
            r'<ul class="homeContentGenresList">(.*?)</ul>', html, re.DOTALL
        )
        if block:
            links = re.findall(
                r'<a[^>]*href="[^"]*/genre/([^"/]+)"[^>]*>(.*?)</a>',
                block.group(1),
                re.DOTALL,
            )
            for slug, label in links:
                name = html_module.unescape(re.sub(r"<[^>]+>", "", label)).strip()
                if name:
                    genres.append({"name": name, "slug": slug})

    if not genres:
        logger.warning("Genre list missing from the homepage")
    return genres


def fetch_genre_animes(slug, page=1, *, limit=None):
    """Fetch one page of a genre listing.

    Returns {"results": [...], "has_more": bool}; request errors propagate.
    """
    validate_limit(limit)
    if limit == 0:
        return {"results": [], "has_more": False}
    url = f"{HOME_URL}/genre/{quote(slug, safe='')}"
    if page > 1:
        url = f"{url}/{page}"

    response = GLOBAL_SESSION.get(url)
    response.raise_for_status()

    html = response.text
    # Everything before the list is navigation, cut it off so only cards match
    start = html.find('class="seriesListContainer')
    results = _parse_cover_items(html[start:] if start != -1 else html)
    # The pager only links to the next page while there is one
    has_more = f"/genre/{slug}/{page + 1}" in html
    return {"results": limit_results(results, limit), "has_more": has_more}


def fetch_aniworld_genres():
    """Return AniWorld's current genre names and slugs."""
    return fetch_genres()


def query_aniworld(keyword="", *, genre=None, limit=None):
    """Search AniWorld titles, or follow its genre pages up to a result limit."""
    validate_limit(limit)
    if limit == 0:
        return []
    if genre not in (None, ""):
        if keyword.strip():
            raise ValueError("Use either a keyword or an AniWorld genre, not both.")
        results = []
        page = 1
        while True:
            data = fetch_genre_animes(genre, page=page)
            results.extend(data["results"])
            if (
                limit_reached(results, limit)
                or not data["has_more"]
                or not data["results"]
            ):
                return limit_results(results, limit)
            page += 1
    if not keyword.strip():
        return []
    results = query(keyword) or []
    if isinstance(results, dict):
        results = [results]
    return limit_results(results, limit)


def fetch_new_animes():
    """Fetch the 'Neue Animes' section from the homepage.

    Returns a list of anime dicts or None on error.
    """
    html = _fetch_homepage()
    if html is None:
        return None
    return _extract_cover_list(html, "Neue Animes")


def fetch_popular_animes():
    """Fetch the 'Derzeit beliebt' section from the homepage.

    Returns a list of anime dicts or None on error.
    """
    html = _fetch_homepage()
    if html is None:
        return None
    return _extract_cover_list(html, "Derzeit beliebt")


def _curses_menu(stdscr, options):
    """Display a simple curses menu to select an option with scrolling support."""
    import curses

    curses.curs_set(0)
    curses.start_color()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)

    selected = 0
    top = 0

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        display_height = h - 2  # leave room for borders

        # Adjust top for scrolling
        if selected < top:
            top = selected
        elif selected >= top + display_height:
            top = selected - display_height + 1

        # Display visible options
        for idx in range(top, min(top + display_height, len(options))):
            option = options[idx]
            title = (
                option.get("title", "Unknown Title")
                .replace("<em>", "")
                .replace("</em>", "")
            )
            title = title[: w - 4]  # clip to width
            y = idx - top + 1
            x = 2
            if idx == selected:
                stdscr.attron(curses.color_pair(1))
                stdscr.addstr(y, x, title)
                stdscr.attroff(curses.color_pair(1))
            else:
                stdscr.addstr(y, x, title)

        stdscr.refresh()
        key = stdscr.getch()

        if key in [curses.KEY_UP, ord("k")]:
            selected = (selected - 1) % len(options)
        elif key in [curses.KEY_DOWN, ord("j")]:
            selected = (selected + 1) % len(options)
        elif key in [curses.KEY_ENTER, ord("\n")]:
            return options[selected]


def search():
    """Prompt user for a search keyword and return a single series URL using a curses menu."""
    import curses

    display_ascii_art()

    use_random = os.getenv("ANIWORLD_RANDOM_ANIME", "0") == "1"

    base_url = "https://aniworld.to"

    if use_random:
        result = random_anime()
        if result:
            logger.debug(f"Random anime selected: {result}")
            return result
        else:
            logger.error("Failed to get random anime. Please try again.")
            return None

    while True:
        keyword = input("\nSearch for a series: ").strip()
        if not keyword:
            logger.error("No keyword entered, aborting search.")
            return None

        results = query(keyword)

        if not results:
            logger.error("No results found. Please try again.")
            continue

        if isinstance(results, dict):
            results = [results]
        elif isinstance(results, str):
            return results

        logger.debug(results)

        stream_pattern = re.compile(
            r"^/anime/stream/[a-zA-Z0-9\-]+/?$", re.IGNORECASE
        )

        stream_results = [
            item for item in results if stream_pattern.match(item.get("link") or "")
        ]

        if not stream_results:
            logger.error("No streamable series found. Please try again.")
            continue

        if len(stream_results) == 1:
            selected_item = stream_results[0]
            title = selected_item.get("title") or selected_item.get(
                "name", "Unknown Title"
            )
            logger.debug(f"Auto-selected: {title}")
            return f"{base_url}{selected_item['link']}"

        def menu_wrapper(stdscr, stream_results=stream_results):
            curses.start_color()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
            selected_item = _curses_menu(stdscr, stream_results)
            return f"{base_url}{selected_item['link']}"

        return curses.wrapper(menu_wrapper)

