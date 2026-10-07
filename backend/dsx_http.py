"""HTTP calls against Coal Porter / UI REST."""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from fastapi import HTTPException

from backend.config import (
    DSX_BASE_URL,
    DSX_CREATION_FORMS_PATH,
    DSX_PARENT_BRAND_NAME,
    DSX_PRODUCT_SERVICES_PATH,
    DSX_PROGRAMS_PATH,
    DSX_PROGRAMS_SERVICE_PARAM,
    DSX_RELEASES_PATH,
    DSX_RELEASES_PROGRAM_PARAM,
    DSX_SERVICES_PATH,
    join_devops,
    join_ui,
)
from backend.dsx_util import (
    ProbeLog,
    extract_items,
    normalize_catalog_items,
    normalize_list_item,
    pick_id,
    pick_name,
    pick_title,
)
from backend.sessions import DsxSession


def _request(
    dsx: DsxSession,
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    data: Any = None,
    json_body: Any = None,
    files: Any = None,
    timeout: int = 90,
) -> requests.Response:
    try:
        return dsx.http.request(
            method,
            url,
            params=params,
            data=data,
            json=json_body,
            files=files,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail={"error": "DSX request failed", "details": str(exc)}) from exc


def _json_or_raise(resp: requests.Response) -> Any:
    if resp.status_code >= 400:
        detail: Any
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:500]
        raise HTTPException(
            status_code=resp.status_code if 400 <= resp.status_code < 600 else 502,
            detail={"error": f"DSX HTTP {resp.status_code}", "details": detail},
        )
    if not resp.content:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text}


def get_json(dsx: DsxSession, url: str, params: dict[str, Any] | None = None, probe: ProbeLog | None = None) -> Any:
    resp = _request(dsx, url, params=params)
    if probe is not None:
        count = len(extract_items(_safe_json(resp)))
        probe.add(url.replace(DSX_BASE_URL, ""), params, resp.status_code, count)
    return _json_or_raise(resp)


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {}


