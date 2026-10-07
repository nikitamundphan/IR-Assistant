"""Release, feature, and detection resolution for IR filing."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from backend.config import navigator_url
from concurrent.futures import ThreadPoolExecutor

from backend.dsx_http import (
    fetch_by_eno_id,
    fetch_by_physical_id,
    fetch_features,
    fetch_program_by_name,
    fetch_programs,
    fetch_release_by_id,
    fetch_releases,
    search_by_title,
    search_releases_global,
)
from backend.dsx_util import ENO_OID_PATTERN, PHYSICAL_ID_PATTERN, normalize_list_item, pick_id, pick_name, pick_title
from backend.sessions import DsxSession

# Where a typed "issue detected version" is looked up. The IR itself needs a program, so programs
# come first and a version found in /regulars is mapped to its program (program_of_version).
DETECTION_ENDPOINTS = ("/programs", "/regulars")


def release_fields_from_item(item: dict[str, Any]) -> dict[str, str]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    rel_id = pick_id(item) or pick_id(raw)
    rel_name = pick_name(item) or pick_name(raw)
    rel_title = pick_title(item) or pick_title(raw)
    rel_level = str(
        raw.get("rel_level_id")
        or raw.get("level_id")
        or raw.get("levelId")
        or rel_name
        or ""
    ).strip()
    return {
        "rel_eno_id": rel_id,
        "rel_name": rel_name,
        "rel_title": rel_title,
        "rel_level_id": rel_level,
    }


def program_of_version(item: dict[str, Any]) -> dict[str, Any]:
    """DSX only accepts a program as the IR's detected level. For a version (/regulars item), use
    the program embedded in it (e.g. version X.1.11.6 -> program titled X-1.11.6)."""
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    if str(raw.get("type") or "") != "DSXRel_Regular":
        return item
    programs = raw.get("program")
    if isinstance(programs, dict):
        programs = [programs]
    for prog in programs or []:
        if isinstance(prog, dict) and pick_id(prog):
            return normalize_list_item(prog)
    return item


def detection_fields_from_program(program: dict[str, Any]) -> dict[str, str]:
    program = program_of_version(program)
    raw = program.get("raw") if isinstance(program.get("raw"), dict) else program
    pid = pick_id(program) or pick_id(raw)
    pname = pick_name(program) or pick_name(raw)
    ptitle = pick_title(program) or pick_title(raw)
    return {
        "detection_level_eno_id": pid,
        "detection_level_name": pname or ptitle,
        "detection_level_title": ptitle or pname,
    }


def resolve_release_by_query(dsx: DsxSession, query: str) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")

    items, probe = search_releases_global(dsx, q)
    match = items[0] if items else None  # already filtered by title/name on the server
    if not match:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"No release matched '{q}' on DSX.",
                "probe": probe.summary(),
            },
        )

    # A typed version (e.g. "Service.1.11.6") is a /regulars object, not a release: the IR's target
    # release is the release that version belongs to, and the version itself is the detected version.
    version = None
    parent = parent_release_of_version(match)
    if parent:
        version = match
        release_fields = release_fields_for_parent(dsx, parent)
    else:
        release_fields = release_fields_from_item(match)
    rel_id = release_fields.get("rel_eno_id") or ""
    result = {
        "resolved": True,
        "query": q,
        "release": {
            "id": rel_id,
            "title": release_fields.get("rel_title"),
            "name": release_fields.get("rel_name"),
        },
        "release_fields": release_fields,
        "navigator_url": navigator_url(rel_id) or None,
        "probe": probe.summary(),
    }
    if version:
        result["detection_fields"] = detection_fields_from_program(version)
        result["version"] = {"id": pick_id(version), "title": pick_title(version), "name": pick_name(version)}
    return result


def parent_release_of_version(version: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The release object embedded in a /regulars item (None if the item is not a version)."""
    raw = version.get("raw") if isinstance(version.get("raw"), dict) else version
    release = raw.get("release")
    if isinstance(release, list):
        release = release[0] if release else None
    return release if isinstance(release, dict) and pick_id(release) else None


