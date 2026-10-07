"""Smoke test: resolve target release names through the app (after env login).

Usage: python scripts/check_release.py <name> [<name> ...]
A typed version (e.g. Service-1.11.6) should give its parent release plus detection_fields.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit(__doc__)

client = TestClient(app)
session_id = client.post("/api/login/env").json()["session_id"]
headers = {"X-Session-Id": session_id}
for name in sys.argv[1:]:
    resp = client.post("/api/dsx/resolve-release", headers=headers, json={"query": name})
    body = resp.json()
    print(f"\n{name} -> {resp.status_code}")
    if resp.status_code == 200:
        print("  release_fields:", body.get("release_fields"))
        print("  detection_fields:", body.get("detection_fields"))
    else:
        print(" ", str(body)[:300])
