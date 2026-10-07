"""Read-only probe: where does a physical id live, and what does POST /incidentfamilies accept?

Usage:
  python scripts/probe_ids.py spec                 # POST /incidentfamilies form fields (from api-docs)
  python scripts/probe_ids.py id <physicalId>      # try GET /<endpoint>/<id> and ?uuid=<id> on several endpoints
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

if sys.argv[1] == "spec":
    spec = None
    for cand in ("/api-docs", "/v3/api-docs", "/api-docs/swagger.json", "/swagger.json", "/openapi.json", "/api-docs.json", "/api-docs/openapi.json"):
        r = requests.get(config.join_devops(cand), headers=headers, auth=auth, timeout=90)
        ctype = r.headers.get("content-type", "")
        print(cand, r.status_code, ctype[:40])
        try:
            data = r.json()
        except ValueError:
            if cand == "/api-docs":
                import re

                print("  html refs:", re.findall(r"""["']([^"']*(?:json|yaml|swagger)[^"']*)["']""", r.text)[:10])
            continue
        if isinstance(data, dict) and data.get("paths"):
            spec = data
            break
    if not spec:
        raise SystemExit("no JSON spec found")
    post = spec.get("paths", {}).get("/incidentfamilies", {}).get("post")
    if not post:
        raise SystemExit("no POST /incidentfamilies in api-docs")
    print("consumes:", post.get("consumes"))
    for p in post.get("parameters", []):
        print(f"{p.get('name')!s:28} in={p.get('in')!s:9} required={p.get('required')} type={p.get('type')} {str(p.get('description', ''))[:90]}")
    if post.get("requestBody"):
        schema = post["requestBody"]["content"]["multipart/form-data"]["schema"]
        print("schema required:", schema.get("required"))
        for name, prop in schema.get("properties", {}).items():
            print(f"{name:26} {prop.get('type')!s:8} {prop.get('description', '')}")
elif sys.argv[1] == "paths":
    needle = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    spec = requests.get(config.join_devops("/openapi.json"), headers=headers, auth=auth, timeout=90).json()
    for path, ops in spec.get("paths", {}).items():
        get = ops.get("get")
        if get and needle in path.lower():
            print(path, "->", [p["name"] for p in get.get("parameters", []) if p.get("in") == "query"])
elif sys.argv[1] == "id":
    pid = sys.argv[2]
    for endpoint in ("/features", "/programs", "/regulars", "/releases"):
        for label, url, params in (
            ("path", config.join_devops(f"{endpoint}/{pid}"), {}),
            ("uuid", config.join_devops(endpoint), {"uuid": pid}),
        ):
            if endpoint == "/releases":
                params = {**params, "is_extended": "false"}
            resp = requests.get(url, params=params, headers=headers, auth=auth, timeout=90)
            summary = ""
            try:
                body = resp.json()
                items = body if isinstance(body, list) else [body]
                first = items[0] if items and isinstance(items[0], dict) else {}
                summary = f"count={len(items)} " + str({k: first.get(k) for k in ("type", "title", "name", "physicalId", "id") if k in first})
            except ValueError:
                summary = resp.text[:100]
            print(f"{endpoint:10} {label:5} -> {resp.status_code} {summary}")
