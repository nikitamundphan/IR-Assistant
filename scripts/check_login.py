"""Smoke test: import the app and try POST /api/login/env (no secrets printed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402

client = TestClient(app)
print("health:", client.get("/api/health").json())
resp = client.post("/api/login/env")
body = resp.json()
if "session_id" in body:
    body["session_id"] = "<redacted>"
print("login/env:", resp.status_code, body)
