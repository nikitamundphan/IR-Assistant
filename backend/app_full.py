"""
FastAPI application for the IR Chatbot (Coal Porter REST wrapper).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import config
from backend.dsx_http import (
    fetch_brands,
    fetch_formtemplates,
    fetch_programs,
    fetch_releases,
    fetch_services,
)
from backend.dsx_resolve import (
    resolve_detection_level,
    resolve_form_templates,
    resolve_release_by_query,
    resolve_release_context,
)
from backend.ir_create import (
    create_incident_report,
    list_unresolved_physical_ids,
    my_incidents,
    prepare_ir_payload,
    search_incidents,
    upload_ir_media,
)
from backend.ir_schema import ir_field_schema_payload
from backend.sessions import (
    DsxAuthError,
    DsxUnavailableError,
    create_session,
    delete_session,
    require_session,
)
from backend.ui_config import ui_bootstrap_payload

app = FastAPI(title="IR Chatbot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def session_header(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")) -> Optional[str]:
    return x_session_id


def auth_session(session_id: Optional[str] = Depends(session_header)):
    try:
        return require_session(session_id)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


class LoginBody(BaseModel):
    username: str
    password: str


class ResolveReleaseBody(BaseModel):
    query: str = Field(..., min_length=1)


class ResolveReleaseContextBody(BaseModel):
    service_id: str
    service_name: str = ""
    program_id: str
    program_name: str = ""
    release_id: str


class ResolveDetectionBody(BaseModel):
    detection_level_name: str = ""
    detection_level_title: str = ""
    service_id: str = ""
    brand_id: str = ""


def _login(username: str, password: str):
    """401 only when DSX rejects the key; 502 when DSX is unreachable or misconfigured."""
    try:
        return create_session(username, password)
    except DsxAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except DsxUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    return config.health_payload()


@app.post("/api/login")
def api_login(body: LoginBody) -> dict[str, str]:
    dsx = _login(body.username, body.password)
    return {"session_id": dsx.session_id, "username": dsx.username}


@app.post("/api/login/env")
def api_login_env() -> dict[str, str]:
    if not config.IR_ALLOW_ENV_LOGIN:
        raise HTTPException(status_code=403, detail="Environment login is disabled")
    user, password = config.parsed_env_credentials()
    if not user or not password:
        raise HTTPException(status_code=400, detail="DSX_CREDENTIALS is not set on the server")
    dsx = _login(user, password)
    return {"session_id": dsx.session_id, "username": dsx.username}


@app.post("/api/logout")
def api_logout(session_id: Optional[str] = Depends(session_header)) -> dict[str, str]:
    delete_session(session_id)
    return {"status": "ok"}


@app.get("/api/me")
def api_me(dsx=Depends(auth_session)) -> dict[str, Any]:
    return {
        "username": dsx.username,
        "profile": dsx.profile,
        **ui_bootstrap_payload(dsx.username),
    }


@app.get("/api/ir-field-schema")
def api_ir_field_schema() -> dict[str, Any]:
    return ir_field_schema_payload()


@app.get("/api/dsx/brands")
def api_dsx_brands(dsx=Depends(auth_session)) -> dict[str, Any]:
    items, hint, probe = fetch_brands(dsx)
    return {"items": items, "hint": hint, "probe": probe}


@app.get("/api/dsx/services")
def api_dsx_services(
    brand_id: str = Query("", alias="brand_id"),
    dsx=Depends(auth_session),
) -> dict[str, Any]:
    return fetch_services(dsx, brand_id=brand_id)


@app.get("/api/dsx/programs")
def api_dsx_programs(
    service_id: str = Query("", alias="service_id"),
    brand_id: str = Query("", alias="brand_id"),
    dsx=Depends(auth_session),
) -> dict[str, Any]:
    if not service_id and not brand_id:
        raise HTTPException(status_code=400, detail="serviceId or brandId is required")
    return fetch_programs(dsx, service_id=service_id, brand_id=brand_id)


@app.get("/api/dsx/releases")
def api_dsx_releases(
    program_id: str = Query(..., alias="program_id"),
    dsx=Depends(auth_session),
) -> dict[str, Any]:
    return fetch_releases(dsx, program_id)


@app.get("/api/dsx/navigator-url")
def api_navigator_url(physical_id: str = Query(..., alias="physical_id")) -> dict[str, str]:
    return {"url": config.navigator_url(physical_id)}


@app.post("/api/dsx/resolve-release")
def api_resolve_release(body: ResolveReleaseBody, dsx=Depends(auth_session)) -> dict[str, Any]:
    return resolve_release_by_query(dsx, body.query)


@app.post("/api/dsx/resolve-release-context")
def api_resolve_release_context(body: ResolveReleaseContextBody, dsx=Depends(auth_session)) -> dict[str, Any]:
    return resolve_release_context(
        dsx,
        service_id=body.service_id,
        service_name=body.service_name,
        program_id=body.program_id,
        program_name=body.program_name,
        release_id=body.release_id,
    )


@app.post("/api/dsx/resolve-detection-level")
def api_resolve_detection(body: ResolveDetectionBody, dsx=Depends(auth_session)) -> dict[str, Any]:
    return resolve_detection_level(
        dsx,
        detection_level_name=body.detection_level_name,
        detection_level_title=body.detection_level_title,
        service_id=body.service_id,
        brand_id=body.brand_id,
    )


@app.get("/api/saved-ir-forms")
def api_saved_ir_forms(dsx=Depends(auth_session)) -> dict[str, Any]:
    items, hint, probe = fetch_formtemplates(dsx)
    return {"items": resolve_form_templates(dsx, items), "hint": hint, "probe": probe}


@app.get("/api/my-incidents")
def api_my_incidents(dsx=Depends(auth_session)) -> dict[str, Any]:
    return {"items": my_incidents(dsx)}


@app.post("/api/resolve-ir-references")
def api_resolve_ir_references(payload: dict[str, Any], dsx=Depends(auth_session)) -> dict[str, Any]:
    data = prepare_ir_payload(dsx, payload, for_create=False)
    return {"fields": data, "unresolved": list_unresolved_physical_ids(data)}


@app.post("/api/create-ir")
def api_create_ir(payload: dict[str, Any], dsx=Depends(auth_session)) -> dict[str, Any]:
    return create_incident_report(dsx, payload)


@app.get("/api/search-ir")
def api_search_ir(
    q: str = Query(""),
    feature: str = Query(""),
    dsx=Depends(auth_session),
) -> dict[str, Any]:
    return {"items": search_incidents(dsx, q, feature)}


@app.post("/api/upload-ir-media/{ir_id}")
async def api_upload_ir_media(
    ir_id: str,
    file: UploadFile = File(...),
    document_id: str = Query(""),
    dsx=Depends(auth_session),
) -> dict[str, Any]:
    return await upload_ir_media(dsx, ir_id, file, document_id or None)


__all__ = ["app"]
