"""
Recover server.py source from a still-running uvicorn process that loaded the
full app before the file was deleted.

1. Do NOT restart uvicorn.
2. In the same Python environment, run:

   python scripts/recover_server_from_running_uvicorn.py

If the old module is still in sys.modules, this writes backend/app_full.py and server.py.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_APP = ROOT / "backend" / "app_full.py"
OUT_SERVER = ROOT / "server.py"

TARGETS = ("server", "backend.app")


def main() -> int:
    for name in TARGETS:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        app = getattr(mod, "app", None)
        if app is None:
            continue
        try:
            source = inspect.getsource(mod)
        except (OSError, TypeError) as exc:
            print(f"Could not get source for {name}: {exc}")
            continue
        if len(source) < 5000:
            print(f"Module {name} source too short ({len(source)} bytes); not the full backend.")
            continue
        OUT_APP.write_text(source, encoding="utf-8")
        OUT_SERVER.write_text(
            '"""Uvicorn entry point."""\n\nfrom backend.app_full import app\n\n__all__ = ["app"]\n',
            encoding="utf-8",
        )
        print(f"Recovered {len(source)} bytes to {OUT_APP}")
        print(f"Wrote entry point {OUT_SERVER}")
        return 0
    print(
        "No loaded server module with full source found. "
        "Restore server.py from Cursor Local History (Timeline) on the deleted file."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
