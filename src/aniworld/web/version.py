"""Resolve the version string shown in the navbar."""

import hashlib
import re
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_TTL = 10.0
_static_cache: dict = {"at": 0.0, "value": ""}


def get_version():
    """Installed package version, falling back to pyproject when run from source."""
    try:
        installed = package_version("aniworld")
        if installed:
            return installed
    except PackageNotFoundError:
        pass
    except Exception:
        pass

    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.exists():
            continue
        try:
            match = re.search(
                r'^\s*version\s*=\s*"([^"]+)"',
                pyproject.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        except OSError:
            break
        return match.group(1) if match else ""
    return ""


def get_static_version():
    """Content hash of the static files, used as the ?v= cache buster.

    Static assets are served with a one year max-age, so the query string is
    the only thing that forces a browser to pick up a changed script or
    stylesheet. Hashing names, sizes and mtimes means any edit bumps the
    version without a restart.
    """
    now = time.monotonic()
    if now - _static_cache["at"] < _STATIC_TTL and _static_cache["value"]:
        return _static_cache["value"]

    digest = hashlib.sha256()
    try:
        for path in sorted(_STATIC_DIR.rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            digest.update(str(path.relative_to(_STATIC_DIR)).encode())
            digest.update(str(stat.st_size).encode())
            digest.update(str(stat.st_mtime_ns).encode())
    except OSError:
        return _static_cache["value"] or "dev"

    _static_cache["at"] = now
    _static_cache["value"] = digest.hexdigest()[:12]
    return _static_cache["value"]
