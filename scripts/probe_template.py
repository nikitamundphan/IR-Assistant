"""Print the parsed `fields` of a saved form template: python scripts/probe_template.py <physicalId>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from backend import config  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit(__doc__)
user, password = config.parsed_env_credentials()
resp = requests.get(
    config.join_ui(f"/formtemplates/{sys.argv[1]}"),
    headers={"accept": "application/json", "x-dsx-limit": "1500"},
    auth=(user.lower(), password),
    timeout=60,
)
fields = json.loads(resp.json()["fields"])
for key, val in fields.items():
    shown = val.get("value") if isinstance(val, dict) else val
    print(f"{key}: {str(shown)[:110]}")
