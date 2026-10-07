"""Smoke test: resolve a release name to its physical ID through the app.

Usage: python scripts/check_release.py <release name> [more names...]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402

client = TestClient(app)
session_id = client.post("/api/login/env").json()["session_id"]
headers = {"X-Session-Id": session_id}

names = sys.argv[1:]
if not names:
    raise SystemExit("usage: python scripts/check_release.py <release name> [more names...]")
for name in names:
    resp = client.post("/api/dsx/resolve-release", json={"query": name}, headers=headers)
    body = resp.json()
    if resp.status_code == 200:
        print(name, "->", resp.status_code, body["release_fields"])
    else:
        print(name, "->", resp.status_code, str(body)[:300])
