"""DSX Coal Porter and UI REST operations."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests
from fastapi import HTTPException

from backend.config import Settings, get_settings
from backend.dsx_util import (
    ProbeRecorder,
    best_match,
    detection_fields_from_program,
    extract_items,
    format_dsx_error,
    navigator_url,
    normalize_catalog_item,
    pick_str,
    probe_get_items,
    release_fields_from_item,
    request_json,
    score_release_match,
)
from backend.sessions import DsxSession

logger = logging.getLogger(__name__)

REQUIRED_CREATE_KEYS = [
    "title",
    "description",
    "severity",
    "detected_environment",
    "rel_eno_id",
    "feature_eno_id",
    "detection_level_eno_id",
    "default_clarifier",
    "default_corrector",
    "default_validator",
]

CREATE_FORM_MAP = {
    "title": ("title",),
    "description": ("description",),
    "severity": ("severity",),
    "detected_environment": ("detectedEnvironment", "detected_environment"),
    "rel_eno_id": ("relEnoId", "rel_eno_id"),
    "feature_eno_id": ("featureEnoId", "feature_eno_id"),
    "detection_level_eno_id": ("detectionLevelEnoId", "detection_level_eno_id"),
    "default_clarifier": ("defaultClarifier", "default_clarifier"),
    "default_corrector": ("defaultCorrector", "default_corrector"),
    "default_validator": ("defaultValidator", "default_validator"),
    "owner": ("owner",),
}


def validate_dsx_login(session: DsxSession) -> None:
    settings = get_settings()
    paths = ["/hello", "/incidentfamilies", "/releases"]
    last_error = "Could not validate DSX credentials"
    for path in paths:
        status, body = request_json(session.http, settings.dsx_base_url, path, params={"limit": 1})
        if status in (200, 201, 204):
            return
        if status == 401:
            raise HTTPException(status_code=401, detail="Invalid DSX username or API key")
        last_error = format_dsx_error(status, body)
    raise HTTPException(status_code=502, detail=last_error)


def _parent_brand_attempts(settings: Settings) -> list[tuple[str, dict[str, Any] | None]]:
    path = settings.dsx_services_path or "/brands"
    return [
        (path, None),
        (path, {"limit": 200}),
        ("/brands", None),
    ]


def list_brands(session: DsxSession) -> dict[str, Any]:
    settings = get_settings()
    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    items, entries = probe_get_items(
        session.http,
        settings,
        settings.dsx_base_url,
        _parent_brand_attempts(settings),
        recorder=probe,
    )
    hint = "" if items else "No brands returned from DSX. Check DSX_SERVICES_PATH."
    return {"items": items, "hint": hint, "probe": entries}


def _find_parent_brand(session: DsxSession, settings: Settings) -> dict[str, Any] | None:
    if not settings.dsx_parent_brand_name:
        return None
    body = list_brands(session)
    target = settings.dsx_parent_brand_name.lower()
    for item in body.get("items") or []:
        title = pick_str(item, "title", "name").lower()
        if title == target or target in title:
            return item
    return None


def list_services(session: DsxSession, brand_id: str = "") -> dict[str, Any]:
    settings = get_settings()
    parent = _find_parent_brand(session, settings) if not brand_id else None
    effective_brand = brand_id or (parent.get("id") if parent else "")
    path = settings.dsx_product_services_path or "/services"
    attempts: list[tuple[str, dict[str, Any] | None]] = [
        (path, {"brand": effective_brand} if effective_brand else None),
        (path, {settings.dsx_programs_service_param: effective_brand} if effective_brand else None),
        (path, None),
        ("/services", {"brand": effective_brand} if effective_brand else None),
    ]
    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    items, entries = probe_get_items(
        session.http, settings, settings.dsx_base_url, attempts, recorder=probe
    )
    services_unavailable = not items and bool(parent)
    hint = ""
    if not items:
        hint = (
            "No product services returned. On some tenants, pick a program directly under the brand."
            if parent
            else "No product services returned. Check DSX_PRODUCT_SERVICES_PATH or set DSX_PARENT_BRAND_NAME."
        )
    result: dict[str, Any] = {
        "items": items,
        "hint": hint,
        "probe": entries,
        "probe_summary": entries,
        "services_unavailable": services_unavailable,
    }
    if parent:
        result["parent_brand"] = parent
    return result


def list_programs(
    session: DsxSession,
    *,
    service_id: str = "",
    brand_id: str = "",
) -> dict[str, Any]:
    settings = get_settings()
    path = settings.dsx_programs_path or "/programs"
    attempts: list[tuple[str, dict[str, Any] | None]] = []
    if service_id:
        attempts.append((path, {settings.dsx_programs_service_param: service_id}))
        attempts.append((path, {"service": service_id}))
    if brand_id:
        attempts.append((path, {"brand": brand_id}))
    attempts.extend([(path, None), ("/programs", None)])
    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    items, entries = probe_get_items(
        session.http, settings, settings.dsx_base_url, attempts, recorder=probe
    )
    hint = "" if items else "No programs returned. Check DSX_PROGRAMS_PATH or pick another brand/service."
    return {"items": items, "hint": hint, "probe": entries, "probe_summary": entries}


def list_releases(session: DsxSession, program_id: str) -> dict[str, Any]:
    settings = get_settings()
    path_template = settings.dsx_releases_path or "/releases/{program_id}"
    path = path_template.format(program_id=program_id)
    attempts: list[tuple[str, dict[str, Any] | None]] = [
        (path, None),
        ("/releases", {settings.dsx_releases_program_param: program_id}),
        ("/releases", {"program": program_id}),
    ]
    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    items, entries = probe_get_items(
        session.http, settings, settings.dsx_base_url, attempts, recorder=probe
    )
    hint = "" if items else "No releases returned for this program. Check DSX_RELEASES_PATH."
    return {"items": items, "hint": hint, "probe": entries}


def _search_releases(session: DsxSession, query: str) -> list[dict[str, Any]]:
    settings = get_settings()
    q = query.strip()
    attempts: list[tuple[str, dict[str, Any] | None]] = [
        ("/releases", {"q": q}),
        ("/releases", {"search": q}),
        ("/releases", {"name": q}),
        ("/releases", {"code": q}),
        ("/releases", {"title": q}),
        ("/releases", None),
    ]
    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    items, _ = probe_get_items(session.http, settings, settings.dsx_base_url, attempts, recorder=probe)
    if items and q:
        ranked = sorted(
            ((score_release_match(q, item), item) for item in items),
            key=lambda pair: pair[0],
            reverse=True,
        )
        strong = [item for score, item in ranked if score >= 0.5]
        return strong or items
    return items


def _fetch_release_by_id(session: DsxSession, release_id: str) -> dict[str, Any] | None:
    settings = get_settings()
    for path in (f"/releases/{release_id}", "/releases"):
        status, body = request_json(
            session.http,
            settings.dsx_base_url,
            path,
            params={"id": release_id} if path == "/releases" else None,
        )
        if status >= 400:
            continue
        if isinstance(body, dict) and not isinstance(body.get("items"), list):
            return normalize_catalog_item(body)
        items = extract_items(body)
        for item in items:
            if pick_str(item, "id", "physicalid") == release_id:
                return item
    return None


def resolve_release_by_query(session: DsxSession, query: str) -> dict[str, Any]:
    settings = get_settings()
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")

    if re.fullmatch(r"[0-9A-Fa-f]{16,32}", q):
        release = _fetch_release_by_id(session, q)
        if release:
            fields = release_fields_from_item(release)
            return {
                "release": release,
                "release_fields": fields,
                "navigator_url": navigator_url(settings, fields.get("rel_eno_id")),
            }

    items = _search_releases(session, q)
    release = best_match(q, items)
    if not release and "-" in q:
        tail = q.split("-")[-1].strip()
        if tail:
            release = best_match(tail, items) or best_match(q.replace("-", " "), items)
    if not release:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"No release matched '{q}'. Try a release code, title, or physical ID.",
                "probe": [],
            },
        )
    fields = release_fields_from_item(release)
    return {
        "release": release,
        "release_fields": fields,
        "navigator_url": navigator_url(settings, fields.get("rel_eno_id")),
    }


def resolve_release_context(
    session: DsxSession,
    *,
    service_id: str,
    service_name: str,
    program_id: str,
    program_name: str,
    release_id: str,
) -> dict[str, Any]:
    settings = get_settings()
    release = _fetch_release_by_id(session, release_id)
    if not release:
        releases_body = list_releases(session, program_id)
        release = best_match(release_id, releases_body.get("items") or [])
    if not release:
        raise HTTPException(status_code=404, detail="Could not load release for the selected program")
    program_items = list_programs(session, service_id=service_id).get("items") or []
    program = next((p for p in program_items if pick_str(p, "id") == program_id), None)
    if not program:
        program = {"id": program_id, "title": program_name, "name": program_name}
    return {
        "service_id": service_id,
        "service_name": service_name,
        "program_id": program_id,
        "program_name": program_name,
        "release": release,
        "release_fields": release_fields_from_item(release),
        "detection_fields": detection_fields_from_program(program),
        "navigator_url": navigator_url(settings, release_fields_from_item(release).get("rel_eno_id")),
    }


def _find_program(
    session: DsxSession,
    *,
    name: str,
    service_id: str = "",
    brand_id: str = "",
) -> dict[str, Any] | None:
    body = list_programs(session, service_id=service_id, brand_id=brand_id)
    return best_match(name, body.get("items") or [])


def _find_feature(session: DsxSession, feature_name: str) -> dict[str, Any] | None:
    settings = get_settings()
    name = feature_name.strip()
    if not name:
        return None
    if re.fullmatch(r"[0-9A-Fa-f]{16,32}", name):
        for path in (f"/features/{name}", "/features"):
            status, body = request_json(
                session.http, settings.dsx_base_url, path, params={"id": name} if path == "/features" else None
            )
            if status < 400:
                if isinstance(body, dict) and body.get("physicalid") or body.get("id"):
                    return normalize_catalog_item(body)
                hit = best_match(name, extract_items(body))
                if hit:
                    return hit
    attempts = [
        ("/features", {"q": name}),
        ("/features", {"search": name}),
        ("/features", {"name": name}),
        ("/features", None),
        ("/collections", {"q": name}),
        ("/collections", {"search": name}),
    ]
    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    items, _ = probe_get_items(session.http, settings, settings.dsx_base_url, attempts, recorder=probe)
    return best_match(name, items)


def resolve_detection_level(
    session: DsxSession,
    *,
    detection_level_name: str,
    detection_level_title: str = "",
    service_id: str = "",
    brand_id: str = "",
) -> dict[str, Any]:
    query = (detection_level_name or detection_level_title or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="detection_level_name is required")
    if re.fullmatch(r"[0-9A-Fa-f]{16,32}", query):
        fields = {
            "detection_level_eno_id": query,
            "detection_level_name": detection_level_name or query,
            "detection_level_title": detection_level_title or detection_level_name or query,
        }
        return {"fields": fields}
    program = _find_program(session, name=query, service_id=service_id, brand_id=brand_id)
    if not program:
        raise HTTPException(status_code=404, detail=f"No program matched '{query}'")
    return {"fields": detection_fields_from_program(program)}


def resolve_ir_references(session: DsxSession, payload: dict[str, Any]) -> dict[str, Any]:
    fields = dict(payload)
    service_id = str(payload.get("service_id") or "").strip()
    brand_id = str(payload.get("brand_id") or "").strip()

    if not fields.get("rel_eno_id") and (fields.get("rel_name") or fields.get("rel_title")):
        query = str(fields.get("rel_title") or fields.get("rel_name"))
        try:
            resolved = resolve_release_by_query(session, query)
            fields.update(resolved.get("release_fields") or {})
        except HTTPException:
            pass

    if not fields.get("feature_eno_id") and fields.get("feature_name"):
        feature = _find_feature(session, str(fields["feature_name"]))
        if feature:
            fields["feature_eno_id"] = pick_str(feature, "id", "physicalid")
            fields["feature_name"] = pick_str(feature, "title", "name") or fields.get("feature_name")

    if not fields.get("detection_level_eno_id") and (
        fields.get("detection_level_name") or fields.get("detection_level_title")
    ):
        try:
            det = resolve_detection_level(
                session,
                detection_level_name=str(fields.get("detection_level_name") or ""),
                detection_level_title=str(fields.get("detection_level_title") or ""),
                service_id=service_id,
                brand_id=brand_id,
            )
            fields.update(det.get("fields") or {})
        except HTTPException:
            pass

    return {"fields": fields}


def _normalize_create_payload(payload: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for src_key, dest_keys in CREATE_FORM_MAP.items():
        val = payload.get(src_key)
        if val is None or str(val).strip() == "":
            continue
        for dest in dest_keys:
            normalized[dest] = str(val).strip()
    if payload.get("detection_level_title") and "detectionLevelEnoId" in normalized:
        normalized.setdefault("detectionLevelTitle", str(payload["detection_level_title"]).strip())
    return normalized


def create_incident_report(session: DsxSession, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    missing = [k for k in REQUIRED_CREATE_KEYS if not str(payload.get(k) or "").strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")

    form = _normalize_create_payload(payload)
    multipart = {key: (None, value) for key, value in form.items()}
    status, body = request_json(
        session.http,
        settings.dsx_base_url,
        "/incidentfamilies",
        method="POST",
        files=multipart,
    )
    if status >= 400:
        raise HTTPException(
            status_code=502,
            detail={"error": "DSX create incident failed", "details": body},
        )

    result: dict[str, Any]
    if isinstance(body, dict):
        result = dict(body)
    else:
        result = {"raw": body}

    physical_id = pick_str(
        result,
        "physicalid",
        "physicalId",
        "object_id",
        "objectId",
        "id",
    )
    name = pick_str(result, "name", "title")
    result.setdefault("physicalid", physical_id)
    result.setdefault("object_id", physical_id)
    result.setdefault("id", name or physical_id)
    result.setdefault("name", name)
    result["url"] = navigator_url(settings, physical_id)
    doc_id = pick_str(result, "document_id", "documentId", "primaryDocumentId")
    if doc_id:
        result["document_id"] = doc_id
    return result


def list_my_incidents(session: DsxSession) -> dict[str, Any]:
    settings = get_settings()
    attempts = [
        ("/incidentfamilies", {"owner": session.username, "state": "Active"}),
        ("/incidentfamilies", {"owner": session.username}),
        ("/incidentfamilies", None),
    ]
    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    items, _ = probe_get_items(session.http, settings, settings.dsx_base_url, attempts, recorder=probe)
    normalized = []
    for item in items:
        normalized.append(
            {
                "id": pick_str(item, "name", "id"),
                "title": pick_str(item, "title", "name"),
                "physicalid": pick_str(item, "physicalid", "id"),
                **item,
            }
        )
    return {"items": normalized}


def search_incidents(session: DsxSession, query: str, feature: str = "") -> dict[str, Any]:
    settings = get_settings()
    params: dict[str, Any] = {}
    if query:
        params["q"] = query
    if feature:
        params["feature"] = feature
    status, body = request_json(session.http, settings.dsx_base_url, "/incidentfamilies", params=params)
    if status >= 400:
        return {"items": []}
    items = extract_items(body)
    return {
        "items": [
            {
                "id": pick_str(item, "name", "id"),
                "title": pick_str(item, "title", "name"),
                **item,
            }
            for item in items
        ]
    }


def upload_ir_media(
    session: DsxSession,
    ir_id: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    document_id: str = "",
) -> dict[str, Any]:
    settings = get_settings()
    files = {"file": (filename, file_bytes, content_type or "application/octet-stream")}
    attempts: list[tuple[str, dict[str, Any] | None]] = [
        (f"/incidentfamilies/{ir_id}/documents", {"document_id": document_id} if document_id else None),
        (f"/incidentfamilies/{ir_id}/attachments", None),
    ]
    if document_id:
        attempts.append((f"/documents/{document_id}/files", None))
    else:
        attempts.append(("/documents", {"parentId": ir_id}))
    last_error = "Upload failed"
    for path, params in attempts:
        status, body = request_json(
            session.http,
            settings.dsx_base_url,
            path,
            method="POST",
            params={k: v for k, v in (params or {}).items() if v},
            files=files,
        )
        if status < 400:
            return {"ok": True, "path": path, "body": body}
        last_error = format_dsx_error(status, body)
    raise HTTPException(status_code=502, detail=last_error)


def _parse_template_fields(raw: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    candidates = [
        raw.get("fields"),
        raw.get("form"),
        raw.get("data"),
        raw.get("attributes"),
    ]
    for block in candidates:
        if isinstance(block, dict):
            for key, val in block.items():
                if val is None:
                    continue
                if isinstance(val, dict):
                    inner = val.get("value") or val.get("defaultValue") or val.get("displayValue")
                    if inner is not None:
                        fields[str(key)] = str(inner).strip()
                else:
                    fields[str(key)] = str(val).strip()
    for key in (
        "rel_eno_id",
        "rel_name",
        "rel_title",
        "rel_level_id",
        "feature_eno_id",
        "feature_name",
        "detection_level_eno_id",
        "detection_level_name",
        "detection_level_title",
        "default_clarifier",
        "default_corrector",
        "default_validator",
        "owner",
        "severity",
        "detected_environment",
    ):
        if key in raw and raw[key] is not None:
            fields[key] = str(raw[key]).strip()
    return fields


def list_saved_ir_forms(session: DsxSession) -> dict[str, Any]:
    settings = get_settings()
    if not settings.dsx_ui_configured:
        return {"items": [], "hint": "DSX_UI_BASE_URL is not configured", "probe": []}

    custom = settings.dsx_creation_forms_path.strip()
    if custom:
        path = custom.split("?", 1)[0].lstrip("/")
        query = custom.split("?", 1)[1] if "?" in custom else ""
        params = {}
        if query:
            for part in query.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        attempts = [(path, params or None)]
    else:
        attempts = [
            ("/formtemplates", {"owner": session.username, "type": "ECR", "current": "true"}),
            ("/formtemplates", {"type": "ECR", "current": "true"}),
            ("/formtemplates", None),
        ]

    probe = ProbeRecorder(settings.dsx_probe_max_attempts)
    status_last = 0
    body_last: Any = None
    items: list[dict[str, Any]] = []
    for path, params in attempts:
        if not probe.can_continue():
            break
        status, body = request_json(session.http, settings.dsx_ui_base_url, path, params=params)
        status_last = status
        body_last = body
        items = extract_items(body)
        probe.record(path, params, status, len(items))
        if items:
            break

    if not items and status_last >= 400:
        return {
            "items": [],
            "hint": format_dsx_error(status_last, body_last),
            "probe": probe.entries,
        }

    forms = []
    for raw in items:
        title = pick_str(raw, "title", "name", "label") or "Saved form"
        forms.append(
            {
                "id": pick_str(raw, "id", "physicalid", "name"),
                "title": title,
                "fields": _parse_template_fields(raw),
            }
        )
    hint = "" if forms else "No saved IR form templates returned from DSX UI REST."
    return {"items": forms, "hint": hint, "probe": probe.entries}