def release_fields_for_parent(dsx: DsxSession, parent: dict[str, Any]) -> dict[str, str]:
    """Release fields for a version's parent release, read from GET /releases/{id} when possible."""
    item = fetch_release_by_id(dsx, pick_id(parent))
    return release_fields_from_item(item or parent)


def swap_version_for_release(dsx: DsxSession, fields: dict[str, Any]) -> dict[str, Any]:
    """If rel_eno_id is really a version's id, use that version's release (DSX rejects non-releases)."""
    out = dict(fields)
    rel_id = str(out.get("rel_eno_id") or "").strip()
    if not PHYSICAL_ID_PATTERN.match(rel_id):
        return out
    version = fetch_by_physical_id(dsx, ("/regulars",), rel_id)
    parent = parent_release_of_version(version) if version else None
    if not parent:
        return out
    out.update(release_fields_for_parent(dsx, parent))
    if not out.get("detection_level_eno_id"):
        out.update(detection_fields_from_program(version))
    return out


def enrich_release_fields(dsx: DsxSession, fields: dict[str, str]) -> dict[str, str]:
    """Fill rel_name / rel_title / rel_level_id from GET /releases/{id} when only rel_eno_id is known."""
    out = dict(fields)
    rel_id = str(out.get("rel_eno_id") or "").strip()
    if not rel_id:
        return out
    needs_name = not out.get("rel_name") or not out.get("rel_title") or not out.get("rel_level_id")
    if not needs_name:
        return out
    item = fetch_release_by_id(dsx, rel_id)
    if not item:
        return out
    enriched = release_fields_from_item(item)
    for key, val in enriched.items():
        if val and not out.get(key):
            out[key] = val
    return out


def resolve_release_context(
    dsx: DsxSession,
    *,
    service_id: str,
    service_name: str,
    program_id: str,
    program_name: str,
    release_id: str,
) -> dict[str, Any]:
    if not release_id:
        raise HTTPException(status_code=400, detail="release_id is required")

    releases_body = fetch_releases(dsx, program_id)
    release_item = next((r for r in releases_body.get("items") or [] if r.get("id") == release_id), None)
    if not release_item:
        release_item = {"id": release_id, "title": release_id, "name": release_id}

    programs_body = fetch_programs(dsx, service_id=service_id)
    program_item = next((p for p in programs_body.get("items") or [] if p.get("id") == program_id), None)
    if not program_item:
        program_item = {"id": program_id, "title": program_name, "name": program_name}

    release_fields = release_fields_from_item(release_item)
    detection_fields = detection_fields_from_program(program_item)

    return {
        "service_id": service_id,
        "service_name": service_name,
        "program_id": program_id,
        "program_name": program_name,
        "release": {
            "id": release_fields.get("rel_eno_id"),
            "title": release_fields.get("rel_title"),
            "name": release_fields.get("rel_name"),
        },
        "release_fields": release_fields,
        "detection_fields": detection_fields,
    }


def resolve_detection_level(
    dsx: DsxSession,
    *,
    detection_level_name: str = "",
    detection_level_title: str = "",
    service_id: str = "",
    brand_id: str = "",
) -> dict[str, Any]:
    query = (detection_level_name or detection_level_title or "").strip()
    fields: dict[str, str] = {}
    resolved = False

    if query:
        # The detected version is a /regulars object (or a program). A typed physical id is looked
        # up directly; anything else is matched by the title/name the user typed.
        match = fetch_by_physical_id(dsx, DETECTION_ENDPOINTS, query)
        if not match:
            found, _probe = search_by_title(dsx, DETECTION_ENDPOINTS, query)
            match = found[0] if found else None
        if not match:
            try:  # scoped program list is slow on DSX; a failure here just means "not found"
                match = fetch_program_by_name(dsx, query, service_id=service_id, brand_id=brand_id)
            except HTTPException:
                match = None
        if match:
            fields = detection_fields_from_program(match)
            resolved = bool(fields.get("detection_level_eno_id"))

    if not resolved and query:
        fields = {
            "detection_level_name": detection_level_name or query,
            "detection_level_title": detection_level_title or detection_level_name or query,
        }

    return {"fields": fields, "resolved": resolved}


