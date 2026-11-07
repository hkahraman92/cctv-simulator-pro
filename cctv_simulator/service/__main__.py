"""``python -m cctv_simulator.service`` — stdio JSON-RPC sidecar for the desktop shell."""
from __future__ import annotations

import sys

from .methods import METHODS
from .protocol import serve_stdio


def main() -> int:
    print("cctv-core: hazır", file=sys.stderr, flush=True)
    serve_stdio(METHODS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
