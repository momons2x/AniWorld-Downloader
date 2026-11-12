"""MegaPlay hoster (used by 9anime's gogoanime.me.uk newplayer wrapper).

The player encrypts its source payload with AES-CBC (key/IV baked into
app.main.js) and returns a base64url JSON blob under ``enc``; the decrypted
payload contains the HLS master playlist URL.
"""

import base64
import json
import re

try:
    from ...config import GLOBAL_SESSION
except ImportError:
    from aniworld.config import GLOBAL_SESSION

MEGAPLAY_BASE = "https://megaplay.buzz"

_PLAYER_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?megaplay\.buzz/stream/mal/[0-9]+/[0-9]+/(?:sub|dub)[^\"'\s]*",
    re.IGNORECASE,
)
_FILE_ID_PATTERN = re.compile(r'data-id="([0-9]+)"')
_SOURCE_ENDPOINTS = (
    "/stream/getSourcesNew",
    "/stream/getSources",
)

_KEY = b"i?LMTAx0Q6,:}50U".ljust(32, b"\x00")
_IV = b"W0;27ToaUpl_P%'c".ljust(16, b"\x00")


def _get(url, referer=MEGAPLAY_BASE + "/"):
    response = GLOBAL_SESSION.get(
        url,
        headers={
            "Referer": referer,
            # Cloudflare serves brotli, which niquests cannot decode without
            # the optional brotli package.
            "Accept-Encoding": "identity",
        },
    )
    response.raise_for_status()
    return response.text


def _decrypt_payload(payload):
    enc = payload.get("enc") if isinstance(payload, dict) else None
    if not enc:
        return payload

    padded = enc + "=" * (-len(enc) % 4)
    raw = base64.urlsafe_b64decode(padded.replace("+", "-").replace("/", "_"))

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise ValueError("MegaPlay sources need the cryptography package") from exc

    decryptor = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).decryptor()
    plain = decryptor.update(raw) + decryptor.finalize()

    padding = plain[-1] if plain else 0
    if 1 <= padding <= 16 and plain.endswith(bytes([padding]) * padding):
        plain = plain[:-padding]
    return json.loads(plain.decode("utf-8"))


def _find_stream_page(url):
    if "megaplay.buzz/stream/" in url:
        return url
    html = _get(url)
    match = _PLAYER_URL_PATTERN.search(html)
    if not match:
        raise ValueError(f"MegaPlay player not found in {url}")
    return match.group(0)


def _find_file_id(stream_page):
    match = _FILE_ID_PATTERN.search(stream_page)
    if not match:
        raise ValueError("MegaPlay file id not found")
    return match.group(1)


def _find_source(file_id):
    errors = []
    for endpoint in _SOURCE_ENDPOINTS:
        url = f"{MEGAPLAY_BASE}{endpoint}?id={file_id}"
        try:
            payload = json.loads(_get(url))
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
            continue
        try:
            payload = _decrypt_payload(payload)
        except Exception as exc:
            errors.append(f"{endpoint} decrypt: {exc}")
            continue
        for key in ("file", "url", "source"):
            value = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(value, str) and value.startswith("http"):
                return value
        sources = payload.get("sources") if isinstance(payload, dict) else None
        if isinstance(sources, list):
            for source in sources:
                value = source.get("file") if isinstance(source, dict) else None
                if isinstance(value, str) and value.startswith("http"):
                    return value
        errors.append(f"{endpoint}: no stream url in payload")
    raise ValueError(f"MegaPlay stream not found for file {file_id} ({'; '.join(errors)})")


def get_direct_link_from_megaplay(embed_url):
    """Resolve a MegaPlay stream page (or its wrapper) to the HLS playlist."""
    if not embed_url:
        raise ValueError("Embed URL cannot be empty")

    stream_page = _get(_find_stream_page(embed_url))
    file_id = _find_file_id(stream_page)
    return _find_source(file_id)
