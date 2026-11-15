"""Live sites: front page -> a current title -> episode -> every hoster it offers.

Manual check, deliberately NOT part of the automated suite. It talks to the
live sites and live hosters, so it fails whenever they change their markup,
block the runner or go down. Nothing here is named `test_*`, so pytest imports
this module and collects nothing, and a push stays green regardless.

    python tests/test_providers_sites.py
    python tests/test_providers_sites.py nineanime
    python tests/test_providers_sites.py miruro voe dood   # only matching hosters
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from provider_check import run_site

SITES = {
    "nineanime": "fetch_nineanime_latest",
    "miruro": "fetch_miruro_latest",
    "reanime": "fetch_reanime_latest",
}

if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]
    wanted = [name for name in SITES if name in args]
    hosters = [a for a in args if a not in SITES]

    failed = 0
    for name in wanted or list(SITES):
        failed += run_site(name.capitalize(), SITES[name], hosters)
    sys.exit(failed)
