"""Frozen entry point of cctv-core.exe (the desktop shell's sidecar)."""
from cctv_simulator.service.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
