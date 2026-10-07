"""Read-only: which programs belong to a version (regular)?

Usage: python scripts/probe_programs.py <regular physicalId> [reg_name]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from backend import config  # noqa: E402

user, password = config.parsed_env_credentials()
auth = (user.lower(), password)
headers = {"accept": "application/json", "x-dsx-limit": "1500"}


def get(path, params=None):
    resp = requests.get(config.join_devops(path), params=params or {}, headers=headers, auth=auth, timeout=60)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text[:200]


def show(label, items):
    print(label)
    for it in items if isinstance(items, list) else [items]:
        if isinstance(it, dict):
            print("  ", {k: it.get(k) for k in ("type", "name", "title", "state", "physicalId", "id")})


status, reg = get(f"/regulars/{sys.argv[1]}")
print("regular:", status, reg.get("title"), reg.get("name"), "dotted id:", reg.get("id"))
show("embedded program list:", reg.get("program") or [])
for key, value in (("reg_eno_id", reg.get("id")), ("reg_name", reg.get("name")), ("reg_title", reg.get("title"))):
    status, body = get("/programs", {key: value})
    print(f"\n/programs?{key}={value} -> {status}")
    if status == 200:
        show("", body)
if len(sys.argv) > 2:
    status, body = get("/programs", {"name": sys.argv[2]})
    print(f"\n/programs?name={sys.argv[2]} -> {status}")
    if status == 200:
        show("", body)
