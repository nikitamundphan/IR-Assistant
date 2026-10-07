"""Smoke test: GET /api/saved-ir-forms through the app (after env login)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402

client = TestClient(app)
session_id = client.post("/api/login/env").json()["session_id"]
resp = client.get("/api/saved-ir-forms", headers={"X-Session-Id": session_id})
body = resp.json()
print("status:", resp.status_code, "hint:", body.get("hint"))
for form in body.get("items", []):
    print(form["id"], "|", form["title"], "| fields:", sorted(form.get("fields", {}).keys()))
print("probe:", body.get("probe"))
