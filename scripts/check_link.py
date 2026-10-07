"""Smoke test (read-only): feature / detection linking, as done when you click Confirm & file.

Usage: python scripts/check_link.py [typed detection value ...]
Uses the first saved form's own feature and detection, then re-resolves them from what a user would type:
the object's name and the object's physical id. Nothing is created on DSX.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402

client = TestClient(app)
session_id = client.post("/api/login/env").json()["session_id"]
headers = {"X-Session-Id": session_id}

forms = client.get("/api/saved-ir-forms", headers=headers).json().get("items", [])
if not forms:
    raise SystemExit("no saved forms")
base = forms[0]["fields"]
print("saved form:", forms[0]["title"])
for key in ("feature_eno_id", "feature_name", "detection_level_eno_id", "detection_level_name", "detection_level_title"):
    print(f"  {key}: {base.get(key)}")


def resolve(label: str, **typed: str) -> None:
    payload = {k: v for k, v in base.items() if k not in ("feature_eno_id", "detection_level_eno_id", "feature_name", "detection_level_name", "detection_level_title")}
    payload.update(typed)
    start = time.time()
    resp = client.post("/api/resolve-ir-references", headers=headers, json=payload)
    body = resp.json()
    fields = body.get("fields", {})
    print(f"\n[{label}] {resp.status_code} unresolved={body.get('unresolved')} ({time.time() - start:.1f}s)")
    start = time.time()
    det = client.post(
        "/api/dsx/resolve-detection-level",
        headers=headers,
        json={"detection_level_name": typed.get("detection_level_name", ""), "detection_level_title": typed.get("detection_level_title", "")},
    )
    if typed.get("detection_level_name"):
        print(f"  resolve-detection-level -> {det.status_code} {det.json().get('resolved')} ({time.time() - start:.1f}s)")
    for key in ("feature_eno_id", "feature_name", "detection_level_eno_id", "detection_level_name", "detection_level_title"):
        print(f"  {key}: {fields.get(key)}")


resolve("feature by name", feature_name=base.get("feature_name", ""))
resolve("feature by physical id", feature_name=base.get("feature_eno_id", ""))
resolve("detection by name", detection_level_title=base.get("detection_level_title", ""), detection_level_name=base.get("detection_level_title", ""))
for typed in sys.argv[1:]:
    resolve(f"detection typed {typed}", detection_level_name=typed, detection_level_title=typed)
