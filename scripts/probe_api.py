"""Generic Coal Porter probe.

Usage:
  python scripts/probe_api.py paths [substring]          # list OpenAPI paths (+ query params)
  python scripts/probe_api.py get <path> [key=value ...]  # GET on DSX_BASE_URL (devops)
  python scripts/probe_api.py getui <path> [key=value ...] # GET on the UI REST base
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from backend import config  # noqa: E402

user, password = config.parsed_env_credentials()
auth = (user.lower(), password)
headers = {"accept": "application/json", "x-dsx-limit": "1500"}

if len(sys.argv) < 2:
    raise SystemExit(__doc__)

if sys.argv[1] == "paths":
    needle = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    spec = requests.get(config.join_devops("/api-docs"), headers=headers, auth=auth, timeout=90).json()
    for path, ops in spec.get("paths", {}).items():
        if needle and needle not in path.lower():
            continue
        get = ops.get("get")
        if get:
            params = [p["name"] for p in get.get("parameters", []) if p.get("in") == "query"]
            print(path, "->", params)
elif sys.argv[1] in ("get", "getui"):
    path = sys.argv[2]
    params = dict(a.split("=", 1) for a in sys.argv[3:])
    url = config.join_ui(path) if sys.argv[1] == "getui" else config.join_devops(path)
    print("url:", url)
    resp = requests.get(url, params=params, headers=headers, auth=auth, timeout=90)
    print("status:", resp.status_code, params)
    try:
        body = resp.json()
    except ValueError:
        print(resp.text[:500])
        raise SystemExit(0)
    items = body if isinstance(body, list) else [body]
    print("count:", len(items))
    for it in items[:5]:
        if isinstance(it, dict):
            print({k: it.get(k) for k in ("type", "title", "name", "suffix", "physicalId", "id") if k in it})
    if len(items) == 1 and isinstance(items[0], dict):
        print("--- all keys (values truncated) ---")
        for k, v in items[0].items():
            print(f"{k}: {json.dumps(v)[:300]}")