def probe_paths(
    dsx: DsxSession,
    paths: list[str],
    param_sets: list[dict[str, Any]],
    probe: ProbeLog,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in paths:
        for params in param_sets:
            if len(probe.entries) >= probe.max_attempts:
                break
            url = join_devops(path)
            resp = _request(dsx, url, params=params)
            body = _safe_json(resp)
            chunk = normalize_catalog_items(body) if resp.status_code < 400 else []
            probe.add(path, params, resp.status_code, len(chunk))
            if chunk:
                items = chunk
                return items
    return items


def fetch_brands(dsx: DsxSession) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    probe = ProbeLog()
    paths = [DSX_SERVICES_PATH, "/brands"]
    items = probe_paths(dsx, paths, [{}], probe)
    hint = "" if items else "No brands returned from DSX."
    return items, hint, probe.summary()


def _match_parent_brand(brands: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not DSX_PARENT_BRAND_NAME:
        return None
    target = DSX_PARENT_BRAND_NAME.lower()
    for brand in brands:
        label = (brand.get("title") or brand.get("name") or "").lower()
        if label == target or target in label:
            return brand
    return None


def fetch_services(dsx: DsxSession, brand_id: str = "") -> dict[str, Any]:
    probe = ProbeLog()
    brands, _, brand_probe = fetch_brands(dsx)
    probe.entries.extend(brand_probe)

    parent = None
    if brand_id:
        parent = next((b for b in brands if b.get("id") == brand_id), None)
    elif DSX_PARENT_BRAND_NAME:
        parent = _match_parent_brand(brands)

    param_sets: list[dict[str, Any]] = [{}]
    if parent and parent.get("id"):
        param_sets = [{"brand": parent["id"]}, {"brand_id": parent["id"]}]

    paths = [DSX_PRODUCT_SERVICES_PATH, DSX_SERVICES_PATH, "/services", "/brands"]
    items = probe_paths(dsx, paths, param_sets, probe)

    services_unavailable = not items and bool(parent)
    hint = ""
    if not items:
        hint = (
            "No product services returned. Check DSX_PRODUCT_SERVICES_PATH and DSX_PARENT_BRAND_NAME."
            if parent
            else "Pick a brand or set DSX_PARENT_BRAND_NAME."
        )

    return {
        "items": items,
        "parent_brand": parent,
        "hint": hint,
        "probe": probe.summary(),
        "probe_summary": probe.summary(),
        "services_unavailable": services_unavailable,
    }


def fetch_programs(dsx: DsxSession, *, service_id: str = "", brand_id: str = "") -> dict[str, Any]:
    probe = ProbeLog()
    param_sets: list[dict[str, Any]] = []
    if service_id:
        param_sets.append({DSX_PROGRAMS_SERVICE_PARAM: service_id})
        param_sets.append({"service_id": service_id})
    if brand_id:
        param_sets.append({"brand": brand_id})
        param_sets.append({"brand_id": brand_id})
    if not param_sets:
        param_sets = [{}]

    paths = [DSX_PROGRAMS_PATH, "/programs"]
    items = probe_paths(dsx, paths, param_sets, probe)
    hint = "" if items else "No programs found for the given scope."
    return {"items": items, "hint": hint, "probe": probe.summary(), "probe_summary": probe.summary()}


def fetch_releases(dsx: DsxSession, program_id: str) -> dict[str, Any]:
    probe = ProbeLog()
    path = DSX_RELEASES_PATH.format(program_id=program_id)
    param_sets = [
        {DSX_RELEASES_PROGRAM_PARAM: program_id},
        {"program_id": program_id},
        {},
    ]
    paths = [path, "/releases"]
    items = probe_paths(dsx, paths, param_sets, probe)
    hint = "" if items else "No releases found for this program."
    return {"items": items, "hint": hint, "probe": probe.summary()}


def search_releases_global(dsx: DsxSession, query: str) -> tuple[list[dict[str, Any]], ProbeLog]:
    """Resolve the name the user typed to an object (version first, then release).

    Looks in /regulars (a specific version, e.g. "Service.1.58.6") and then /releases, using
    GET <endpoint>?title=<pattern>&is_extended=false. The server filter is case-sensitive and
    supports `*` and `?`, so the typed text is turned into a wildcard pattern (separators -> `*`,
    and on a second try letters -> `?`) and the candidates are compared case-insensitively here.
    A release code is also tried as `name`. Nothing about the text is hard-coded.
    """
    probe = ProbeLog()
    q = query.strip()
    wanted = _compact(q)
    if not wanted:
        return [], probe

    attempts: list[tuple[str, str, str]] = []
    patterns = [_wildcard(q, any_case=False), _wildcard(q, any_case=True)]
    for pattern in dict.fromkeys(patterns):
        for endpoint in ("/regulars", "/releases"):
            attempts.append((endpoint, "title", pattern))
    for endpoint in ("/regulars", "/releases"):
        attempts.append((endpoint, "name", q))

    for endpoint, field, value in attempts:
        if len(probe.entries) >= probe.max_attempts:
            break
        params = {field: value, "is_extended": "false"}
        resp = _request(dsx, join_devops(endpoint), params=params)
        body = _safe_json(resp)
        items = normalize_catalog_items(body) if resp.status_code < 400 else []
        probe.add(endpoint, params, resp.status_code, len(items))
        matches = [i for i in items if wanted in (_compact(i["title"]), _compact(i["name"]))]
        if matches:
            return matches, probe
    return [], probe


def _compact(text: str) -> str:
    """Lowercase letters/digits only, so 'X-1.58.6' and 'x.1.58.6' compare equal."""
    return re.sub(r"[^0-9a-z]", "", (text or "").lower())


def _wildcard(text: str, *, any_case: bool) -> str:
    """Build a server-side title pattern: separators become `*`; letters become `?` if any_case."""
    out: list[str] = []
    for ch in text:
        if ch.isalnum():
            out.append("?" if any_case and ch.isalpha() else ch)
        elif not out or out[-1] != "*":
            out.append("*")
    return "".join(out)


def fetch_release_by_id(dsx: DsxSession, release_ref: str) -> Optional[dict[str, Any]]:
    """Fetch one release by physical id or reference via GET /releases/{id}."""
    ref = release_ref.strip()
    if not ref:
        return None
    variants = [ref]
    if re.fullmatch(r"[0-9A-Fa-f]{32}", ref):
        variants = [ref.upper(), ref]
    for variant in variants:
        resp = _request(dsx, join_devops(f"/releases/{variant}"))
        if resp.status_code != 200:
            continue
        body = _safe_json(resp)
        if isinstance(body, dict):
            if pick_id(body) or pick_title(body):
                return normalize_list_item(body)
            return normalize_list_item({**body, "id": variant})
    return None


def fetch_by_eno_id(dsx: DsxSession, endpoint: str, eno_id: str) -> Optional[dict[str, Any]]:
    """GET <endpoint>?eno_id=<dotted id> (works on /releases, /programs, /features)."""
    params: dict[str, Any] = {"eno_id": eno_id}
    if endpoint == "/releases":
        params["is_extended"] = "false"
    resp = _request(dsx, join_devops(endpoint), params=params)
    if resp.status_code >= 400:
        return None
    items = normalize_catalog_items(_safe_json(resp))
    return items[0] if items else None


def fetch_features(dsx: DsxSession, query: str) -> list[dict[str, Any]]:
    probe = ProbeLog()
    q = query.strip()
    for params in ({"q": q}, {"search": q}, {"name": q}):
        if len(probe.entries) >= probe.max_attempts:
            break
        resp = _request(dsx, join_devops("/features"), params=params)
        body = _safe_json(resp)
        chunk = normalize_catalog_items(body) if resp.status_code < 400 else []
        probe.add("/features", params, resp.status_code, len(chunk))
        if chunk:
            return chunk
    return []


def fetch_program_by_name(dsx: DsxSession, name: str, *, service_id: str = "", brand_id: str = "") -> Optional[dict[str, Any]]:
    body = fetch_programs(dsx, service_id=service_id, brand_id=brand_id)
    from backend.dsx_util import best_match

    return best_match(name, body.get("items") or [])


def fetch_formtemplates(dsx: DsxSession) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    probe = ProbeLog()
    if DSX_CREATION_FORMS_PATH:
        path = DSX_CREATION_FORMS_PATH
        if path.startswith("http"):
            url = path
        else:
            url = join_ui(path if path.startswith("/") else f"/{path}")
        resp = _request(dsx, url)
        body = _safe_json(resp)
        probe.add(path, {}, resp.status_code, len(extract_items(body)))
        items = _parse_formtemplates(body)
        hint = "" if items else "No form templates returned for DSX_CREATION_FORMS_PATH."
        return items, hint, probe.summary()

    # The signed-in user's current templates (GET /formtemplates?owner=<user>&current=true).
    for params in ({"owner": dsx.username, "current": "true"}, {"owner": dsx.username}):
        resp = _request(dsx, join_ui("/formtemplates"), params=params)
        body = _safe_json(resp)
        items = _parse_formtemplates(body) if resp.status_code < 400 else []
        probe.add("/formtemplates", params, resp.status_code, len(items))
        if items:
            return items, "", probe.summary()
    return [], "No saved IR form templates found on DSX UI REST.", probe.summary()


def _parse_formtemplates(body: Any) -> list[dict[str, Any]]:
    raw_items = extract_items(body)
    if not raw_items and isinstance(body, dict):
        raw_items = [body]
    forms: list[dict[str, Any]] = []
    for raw in raw_items:
        fid = pick_id(raw) or str(raw.get("templateId") or raw.get("uuid") or "")
        title = pick_title(raw) or "Saved form"
        description = str(raw.get("description") or raw.get("summary") or "").strip()
        fields = _extract_template_fields(raw)
        if not fid and not fields:
            continue
        forms.append(
            {
                "id": fid or title,
                "title": title,
                "description": description,
                "fields": fields,
            }
        )
    return forms


# DSX form-template field name -> IR field. Template values come as {"<name>": {"value": ...}}.
TEMPLATE_FIELD_MAP = {
    "Abstract": "title",
    "Description": "description",
    "ECRSeverity": "severity",
    "DetectedEnvironment": "detected_environment",
    "TargetReleaseOID": "rel_eno_id",
    "ClassificationElementOID": "feature_eno_id",
    "DetectionProgramOID": "detection_level_eno_id",
    "clarifier": "default_clarifier",
    "directAssignee": "default_corrector",
    "validator": "default_validator",
    "owner": "owner",
}


def _extract_template_fields(raw: dict[str, Any]) -> dict[str, str]:
    """Read the template's `fields` (a JSON string or dict) into IR field names."""
    block = raw.get("fields")
    if isinstance(block, str):
        try:
            block = json.loads(block)
        except ValueError:
            block = {}
    if not isinstance(block, dict):
        return {}
    fields: dict[str, str] = {}
    for name, ir_key in TEMPLATE_FIELD_MAP.items():
        entry = block.get(name)
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value is not None and str(value).strip():
            fields[ir_key] = str(value).strip()
    return fields


def list_incident_families(dsx: DsxSession, *, owner: str = "", q: str = "", feature: str = "") -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if owner:
        params["owner"] = owner
    if q:
        params["q"] = q
        params["search"] = q
        params["title"] = q
    if feature:
        params["feature"] = feature
        params["feature_eno_id"] = feature
    url = join_devops("/incidentfamilies")
    resp = _request(dsx, url, params=params)
    body = _json_or_raise(resp)
    items = extract_items(body)
    results: list[dict[str, Any]] = []
    for item in items:
        iid = pick_id(item) or str(item.get("name") or "")
        title = pick_title(item)
        from backend.config import ir_object_url

        results.append(
            {
                "id": iid,
                "title": title,
                "name": pick_name(item),
                "url": ir_object_url(iid),
                "raw": item,
            }
        )
    return results
