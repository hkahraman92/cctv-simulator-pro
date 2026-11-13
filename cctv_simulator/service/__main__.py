"""``python -m cctv_simulator.service`` — stdio JSON-RPC sidecar for the desktop shell."""
from __future__ import annotations

import sys

from .methods import METHODS
from .protocol import serve_stdio


def main() -> int:
    # Before the first Turkish character: stderr starts in the console code page. A cp1254 console
    # wrote a byte the shell could not decode; a cp1252 one (CI runner) has no dotless i at all and
    # the process died here with UnicodeEncodeError. serve_stdio does the same again, harmlessly.
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print("cctv-core: hazır", file=sys.stderr, flush=True)
    serve_stdio(METHODS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
