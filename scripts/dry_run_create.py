"""Dry run (nothing is created): build the POST /incidentfamilies request for an edited saved form.

Usage: python scripts/dry_run_create.py [detection value to type, default: the saved one]
Reads the first saved form, applies a typed detection value, resolves ids/names like the chat does,
then prints the multipart request that create_incident_report would send. No POST is made.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from backend import config  # noqa: E402
from backend.dsx_http import fetch_formtemplates  # noqa: E402
from backend.dsx_resolve import resolve_form_templates  # noqa: E402
from backend.ir_create import REQUIRED_FIELDS, prepare_ir_payload  # noqa: E402
from backend.sessions import create_session  # noqa: E402

user, password = config.parsed_env_credentials()
dsx = create_session(user, password)

items, _hint, _probe = fetch_formtemplates(dsx)
form = resolve_form_templates(dsx, items)[0]
fields = dict(form["fields"])
fields.update({"title": "DRY RUN title", "description": "DRY RUN description", "severity": fields.get("severity") or "1"})
if len(sys.argv) > 1:
    for key in ("detection_level_eno_id", "detection_level_name", "detection_level_title"):
        fields.pop(key, None)
    fields["detection_level_name"] = sys.argv[1]
    fields["detection_level_title"] = sys.argv[1]

if len(sys.argv) > 2:  # simulate a stale target release that is really a version id
    fields["rel_eno_id"] = sys.argv[2]
    for key in ("rel_name", "rel_title", "rel_level_id"):
        fields.pop(key, None)

data = prepare_ir_payload(dsx, fields, for_create=True)
missing = [k for k in REQUIRED_FIELDS if not data.get(k)]
print("missing required:", missing)
for key, value in data.items():
    print(f"  {key}: {value[:70]!r}")

prepared = requests.Request(
    "POST",
    config.join_devops("/incidentfamilies"),
    files={k: (None, v) for k, v in data.items()},
).prepare()
print("\nContent-Type:", prepared.headers["Content-Type"])
print("body starts:", prepared.body[:160])
print("NOT SENT (dry run)")
