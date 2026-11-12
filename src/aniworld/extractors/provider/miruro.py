"""Miruro CDN servers (hop/bee/sun/bun).

The Miruro episode model resolves these stream URLs from the site's own
sources API; each server only serves its HLS with the headers configured in
PROVIDER_HEADERS_D/W, so the extractor just passes the URL through.
"""


def _passthrough(embed_url):
    if not embed_url:
        raise ValueError("Embed URL cannot be empty")
    url = str(embed_url).strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Not a stream URL: {embed_url}")
    return url


def get_direct_link_from_mirurohop(embed_url):
    """Krussdomi (1080p, separate audio) server."""
    return _passthrough(embed_url)


def get_direct_link_from_mirurobee(embed_url):
    """MegaPlay mirrors (720p max)."""
    return _passthrough(embed_url)


def get_direct_link_from_mirurosun(embed_url):
    """Ultracloud (sun/coralarch) server."""
    return _passthrough(embed_url)


def get_direct_link_from_mirurobun(embed_url):
    """Bun server (same CDN family as hop)."""
    return _passthrough(embed_url)
