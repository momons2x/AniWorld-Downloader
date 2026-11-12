"""Re:Anime flixcloud embeds.

The flixcloud.cc player encrypts its playback URL (and the HLS manifests) with
a WebAssembly helper, so decryption runs in the bundled Node sidecar
(`models/reanime/flixcloud_resolve.js`), which writes the plain playlist
tree (master + variants + audio) to a directory. The decrypted master path is
handed to FFmpeg; its segments stay remote.
"""

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

try:
    from ...config import ANIWORLD_CONFIG_DIR
except ImportError:
    from aniworld.config import ANIWORLD_CONFIG_DIR


def _resolve_with_node(embed_url, output_dir):
    node = shutil.which("node")
    if not node:
        raise FileNotFoundError(
            "Re:Anime needs Node.js (it decrypts flixcloud streams). "
            "Install Node.js from https://nodejs.org to use this provider."
        )
    script = (
        Path(__file__).resolve().parent.parent.parent
        / "models"
        / "reanime"
        / "flixcloud_resolve.js"
    )
    if not script.exists():
        raise FileNotFoundError(f"Missing resolver script: {script}")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [node, str(script), embed_url, str(output_dir)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"flixcloud resolution failed: {detail[:400]}")
    return json.loads(result.stdout)


def get_direct_link_from_reanimehd1(embed_url):
    """HD-1 server (1080p)."""
    return _resolve(embed_url)


def get_direct_link_from_reanimehd2(embed_url):
    """HD-2 server (1080p)."""
    return _resolve(embed_url)


def _resolve(embed_url):
    if not embed_url:
        raise ValueError("Embed URL cannot be empty")

    cache_dir = ANIWORLD_CONFIG_DIR / "flixcloud"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(embed_url.encode("utf-8")).hexdigest()[:16]
    manifest_path = cache_dir / digest / "master.m3u8"
    # The token embedded in the manifest expires after a few hours, so a
    # cached copy older than that is always re-resolved.
    if (
        manifest_path.exists()
        and time.time() - manifest_path.stat().st_mtime < 3 * 3600
        and all(manifest_path.parent.glob("*.m3u8"))
    ):
        return str(manifest_path)

    data = _resolve_with_node(embed_url, cache_dir / digest)
    manifest_path = Path(data["manifest"])
    if not manifest_path.exists():
        raise RuntimeError("flixcloud resolution produced no manifest")
    return str(manifest_path)
