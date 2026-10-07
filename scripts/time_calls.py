"""Read-only: time the DSX GETs used to link a detection version.

Usage: python scripts/time_calls.py <typed value>
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from backend import config  # noqa: E402

typed = sys.argv[1]
user, password = config.parsed_env_credentials()
auth = (user.lower(), password)
headers = {"accept": "application/json", "x-dsx-limit": "1500"}

calls = [
    ("/regulars", {"title": typed, "is_extended": "false"}),
    ("/regulars", {"name": typed, "is_extended": "false"}),
    ("/programs", {"title": typed}),
    ("/programs", {"name": typed}),
    ("/programs", {}),
]
for path, params in calls:
    start = time.time()
    try:
        resp = requests.get(config.join_devops(path), params=params, headers=headers, auth=auth, timeout=40)
        try:
            body = resp.json()
            count = len(body) if isinstance(body, list) else "?"
            first = body[0] if isinstance(body, list) and body else {}
            info = {k: first.get(k) for k in ("title", "name", "physicalId") if isinstance(first, dict) and k in first}
        except ValueError:
            count, info = "-", resp.text[:80]
        print(f"{path:10} {params} -> {resp.status_code} count={count} {info} {time.time() - start:.1f}s")
    except requests.RequestException as exc:
        print(f"{path:10} {params} -> ERROR {type(exc).__name__} after {time.time() - start:.1f}s")
