"""Create IR, search, and media upload against Coal Porter."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, UploadFile

from backend.config import ir_object_url, join_devops
from backend.dsx_http import _json_or_raise, _request, list_incident_families
from backend.dsx_resolve import enrich_release_fields, resolve_ir_references
from backend.dsx_util import (
    BLOCKED_PLACEHOLDER_IDS,
    PHYSICAL_ID_FIELDS,
    is_object_reference,
    normalize_detected_environment,
    pick_id,
    uppercase_physical_id_fields,
)
from backend.sessions import DsxSession
from backend.ui_config import LINKED_FIELD_LABELS

REQUIRED_FIELDS = [
    "title",
    "description",
    "severity",
    "detected_environment",
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
]  # mandatory in POST /incidentfamilies (see /openapi.json)

DSX_CREATE_KEYS = [
    "title",
    "description",
    "severity",
    "detected_environment",
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
]

def _normalize_create_payload(payload: dict[str, Any]) -> dict[str, str]:
    data: dict[str, str] = {}
    for key in DSX_CREATE_KEYS:
        val = payload.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            data[key] = text
    if "detected_environment" in data:
        data["detected_environment"] = normalize_detected_environment(data["detected_environment"])
    return uppercase_physical_id_fields(data)


def _finalize_create_payload(data: dict[str, str], username: str) -> dict[str, str]:
    finalized = dict(data)
    if finalized.get("rel_name") and not finalized.get("rel_level_id"):
        finalized["rel_level_id"] = finalized["rel_name"]
    if finalized.get("detection_level_name") and not finalized.get("detection_level_title"):
        finalized["detection_level_title"] = finalized["detection_level_name"]
    if not finalized.get("owner"):
        finalized["owner"] = username
    return finalized


def validate_physical_ids(data: dict[str, str]) -> None:
    errors: list[str] = []
    for key in PHYSICAL_ID_FIELDS:
        value = data.get(key, "").strip()
        if not value:
            continue
        if value.upper() in BLOCKED_PLACEHOLDER_IDS:
            errors.append(f"{LINKED_FIELD_LABELS.get(key, key)}: invalid placeholder reference")
        elif not is_object_reference(value):
            errors.append(
                f"{LINKED_FIELD_LABELS.get(key, key)}: could not resolve the linked 3DEXPERIENCE object — "
                "try a saved form template or pick release/feature/detection in DSX first"
            )
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))


def list_unresolved_physical_ids(data: dict[str, str]) -> list[dict[str, str]]:
    unresolved: list[dict[str, str]] = []
    for key in PHYSICAL_ID_FIELDS:
        value = data.get(key, "").strip()
        if not value or not is_object_reference(value):
            unresolved.append({"key": key, "label": LINKED_FIELD_LABELS.get(key, key)})
    return unresolved


def prepare_ir_payload(
    dsx: DsxSession,
    payload: dict[str, Any],
    *,
    for_create: bool = False,
) -> dict[str, str]:
    raw = dict(payload)
    service_id = str(raw.pop("service_id", "") or "").strip()
    brand_id = str(raw.pop("brand_id", "") or "").strip()

    data = _normalize_create_payload(raw)
    data = _finalize_create_payload(data, dsx.username)

    resolve_input = {**data, "service_id": service_id, "brand_id": brand_id}
    resolved = resolve_ir_references(dsx, resolve_input)
    data.update(resolved.get("fields") or {})
    data = enrich_release_fields(dsx, data)

    if for_create:
        missing = [k for k in REQUIRED_FIELDS if not data.get(k)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required fields for IR creation: {', '.join(missing)}",
            )
        if not data.get("rel_eno_id"):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Target release required",
                    "message": (
                        "rel_eno_id is missing. Set target release on the dashboard "
                        "(service → program → release) before filing."
                    ),
                },
            )
        validate_physical_ids(data)

    return data


def _parse_create_response(body: Any) -> dict[str, Any]:
    if isinstance(body, list) and body:
        body = body[0]
    if not isinstance(body, dict):
        return {"raw": body}
    object_id = (
        pick_id(body)
        or str(body.get("object_id") or body.get("physicalid") or body.get("name") or "")
    )
    name = str(body.get("name") or body.get("id") or object_id)
    document_id = str(body.get("document_id") or body.get("documentId") or "").strip()
    return {
        "id": name or object_id,
        "name": name,
        "object_id": object_id,
        "physicalid": object_id,
        "document_id": document_id or None,
        "url": ir_object_url(object_id),
        "raw": body,
    }


def create_incident_report(dsx: DsxSession, payload: dict[str, Any]) -> dict[str, Any]:
    form = prepare_ir_payload(dsx, payload, for_create=True)
    url = join_devops("/incidentfamilies")
    # POST /incidentfamilies only accepts multipart/form-data (urlencoded gives HTTP 415), so send
    # every field as a multipart part: (None, value) means "plain field, no filename".
    parts = {key: (None, value) for key, value in form.items()}
    resp = _request(dsx, url, method="POST", files=parts)
    if resp.status_code >= 400:
        detail: Any
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:800]
        raise HTTPException(
            status_code=resp.status_code if resp.status_code < 500 else 502,
            detail={"error": "DSX incidentfamilies POST failed", "details": detail},
        )
    try:
        body = resp.json()
    except Exception:
        body = {"text": resp.text}
    return _parse_create_response(body)


def search_incidents(dsx: DsxSession, query: str, feature: str = "") -> list[dict[str, Any]]:
    items = list_incident_families(dsx, q=query, feature=feature)
    if not query:
        return items
    ql = query.lower()
    return [
        ir
        for ir in items
        if ql in (ir.get("title") or "").lower() or ql in (ir.get("name") or "").lower()
    ]


def my_incidents(dsx: DsxSession) -> list[dict[str, Any]]:
    return list_incident_families(dsx, owner=dsx.username)


async def upload_ir_media(
    dsx: DsxSession,
    ir_id: str,
    file: UploadFile,
    document_id: Optional[str] = None,
) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload")
    filename = file.filename or "attachment"
    files = {"file": (filename, content, file.content_type or "application/octet-stream")}

    paths = [
        f"/incidentfamilies/{ir_id}/documents",
        f"/incidentfamilies/{ir_id}/files",
        f"/documents/{document_id}/files" if document_id else "",
    ]
    paths = [p for p in paths if p]

    last_detail: Any = "upload failed"
    for path in paths:
        url = join_devops(path)
        data = {}
        if document_id and "documents" not in path:
            data["document_id"] = document_id
        resp = _request(dsx, url, method="POST", data=data, files=files)
        if resp.status_code < 400:
            try:
                body = resp.json()
            except Exception:
                body = {"status": "ok"}
            return {"status": "ok", "path": path, "body": body}
        try:
            last_detail = resp.json()
        except Exception:
            last_detail = resp.text[:500]

    raise HTTPException(
        status_code=502,
        detail={"error": "IR media upload failed on DSX", "details": last_detail},
    )
