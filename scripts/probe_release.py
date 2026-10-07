"""Probe GET /releases and print a compact summary of each release.

Usage:
  python scripts/probe_release.py title=<release title>
  python scripts/probe_release.py name=REL000853
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from backend import config  # noqa: E402

if len(sys.argv) < 2 or "=" not in sys.argv[1]:
    raise SystemExit("usage: python scripts/probe_release.py <field>=<value>   (e.g. title=<release title>)")
key, val = sys.argv[1].split("=", 1)
params = {"is_extended": "false", key: val}

user, password = config.parsed_env_credentials()
resp = requests.get(
    config.join_devops("/releases"),
    params=params,
    headers={"accept": "application/json", "x-dsx-limit": "1500"},
    auth=(user.lower(), password),
    timeout=90,
)
print("status:", resp.status_code, "params:", params)
body = resp.json()
if not isinstance(body, list):
    print(json.dumps(body)[:800])
    raise SystemExit(0)
for rel in body:
    print("RELEASE", rel.get("title"), rel.get("name"), "physicalId=", rel.get("physicalId"))
    print("  top-level keys:", sorted(k for k, v in rel.items() if isinstance(v, (list, dict))))
    for kind in ("regular", "version"):
        val = rel.get(kind)
        items = val if isinstance(val, list) else ([val] if isinstance(val, dict) else [])
        print(f"  {kind}: {len(items)}")
        for it in items[-6:]:
            print("    ", it.get("title"), "|", it.get("name"), "|", it.get("suffix"), "|", it.get("physicalId"))
