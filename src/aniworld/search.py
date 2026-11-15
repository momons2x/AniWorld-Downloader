"""Interactive terminal search.

Asks every live site for a keyword at once and offers the merged hits in a
curses menu, so one query covers the whole catalogue instead of one scraper.
The URL that comes back is a series page the providers recognise, which is
what entry.point hands to model_for_url().
"""

import os

try:
    from .ascii import display_ascii_art
    from .config import logger
except ImportError:
    from aniworld.ascii import display_ascii_art
    from aniworld.config import logger

# The sites the terminal search queries, in the order they are offered.
SEARCH_SITES = ("nineanime", "miruro", "reanime")


def _search_all(keyword):
    """Every site's hits for a keyword, tagged with the site that found them.

    Imported lazily because aniworld.web.sitesearch pulls in the web stack,
    which a first call from the terminal should not pay for up front. A site
    that errors is skipped: one slow mirror should not cost the whole query.
    """
    from .web.sitesearch import search as search_site

    merged = []
    seen = set()
    for site in SEARCH_SITES:
        for item in search_site(site, keyword):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            merged.append({**item, "site": site})
    return merged


def _random_series():
    """A random series URL, drawn from one live site's newest uploads."""
    import random

    from .models.miruro._api import fetch_latest_animes as miruro_latest
    from .models.nineanime._api import fetch_latest_animes as nineanime_latest
    from .models.reanime._api import fetch_latest_animes as reanime_latest

    # The web UI's Random button keeps its own copy of this list, in
    # web/views/api_media.py, because that layer already holds these fetchers
    # for its browse rows.
    results = random.choice(
        (nineanime_latest, miruro_latest, reanime_latest)
    )()
    if not results:
        return None
    return random.choice(results)["url"]


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
    """Prompt for a search keyword and return a single series URL."""
    import curses

    display_ascii_art()

    if os.getenv("ANIWORLD_RANDOM_ANIME", "0") == "1":
        url = _random_series()
        if url:
            logger.debug(f"Random series selected: {url}")
            return url
        logger.error("Failed to pick a random series.")
        return None

    while True:
        keyword = input("\nSearch for a series: ").strip()
        if not keyword:
            logger.error("No keyword entered, aborting search.")
            return None

        results = _search_all(keyword)

        if not results:
            logger.error("No results found. Please try again.")
            continue

        logger.debug(results)

        # Tag each hit with where it came from: the same title often exists on
        # more than one site, and the URL is the only thing that tells them apart.
        options = [{**item, "title": f"[{item['site']}] {item['title']}"} for item in results]

        if len(options) == 1:
            logger.debug(f"Auto-selected: {options[0]['title']}")
            return options[0]["url"]

        def menu_wrapper(stdscr, options=options):
            curses.start_color()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
            return _curses_menu(stdscr, options)["url"]

        return curses.wrapper(menu_wrapper)