def resolve_eno_ids(dsx: DsxSession, fields: dict[str, Any]) -> dict[str, Any]:
    """Turn dotted ENOVIA ids (34152.36236...) into physical ids plus names via GET ?eno_id=."""
    out = dict(fields)

    ref = str(out.get("rel_eno_id") or "")
    if ENO_OID_PATTERN.match(ref):
        item = fetch_by_eno_id(dsx, "/releases", ref)
        if item:
            out.update(release_fields_from_item(item))

    ref = str(out.get("feature_eno_id") or "")
    if ENO_OID_PATTERN.match(ref):
        item = fetch_by_eno_id(dsx, "/features", ref)
        if item:
            out["feature_eno_id"] = pick_id(item)
            out["feature_name"] = pick_title(item)

    ref = str(out.get("detection_level_eno_id") or "")
    if ENO_OID_PATTERN.match(ref):
        for endpoint in DETECTION_ENDPOINTS:
            item = fetch_by_eno_id(dsx, endpoint, ref)
            if item:
                out.update(detection_fields_from_program(item))
                break
    return out


def resolve_form_templates(dsx: DsxSession, forms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill physical ids and names on each saved form (templates only store dotted ids)."""
    if not forms:
        return forms
    with ThreadPoolExecutor(max_workers=min(len(forms), 6)) as pool:
        resolved = list(pool.map(lambda f: resolve_eno_ids(dsx, f.get("fields") or {}), forms))
    return [{**form, "fields": fields} for form, fields in zip(forms, resolved)]


def resolve_ir_references(dsx: DsxSession, payload: dict[str, Any]) -> dict[str, Any]:
    fields = swap_version_for_release(dsx, resolve_eno_ids(dsx, payload))
    service_id = str(payload.get("service_id") or "").strip()
    brand_id = str(payload.get("brand_id") or "").strip()

    if not fields.get("rel_eno_id"):
        rel_query = str(payload.get("rel_title") or payload.get("rel_name") or "").strip()
        if rel_query:
            try:
                resolved = resolve_release_by_query(dsx, rel_query)
                fields.update(resolved.get("release_fields") or {})
            except HTTPException:
                pass

    if not fields.get("feature_eno_id"):
        feature_query = str(payload.get("feature_name") or "").strip()
        if feature_query:
            feat = fetch_by_physical_id(dsx, ("/features",), feature_query)
            if not feat:
                features = fetch_features(dsx, feature_query)
                feat = features[0] if features else None
            if feat:
                fields["feature_eno_id"] = pick_id(feat)
                fields["feature_name"] = pick_title(feat) or pick_name(feat)

    if not fields.get("detection_level_eno_id"):
        det = resolve_detection_level(
            dsx,
            detection_level_name=str(payload.get("detection_level_name") or ""),
            detection_level_title=str(payload.get("detection_level_title") or ""),
            service_id=service_id,
            brand_id=brand_id,
        )
        fields.update(det.get("fields") or {})

    # The physical id is the source of truth: the API needs the matching names too, so always read
    # them from the object itself instead of trusting names left over from the saved form.
    feature_id = str(fields.get("feature_eno_id") or "")
    if PHYSICAL_ID_PATTERN.match(feature_id):
        feat = fetch_by_physical_id(dsx, ("/features",), feature_id)
        if feat:
            fields["feature_name"] = pick_title(feat) or pick_name(feat)
    detection_id = str(fields.get("detection_level_eno_id") or "")
    if PHYSICAL_ID_PATTERN.match(detection_id):
        det_item = fetch_by_physical_id(dsx, DETECTION_ENDPOINTS, detection_id)
        if det_item:
            fields.update(detection_fields_from_program(det_item))

    fields = enrich_release_fields(dsx, {k: str(v) for k, v in fields.items() if v is not None and str(v).strip()})
    return {"fields": fields}
