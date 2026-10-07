"""
IR Chatbot backend (Python / FastAPI) — wraps 3DEXPERIENCE DSX REST APIs.

Auth: per-user HTTP Basic (login:api-key) via /api/login session, or DSX_CREDENTIALS in .env.

Run:
  pip install -r requirements.txt
  uvicorn server:app --reload --port 3001
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from typing import Any, Optional

import logging

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger(__name__)

try:
    from mli_client import MLI_MODEL, generate_ir_title, mli_configured
except ImportError:
    MLI_MODEL = ""
    mli_configured = lambda: False  # noqa: E731
    generate_ir_title = None  # type: ignore

BASE_URL = os.environ.get("DSX_BASE_URL", "").rstrip("/")
UI_BASE_URL = os.environ.get("DSX_UI_BASE_URL", "").strip().rstrip("/")
WEB_BASE_URL = os.environ.get("DSX_WEB_BASE_URL", "").strip().rstrip("/")
ENV_CREDENTIALS = os.environ.get("DSX_CREDENTIALS", "").strip()
IR_ALLOW_ENV_LOGIN = os.environ.get("IR_ALLOW_ENV_LOGIN", "true").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

INVALID_CREDENTIALS_DETAIL = (
    "DSX rejected this login/API key. Use your DSX service key (from MyApps), "
    "not your Windows password."
)
DSX_UNREACHABLE_DETAIL = "Could not reach DSX. Check DSX_BASE_URL and your network connection."

DSX_SERVICES_PATH = os.environ.get("DSX_SERVICES_PATH", "/brands").strip() or "/brands"
DSX_PRODUCT_SERVICES_PATH = os.environ.get("DSX_PRODUCT_SERVICES_PATH", "/services").strip() or "/services"
DSX_PROGRAMS_PATH = os.environ.get("DSX_PROGRAMS_PATH", "/programs").strip() or "/programs"
DSX_RELEASES_PATH = os.environ.get("DSX_RELEASES_PATH", "/releases/{program_id}").strip() or "/releases/{program_id}"
DSX_PROGRAMS_SERVICE_PARAM = os.environ.get("DSX_PROGRAMS_SERVICE_PARAM", "service").strip() or "service"
DSX_RELEASES_PROGRAM_PARAM = os.environ.get("DSX_RELEASES_PROGRAM_PARAM", "program").strip() or "program"
DSX_PROBE_MAX_ATTEMPTS = max(1, int(os.environ.get("DSX_PROBE_MAX_ATTEMPTS", "12")))
DSX_PARENT_BRAND_NAME = os.environ.get("DSX_PARENT_BRAND_NAME", "3DEXPERIENCE Platform").strip() or "3DEXPERIENCE Platform"

REQUIRED_FIELDS = [
    "title",
    "description",
    "severity",
    "detected_environment",
    "default_clarifier",
    "default_corrector",
    "default_validator",
    "rel_eno_id",
    "rel_name",
    "rel_title",
    "rel_level_id",
    "feature_eno_id",
    "feature_name",
    "detection_level_eno_id",
    "detection_level_name",
    "detection_level_title",
]

SAVED_FORM_FIELD_KEYS = [
    "title",
    "description",
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
    "env",
    "aura",
    "swym",
    "swymUi",
]

DETECTED_ENVIRONMENT_MAP = {
    "cloud": "CLOUD",
    "on-premise": "ON_PREMISE",
    "on premise": "ON_PREMISE",
    "windows": "WINDOWS",
    "linux": "LINUX",
    "macos": "MACOS",
}

PHYSICAL_ID_FIELDS = ("rel_eno_id", "feature_eno_id", "detection_level_eno_id")
PHYSICAL_ID_PATTERN = re.compile(r"^[0-9A-F]{32}$", re.IGNORECASE)
ENO_OID_PATTERN = re.compile(r"^\d+(?:\.\d+)+$")
BLOCKED_PLACEHOLDER_IDS = frozenset(
    {
        "A1B2C3D4E5F6789012345678ABCDEF01",
        "0123456789ABCDEF0123456789ABCDEF",
        "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
    }
)

IR_FORM_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "release",
        "label": "Target release (fix version)",
        "intro": (
            "The release where the issue should be fixed — not the build where you found it. "
            "Open the release object in 3DEXPERIENCE and copy values from its properties."
        ),
        "fields": [
            {
                "key": "rel_title",
                "label": "Release title",
                "prompt": "What is the release title (display name)?",
                "hint": "Human-readable release name shown in 3DEXPERIENCE (e.g. R2026x GA, 26x.FP1).",
                "placeholder": "e.g. R2026x GA",
            },
            {
                "key": "rel_name",
                "label": "Release short name",
                "prompt": "What is the release short name / code?",
                "hint": "Internal release code, often the milestone or stream identifier.",
                "placeholder": "e.g. R426",
            },
            {
                "key": "rel_eno_id",
                "label": "Release reference",
                "internal": True,
                "prompt": "Release object reference (filled automatically from saved forms).",
                "hint": "Resolved by the backend — users do not enter this in 3DEXPERIENCE.",
                "placeholder": "",
            },
            {
                "key": "rel_level_id",
                "label": "Release level ID",
                "prompt": "What is the release level ID?",
                "hint": "Level identifier within the release hierarchy (from the release level object).",
                "placeholder": "Level ID from 3DEXPERIENCE",
            },
        ],
    },
    {
        "id": "feature",
        "label": "Concerned feature",
        "intro": "The product feature or functional area affected by this incident.",
        "fields": [
            {
                "key": "feature_name",
                "label": "Feature name",
                "prompt": "Which feature is affected?",
                "hint": "Display name of the feature object in Software Governance / 3DEXPERIENCE.",
                "placeholder": "e.g. SWYM Communities",
            },
            {
                "key": "feature_eno_id",
                "label": "Feature reference",
                "internal": True,
                "prompt": "Feature object reference (filled automatically from saved forms).",
                "hint": "Resolved by the backend — users do not enter this in 3DEXPERIENCE.",
                "placeholder": "",
            },
        ],
    },
    {
        "id": "detection",
        "label": "Issue detected version",
        "intro": "The program or build level where you detected the issue (may differ from the fix release).",
        "fields": [
            {
                "key": "detection_level_name",
                "label": "Issue detected version",
                "prompt": "Which version did you detect this issue in?",
                "hint": "Program code or version label (e.g. PRG044546). The ID is looked up automatically.",
                "placeholder": "e.g. PRG044546",
            },
            {
                "key": "detection_level_title",
                "label": "Detection level title",
                "internal": True,
                "prompt": "Detection level title (filled automatically).",
                "hint": "Mirrored from the issue detected version or saved form.",
                "placeholder": "",
            },
            {
                "key": "detection_level_eno_id",
                "label": "Detection program reference",
                "internal": True,
                "prompt": "Detection program reference (filled automatically from saved forms).",
                "hint": "Resolved by the backend — users do not enter this in 3DEXPERIENCE.",
                "placeholder": "",
            },
        ],
    },
    {
        "id": "actors",
        "label": "Clarifier, corrector & validator",
        "intro": "DS login (trigram) of the people who will clarify, correct, and validate this IR.",
        "fields": [
            {
                "key": "default_clarifier",
                "label": "Clarifier",
                "prompt": "Who is the clarifier (DS login)?",
                "hint": "Person who clarifies the incident — usually your trigram, e.g. nmn38.",
                "placeholder": "e.g. nmn38",
            },
            {
                "key": "default_corrector",
                "label": "Corrector",
                "prompt": "Who is the corrector (DS login)?",
                "hint": "Developer or owner responsible for the fix.",
                "placeholder": "e.g. abc12",
            },
            {
                "key": "default_validator",
                "label": "Validator",
                "prompt": "Who is the validator (DS login)?",
                "hint": "Person who validates the fix before closure.",
                "placeholder": "e.g. xyz99",
            },
            {
                "key": "owner",
                "label": "Owner",
                "prompt": "IR owner (DS login)? Leave blank to use your login.",
                "hint": "Defaults to your account if you skip this.",
                "placeholder": "Your trigram (optional)",
                "optional": True,
            },
        ],
    },
]

# In-memory sessions: session_id -> {encoded_auth, username}
SESSIONS: dict[str, dict[str, str]] = {}

app = FastAPI(title="IR Chatbot Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_request, exc: RequestValidationError):
    labels = _field_labels()
    parts: list[str] = []
    for error in exc.errors():
        field = error.get("loc", ["field"])[-1]
        label = labels.get(str(field), str(field))
        parts.append(f"{label}: {error.get('msg', 'invalid value')}")
    message = "Invalid request: " + "; ".join(parts) if parts else "Invalid request payload"
    return JSONResponse(status_code=400, content={"detail": message})


class LoginRequest(BaseModel):
    username: str
    password: str = Field(..., description="DSX API key / service password")


class GenerateTitleRequest(BaseModel):
    description: str
    severity: Optional[str] = ""
    feature_name: Optional[str] = ""
    detection_version: Optional[str] = ""
    detected_environment: Optional[str] = ""


class CreateIRRequest(BaseModel):
    title: str
    description: str
    severity: str
    detected_environment: str
    default_clarifier: str
    default_corrector: str
    default_validator: str
    rel_eno_id: Optional[str] = None
    rel_name: str
    rel_title: str
    rel_level_id: str
    feature_eno_id: Optional[str] = None
    feature_name: str
    detection_level_eno_id: Optional[str] = None
    detection_level_name: str
    detection_level_title: Optional[str] = ""
    service_id: Optional[str] = None
    os: Optional[str] = None
    owner: Optional[str] = None
    owner_comment: Optional[str] = None
    tenant_id: Optional[str] = None
    regression_type: Optional[str] = None
    category: Optional[str] = None
    symptom: Optional[str] = None
    cve_id: Optional[str] = None
    cvss_score: Optional[str] = None
    last_working_level_eno_id: Optional[str] = None
    last_working_level_name: Optional[str] = None
    last_working_level_title: Optional[str] = None


class ResolveReleaseContextRequest(BaseModel):
    service_id: str
    program_id: str
    release_id: Optional[str] = None
    service_name: Optional[str] = None
    program_name: Optional[str] = None


class ResolveDetectionLevelRequest(BaseModel):
    detection_level_name: Optional[str] = ""
    detection_level_title: Optional[str] = ""
    service_id: Optional[str] = ""
    brand_id: Optional[str] = ""


class ResolveReleaseByNameRequest(BaseModel):
    query: Optional[str] = ""
    release_name: Optional[str] = ""
    release_title: Optional[str] = ""


class ResolveIrReferencesRequest(BaseModel):
    service_id: Optional[str] = None

    class Config:
        extra = "allow"


def _encode_credentials(username: str, password: str) -> str:
    raw = f"{username.strip().lower()}:{password.strip()}"
    return base64.b64encode(raw.encode()).decode()


def _configured_env_username() -> Optional[str]:
    if ENV_CREDENTIALS and ":" in ENV_CREDENTIALS:
        return ENV_CREDENTIALS.split(":", 1)[0].strip().lower()
    return None


def _env_login_available() -> bool:
    return bool(ENV_CREDENTIALS and ":" in ENV_CREDENTIALS and IR_ALLOW_ENV_LOGIN)


def _create_session_from_encoded_auth(encoded_auth: str, username: str) -> dict:
    session_id = secrets.token_hex(24)
    normalized_username = username.strip().lower()
    SESSIONS[session_id] = {
        "encoded_auth": encoded_auth,
        "username": normalized_username,
    }
    return {
        "session_id": session_id,
        "username": normalized_username,
    }


def _validate_dsx_credentials(encoded_auth: str, username: str) -> None:
    if not BASE_URL:
        raise HTTPException(500, detail="DSX_BASE_URL is not configured")

    session_stub = {"encoded_auth": encoded_auth, "username": username.strip().lower()}
    probe_paths = [DSX_SERVICES_PATH, "/creationforms", "/incidentfamilies?state=OPEN"]
    authenticated = False
    last_status = None
    network_error = False

    for path in probe_paths:
        try:
            response = _dsx_request(session_stub, "GET", path)
            last_status = response.status_code
            if response.status_code in (200, 204):
                authenticated = True
                break
            if response.status_code == 401:
                raise HTTPException(401, detail=INVALID_CREDENTIALS_DETAIL)
        except requests.RequestException:
            network_error = True
            continue

    if authenticated:
        return

    if network_error and last_status is None:
        raise HTTPException(502, detail=DSX_UNREACHABLE_DETAIL)
    if last_status == 401:
        raise HTTPException(401, detail=INVALID_CREDENTIALS_DETAIL)
    if network_error:
        raise HTTPException(502, detail=DSX_UNREACHABLE_DETAIL)
    raise HTTPException(
        401,
        detail=INVALID_CREDENTIALS_DETAIL,
    )


def _auth_headers(encoded_key: str, extra: Optional[dict] = None) -> dict:
    headers = {
        "Authorization": f"Basic {encoded_key}",
        "Accept": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _resolve_encoded_auth(x_session_id: Optional[str]) -> tuple[str, str]:
    if x_session_id and x_session_id in SESSIONS:
        session = SESSIONS[x_session_id]
        return session["encoded_auth"], session["username"]

    if ENV_CREDENTIALS and ":" in ENV_CREDENTIALS:
        login = ENV_CREDENTIALS.split(":", 1)[0]
        return base64.b64encode(ENV_CREDENTIALS.encode()).decode(), login

    raise HTTPException(401, detail="Not authenticated. Please log in.")


def get_session(x_session_id: Optional[str] = Header(None, alias="X-Session-Id")) -> dict:
    encoded_auth, username = _resolve_encoded_auth(x_session_id)
    return {"encoded_auth": encoded_auth, "username": username, "session_id": x_session_id}


def _dsx_request(
    session: dict,
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    files: Optional[dict] = None,
    data: Optional[dict] = None,
    timeout: int = 30,
) -> requests.Response:
    if not BASE_URL:
        raise HTTPException(500, detail="DSX_BASE_URL is not configured")

    url = f"{BASE_URL}{path}"
    headers = _auth_headers(session["encoded_auth"], {"x-dsx-limit": "1500"})

    if method.upper() == "GET":
        return requests.get(url, params=params, headers=headers, timeout=timeout)
    if method.upper() == "POST":
        if files:
            post_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            return requests.post(url, files=files, headers=post_headers, timeout=timeout)
        return requests.post(url, json=data, headers=headers, timeout=timeout)
    raise HTTPException(500, detail=f"Unsupported HTTP method: {method}")


def _ui_base_url() -> str:
    if UI_BASE_URL:
        return UI_BASE_URL
    if not BASE_URL:
        return ""
    base = BASE_URL.rstrip("/")
    if base.endswith("/devops/v1"):
        return f"{base[: -len('/devops/v1')]}/ui/v1"
    return ""


def _dsx_web_base_url() -> str:
    if WEB_BASE_URL:
        return WEB_BASE_URL
    for base in (UI_BASE_URL, BASE_URL):
        if not base:
            continue
        trimmed = base.rstrip("/")
        for suffix in ("/rest/ui/v1", "/rest/devops/v1"):
            if trimmed.endswith(suffix):
                return trimmed[: -len(suffix)]
        if trimmed.endswith("/enovia"):
            return trimmed
    return ""


def _build_ir_navigator_url(physical_id: str) -> str:
    pid = (physical_id or "").strip().upper()
    if not pid or not PHYSICAL_ID_PATTERN.match(pid):
        return ""
    web_base = _dsx_web_base_url()
    if not web_base:
        return ""
    return f"{web_base}/common/emxNavigator.jsp?physicalId={pid}"


def _dsx_api_docs_url() -> str:
    base = (BASE_URL or "").rstrip("/")
    return f"{base}/api-docs" if base else ""


def _dsx_navigator_base_url() -> str:
    return _dsx_web_base_url()


def _resolve_ir_physical_id(session: Optional[dict], body: dict) -> str:
    physical_id = _extract_physical_id(body) or ""
    if physical_id:
        return physical_id
    if not session:
        return ""
    for key in ("id", "object_id"):
        ref = body.get(key)
        if not ref:
            continue
        resolved = _resolve_object_reference(session, str(ref))
        if PHYSICAL_ID_PATTERN.match(resolved):
            return resolved
    return ""


def _ui_request(
    session: dict,
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    data: Optional[dict] = None,
    timeout: int = 30,
) -> requests.Response:
    ui_base = _ui_base_url()
    if not ui_base:
        raise HTTPException(500, detail="DSX UI base URL is not configured")

    url = f"{ui_base}{path}"
    headers = _auth_headers(session["encoded_auth"], {"x-dsx-limit": "1500"})

    if method.upper() == "GET":
        return requests.get(url, params=params, headers=headers, timeout=timeout)
    if method.upper() == "POST":
        return requests.post(url, json=data, headers=headers, timeout=timeout)
    raise HTTPException(500, detail=f"Unsupported HTTP method: {method}")


def _extract_items(body: Any) -> list:
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    for key in ("member", "data", "items", "results", "creationforms", "creationformsets", "formtemplates"):
        value = body.get(key)
        if isinstance(value, list):
            return value
    return []


def _item_reference_id(item: dict) -> str:
    for key in ("id", "physicalid", "physicalId", "name", "code"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_pick_item(item: dict) -> dict[str, Any]:
    legacy_id = ""
    for key in ("id", "name", "code"):
        value = item.get(key)
        if value is not None and str(value).strip():
            legacy_id = str(value).strip()
            break

    physicalid = ""
    for key in ("physicalid", "physicalId"):
        value = item.get(key)
        if value and PHYSICAL_ID_PATTERN.match(str(value).strip()):
            physicalid = str(value).strip().upper()
            break

    item_id = physicalid or legacy_id or _item_reference_id(item)
    if not physicalid and item_id and PHYSICAL_ID_PATTERN.match(item_id):
        physicalid = item_id.upper()

    name = str(item.get("name") or item.get("shortName") or item.get("code") or legacy_id or item_id).strip()
    title = str(
        item.get("title")
        or item.get("displayName")
        or item.get("display")
        or item.get("description")
        or name
    ).strip()
    return {
        "id": item_id,
        "legacy_id": legacy_id if legacy_id and legacy_id != item_id else None,
        "name": name,
        "title": title,
        "physicalid": physicalid or None,
    }


def _normalize_pick_list(body: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in _extract_items(body):
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_pick_item(raw)
        if normalized.get("id"):
            items.append(normalized)
    return items


def _dsx_get_pick_list(session: dict, path: str, params: Optional[dict] = None) -> list[dict[str, Any]]:
    try:
        response = _dsx_request(session, "GET", path, params=params)
        if response.status_code != 200:
            return []
        return _normalize_pick_list(response.json())
    except (requests.RequestException, ValueError):
        return []


def _dsx_probe_pick_list(
    session: dict,
    path: str,
    params: Optional[dict] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entry: dict[str, Any] = {"path": path, "params": params or {}, "status": None, "count": 0}
    try:
        response = _dsx_request(session, "GET", path, params=params)
        entry["status"] = response.status_code
        if response.status_code != 200:
            if response.text:
                entry["detail"] = response.text[:240]
            return [], entry
        items = _normalize_pick_list(response.json())
        entry["count"] = len(items)
        return items, entry
    except (requests.RequestException, ValueError) as exc:
        entry["status"] = "error"
        entry["detail"] = str(exc)
        return [], entry


class _ProbeBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.remaining = limit

    def try_consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0


def _probe_budget_capped_entry() -> dict[str, Any]:
    return {
        "path": "(probe budget)",
        "params": {},
        "status": "capped",
        "count": 0,
        "detail": f"Stopped after {DSX_PROBE_MAX_ATTEMPTS} fallback attempts",
    }


def _get_cached_brands(session: dict) -> list[dict[str, Any]]:
    session_id = session.get("session_id")
    if session_id and session_id in SESSIONS:
        store = SESSIONS[session_id]
        cached = store.get("_brands_cache")
        if isinstance(cached, list):
            return cached
        brands = _dsx_get_pick_list(session, "/brands")
        store["_brands_cache"] = brands
        return brands
    return _dsx_get_pick_list(session, "/brands")


def _brand_variants_from_pick(brand: dict[str, Any]) -> list[str]:
    brand_pid = brand.get("physicalid") or ""
    brand_legacy = brand.get("legacy_id") or ""
    brand_id = brand.get("id") or ""
    brand_name = brand.get("name") or ""
    brand_title = brand.get("title") or ""
    variants = [brand_pid, brand_legacy, brand_id, brand_name, brand_title]
    if brand_pid:
        variants.append(brand_pid.upper())
    return _unique_strings(variants)


def _brand_api_variants(brand: dict[str, Any]) -> list[str]:
    """Id forms suitable for Coal Porter brand filters (no display name/title)."""
    brand_pid = brand.get("physicalid") or ""
    brand_legacy = brand.get("legacy_id") or ""
    brand_id = brand.get("id") or ""
    variants: list[str] = []
    if brand_pid:
        variants.extend([brand_pid, brand_pid.upper()])
    if brand_legacy:
        variants.append(brand_legacy)
    if brand_id:
        variants.append(brand_id)
    return _unique_strings(variants)


def _brand_matches_needle(brand: dict[str, Any], needle: str) -> bool:
    needle = needle.strip().lower()
    if not needle:
        return False
    label = " ".join(
        str(brand.get(key) or "")
        for key in ("title", "name", "id", "legacy_id", "physicalid")
    ).lower()
    return needle in label


def _brand_display_label(brand: dict[str, Any]) -> str:
    return str(brand.get("title") or brand.get("name") or brand.get("id") or "?").strip()


def _brands_availability_hint(brands: list[dict[str, Any]], *, limit: int = 8) -> str:
    labels = [_brand_display_label(brand) for brand in brands[:limit]]
    labels = [label for label in labels if label and label != "?"]
    if not labels:
        return "(none returned from GET /brands)"
    suffix = f" (+{len(brands) - limit} more)" if len(brands) > limit else ""
    return ", ".join(labels) + suffix


def _find_brand_by_id(session: dict, brand_id: str) -> Optional[dict[str, Any]]:
    brand_id = brand_id.strip()
    if not brand_id:
        return None
    brand_id_upper = brand_id.upper()
    for brand in _get_cached_brands(session):
        if brand.get("id") == brand_id or brand.get("legacy_id") == brand_id:
            return brand
        physical = brand.get("physicalid") or ""
        if physical and physical.upper() == brand_id_upper:
            return brand
    return None


def _cache_parent_brand(session: dict, brand: dict[str, Any]) -> None:
    session_id = session.get("session_id")
    if session_id and session_id in SESSIONS:
        SESSIONS[session_id]["_parent_brand"] = brand


def _find_parent_brand(session: dict) -> Optional[dict[str, Any]]:
    session_id = session.get("session_id")
    if session_id and session_id in SESSIONS:
        cached = SESSIONS[session_id].get("_parent_brand")
        if isinstance(cached, dict) and cached.get("id"):
            return cached

    needles = _unique_strings(
        [
            DSX_PARENT_BRAND_NAME,
            "3DExp",
            "3DEXPERIENCE Platform",
            "3dexperience",
            "platform",
            "3dexp",
        ]
    )
    brands = _get_cached_brands(session)
    for needle in needles:
        for brand in brands:
            if _brand_matches_needle(brand, needle):
                _cache_parent_brand(session, brand)
                return brand

    budget = _ProbeBudget(min(DSX_PROBE_MAX_ATTEMPTS, max(3, len(brands))))
    for brand in brands:
        if budget.exhausted:
            break
        brand_id = brand.get("id") or ""
        if not brand_id:
            continue
        items, _probe = _fetch_dsx_product_services_fast(session, brand_id, brand=brand)
        if items:
            _cache_parent_brand(session, brand)
            return brand
    return None


def _fetch_product_services_for_brand(
    session: dict,
    brand: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    brand_id = brand.get("id") or ""
    items, probe = _fetch_dsx_product_services_fast(session, brand_id, brand=brand)
    if not items:
        fallback, fb_probe = _fetch_dsx_product_services(
            session,
            brand_id,
            brand=brand,
            budget=_ProbeBudget(DSX_PROBE_MAX_ATTEMPTS),
        )
        probe.extend(fb_probe)
        items = fallback
    return items, probe


def _fetch_parent_brand_product_services(
    session: dict,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    parent = _find_parent_brand(session)
    if not parent:
        brands = _get_cached_brands(session)
        probe = [
            {
                "path": "/brands",
                "params": {},
                "status": 200,
                "count": len(brands),
                "detail": _brands_availability_hint(brands),
            }
        ]
        return None, [], probe

    items, probe = _fetch_product_services_for_brand(session, parent)
    return parent, items, probe


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _service_lookup_variants(
    session: dict,
    service_id: str,
    *,
    brand: Optional[dict[str, Any]] = None,
) -> list[str]:
    service_id = service_id.strip()
    variants = [service_id]
    if PHYSICAL_ID_PATTERN.match(service_id):
        variants.append(service_id.upper())

    if brand:
        variants = list(_brand_api_variants(brand))
        if service_id and service_id not in variants:
            variants.insert(0, service_id)
        return _unique_strings(variants)

    for cached_brand in _get_cached_brands(session):
        brand_id = cached_brand.get("id") or ""
        brand_legacy = cached_brand.get("legacy_id") or ""
        brand_pid = cached_brand.get("physicalid") or ""
        brand_name = cached_brand.get("name") or ""
        if service_id in (brand_id, brand_legacy, brand_pid, brand_pid.upper() if brand_pid else "", brand_name):
            variants.extend([brand_pid, brand_legacy, brand_id, brand_name])

    return _unique_strings(variants)


def _summarize_probe(probe: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in probe[:limit]:
        params = entry.get("params") or {}
        param_key = next(iter(params), None)
        item: dict[str, Any] = {
            "path": entry.get("path"),
            "param": param_key,
            "param_value": params.get(param_key) if param_key else None,
            "status": entry.get("status"),
            "count": entry.get("count", 0),
        }
        if entry.get("detail"):
            item["detail"] = entry.get("detail")
        summary.append(item)
    if len(probe) > limit:
        summary.append({"truncated": len(probe) - limit})
    return summary


def _collect_pick_items(
    seen: set[str],
    items: list[dict[str, Any]],
    batch: list[dict[str, Any]],
) -> None:
    for entry_item in batch:
        entry_id = entry_item.get("id") or ""
        if entry_id and entry_id not in seen:
            seen.add(entry_id)
            items.append(entry_item)


def _fetch_dsx_product_services(
    session: dict,
    brand_id: str,
    *,
    brand: Optional[dict[str, Any]] = None,
    budget: Optional[_ProbeBudget] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    brand_variants = _service_lookup_variants(session, brand_id, brand=brand)
    param_keys = _unique_strings(
        [
            "brand",
            "brand_eno_id",
            "brand_id",
            "brandId",
            "brandPhysicalId",
            "physicalid",
            "physicalId",
            "enoId",
            "parent",
            "scope",
            "service",
        ]
    )
    path_variants = _unique_strings(
        [
            DSX_PRODUCT_SERVICES_PATH,
            "/services",
            "/productservices",
            "/product-services",
            "/dsxservices",
            *[f"/brands/{variant}/services" for variant in brand_variants],
        ]
    )

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    probe: list[dict[str, Any]] = []
    unfiltered_attempts = [
        (DSX_PRODUCT_SERVICES_PATH or "/services", {"state": "Applicable"}),
        (DSX_PRODUCT_SERVICES_PATH or "/services", None),
        ("/productservices", {"state": "Applicable"}),
        ("/productservices", None),
        ("/product-services", {"state": "Applicable"}),
        ("/dsxservices", {"state": "Applicable"}),
    ]

    for path, params in unfiltered_attempts:
        if budget is not None and not budget.try_consume():
            probe.append(_probe_budget_capped_entry())
            return items, probe
        batch, entry = _dsx_probe_pick_list(session, path, params=params)
        probe.append(entry)
        _collect_pick_items(seen, items, batch)
        if items:
            return items, probe

    ui_base = _ui_base_url()
    if ui_base:
        for params in ({"state": "Applicable"}, None):
            if budget is not None and not budget.try_consume():
                probe.append(_probe_budget_capped_entry())
                return items, probe
            entry: dict[str, Any] = {
                "path": f"{ui_base}/services",
                "params": params or {},
                "status": None,
                "count": 0,
            }
            try:
                response = requests.get(
                    f"{ui_base}/services",
                    params=params,
                    headers=_auth_headers(session["encoded_auth"], {"x-dsx-limit": "1500"}),
                    timeout=30,
                )
                entry["status"] = response.status_code
                if response.status_code != 200:
                    if response.text:
                        entry["detail"] = response.text[:240]
                else:
                    batch = _normalize_pick_list(response.json())
                    entry["count"] = len(batch)
                    _collect_pick_items(seen, items, batch)
            except (requests.RequestException, ValueError) as exc:
                entry["status"] = "error"
                entry["detail"] = str(exc)
            probe.append(entry)
            if items:
                return items, probe

    extra_params = [{"state": "Applicable"}, {"applicable": "true"}, {"current": "true"}, {}]
    for path in path_variants:
        params_variants: list[Optional[dict]] = [None]
        if "{" not in path:
            params_variants = []
            for key in param_keys:
                for variant in brand_variants:
                    for extra in extra_params:
                        params_variants.append({key: variant, **extra})
        for params in params_variants:
            if budget is not None and not budget.try_consume():
                probe.append(_probe_budget_capped_entry())
                return items, probe
            batch, entry = _dsx_probe_pick_list(session, path, params=params)
            probe.append(entry)
            _collect_pick_items(seen, items, batch)
            if items:
                return items, probe
    return items, probe


def _fetch_dsx_product_services_fast(
    session: dict,
    brand_id: str,
    *,
    brand: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Coal Porter fast path for product services under a parent brand."""
    if brand:
        brand_variants = _brand_api_variants(brand)
    else:
        brand_variants = _service_lookup_variants(session, brand_id)

    probe: list[dict[str, Any]] = []
    services_path = DSX_PRODUCT_SERVICES_PATH or "/services"
    physical_ids = [v for v in brand_variants if PHYSICAL_ID_PATTERN.match(v)]
    legacy_ids = [v for v in brand_variants if not PHYSICAL_ID_PATTERN.match(v)]

    attempts: list[tuple[str, Optional[dict[str, str]]]] = []
    for pid in physical_ids:
        attempts.extend(
            [
                (services_path, {"brand": pid, "state": "Applicable"}),
                (services_path, {"brand": pid}),
                (services_path, {"brandPhysicalId": pid, "state": "Applicable"}),
                (services_path, {"brandPhysicalId": pid}),
                (services_path, {"brandId": pid, "state": "Applicable"}),
                (services_path, {"brandId": pid}),
                (services_path, {"physicalId": pid}),
                ("/productservices", {"brand": pid, "state": "Applicable"}),
                ("/productservices", {"brand": pid}),
                (f"/brands/{pid}/services", None),
            ]
        )
    for oid in legacy_ids:
        attempts.extend(
            [
                (services_path, {"brand_eno_id": oid, "state": "Applicable"}),
                (services_path, {"brand_eno_id": oid}),
                (services_path, {"brand": oid, "state": "Applicable"}),
                (services_path, {"brand": oid}),
                (services_path, {"brandId": oid, "state": "Applicable"}),
                (services_path, {"brandId": oid}),
                (services_path, {"enoId": oid, "state": "Applicable"}),
                (services_path, {"enoId": oid}),
                ("/productservices", {"brand_eno_id": oid}),
                ("/productservices", {"brand": oid}),
                (f"/brands/{oid}/services", None),
            ]
        )

    seen_attempts: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for path, params in attempts:
        param_key = tuple(sorted((params or {}).items()))
        attempt_key = (path, param_key)
        if attempt_key in seen_attempts:
            continue
        seen_attempts.add(attempt_key)
        batch, entry = _dsx_probe_pick_list(session, path, params=params)
        entry["fast_path"] = True
        probe.append(entry)
        if batch:
            return batch, probe

    for params in ({"state": "Applicable"}, None):
        batch, entry = _dsx_probe_pick_list(session, services_path, params=params)
        entry["fast_path"] = True
        probe.append(entry)
        if batch:
            return batch, probe
    for alt_path in ("/product-services", "/dsxservices"):
        batch, entry = _dsx_probe_pick_list(session, alt_path, params={"state": "Applicable"})
        entry["fast_path"] = True
        probe.append(entry)
        if batch:
            return batch, probe

    return [], probe


def _program_matches_brand(program: dict[str, Any], brand: dict[str, Any]) -> bool:
    for needle in _brand_variants_from_pick(brand):
        if _program_matches_search(program, needle, needle):
            return True
    return False


def _fetch_programs_for_brand(
    session: dict,
    brand: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    brand_id = brand.get("id") or brand.get("physicalid") or ""
    if not brand_id:
        return [], []

    items, probe = _fetch_dsx_programs_direct(
        session,
        brand_id,
        mode="brand",
        budget=_ProbeBudget(DSX_PROBE_MAX_ATTEMPTS),
    )
    if items:
        return items, probe

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []

    def _collect_programs(batch: list[dict[str, Any]]) -> None:
        for program in batch:
            program_id = program.get("id") or ""
            if program_id and program_id not in seen:
                seen.add(program_id)
                merged.append(program)

    programs_path = DSX_PROGRAMS_PATH or "/programs"
    for params in ({"state": "Applicable"}, None):
        batch, entry = _dsx_probe_pick_list(session, programs_path, params=params)
        entry["fallback"] = "unfiltered_programs"
        probe.append(entry)
        _collect_programs(batch)
        if merged:
            break

    if not merged:
        for needle in _brand_variants_from_pick(brand)[:4]:
            if not needle:
                continue
            for param_key in ("q", "search", "name"):
                batch, entry = _dsx_probe_pick_list(session, programs_path, {param_key: needle})
                entry["fallback"] = "program_search"
                probe.append(entry)
                _collect_programs(batch)
                if merged:
                    break
            if merged:
                break

    if merged:
        filtered = [program for program in merged if _program_matches_brand(program, brand)]
        if filtered:
            return filtered, probe
        return merged, probe

    return [], probe


def _fetch_programs_for_product_service_fast(
    session: dict,
    service_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Coal Porter fast path: GET /programs?service={physicalId}."""
    probe: list[dict[str, Any]] = []
    path = DSX_PROGRAMS_PATH or "/programs"
    for variant in _id_variants(service_id):
        params = {DSX_PROGRAMS_SERVICE_PARAM: variant}
        batch, entry = _dsx_probe_pick_list(session, path, params=params)
        entry["fast_path"] = True
        probe.append(entry)
        if batch:
            return batch, probe
    return [], probe


def _enrich_programs_from_product_service(
    programs: list[dict[str, Any]],
    ps_label: str,
    seen: set[str],
    merged: list[dict[str, Any]],
) -> None:
    for program in programs:
        program_id = program.get("id") or ""
        if not program_id or program_id in seen:
            continue
        seen.add(program_id)
        enriched = dict(program)
        if ps_label and ps_label not in (enriched.get("title") or ""):
            enriched["title"] = f"{enriched.get('title') or enriched.get('name') or program_id} — {ps_label}"
        merged.append(enriched)


def _merge_programs_for_product_services(
    session: dict,
    product_services: list[dict[str, Any]],
    *,
    use_fast_path: bool = True,
    budget: Optional[_ProbeBudget] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    probe: list[dict[str, Any]] = []
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for product_service in product_services:
        ps_id = product_service.get("id") or ""
        if not ps_id:
            continue
        if use_fast_path:
            programs, program_probe = _fetch_programs_for_product_service_fast(session, ps_id)
        else:
            if budget is not None and budget.exhausted:
                probe.append(_probe_budget_capped_entry())
                return merged, probe
            programs, program_probe = _fetch_dsx_programs_direct(session, ps_id, mode="service", budget=budget)
        probe.extend(program_probe)
        ps_label = product_service.get("title") or product_service.get("name") or ""
        _enrich_programs_from_product_service(programs, ps_label, seen, merged)
    return merged, probe


def _fetch_dsx_programs_fast(
    session: dict,
    brand_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    product_services, probe = _fetch_dsx_product_services_fast(session, brand_id)
    if not product_services:
        return [], probe
    merged, program_probe = _merge_programs_for_product_services(session, product_services, use_fast_path=True)
    probe.extend(program_probe)
    return merged, probe


def _id_variants(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    variants = [value]
    if PHYSICAL_ID_PATTERN.match(value):
        variants.append(value.upper())
    return _unique_strings(variants)


def _fetch_dsx_programs_direct(
    session: dict,
    parent_id: str,
    *,
    mode: str = "auto",
    budget: Optional[_ProbeBudget] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if mode == "service":
        parent_variants = _id_variants(parent_id)
    else:
        parent_variants = _service_lookup_variants(session, parent_id)

    if mode == "brand":
        param_keys = _unique_strings(
            [
                "brand",
                "brand_id",
                "brandId",
                "brandPhysicalId",
                "physicalid",
                "physicalId",
            ]
        )
        path_variants = _unique_strings(
            [
                DSX_PROGRAMS_PATH,
                "/programs",
                *[f"/brands/{variant}/programs" for variant in parent_variants],
            ]
        )
    elif mode == "service":
        param_keys = _unique_strings(
            [
                DSX_PROGRAMS_SERVICE_PARAM,
                "service",
                "service_id",
                "serviceId",
                "servicePhysicalId",
                "physicalid",
                "physicalId",
            ]
        )
        path_variants = _unique_strings(
            [
                DSX_PROGRAMS_PATH,
                "/programs",
                *[f"/services/{variant}/programs" for variant in parent_variants],
            ]
        )
    else:
        param_keys = _unique_strings(
            [
                DSX_PROGRAMS_SERVICE_PARAM,
                "brand",
                "brand_id",
                "brandId",
                "brandPhysicalId",
                "physicalid",
                "physicalId",
                "service",
                "service_id",
                "serviceId",
            ]
        )
        path_variants = _unique_strings(
            [
                DSX_PROGRAMS_PATH,
                "/programs",
                *[f"/brands/{variant}/programs" for variant in parent_variants],
                *[f"/services/{variant}/programs" for variant in parent_variants],
            ]
        )

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    probe: list[dict[str, Any]] = []
    extra_params = [{"state": "Applicable"}, {"applicable": "true"}, {"current": "true"}, {}]
    for path in path_variants:
        params_variants: list[Optional[dict]] = [None]
        if "{" not in path:
            params_variants = []
            for key in param_keys:
                for variant in parent_variants:
                    for extra in extra_params:
                        params_variants.append({key: variant, **extra})
        for params in params_variants:
            if budget is not None and not budget.try_consume():
                probe.append(_probe_budget_capped_entry())
                return items, probe
            batch, entry = _dsx_probe_pick_list(session, path, params=params)
            probe.append(entry)
            _collect_pick_items(seen, items, batch)
            if items:
                return items, probe
    return items, probe


def _program_lookup_variants(session: dict, program_id: str) -> list[str]:
    program_id = program_id.strip()
    variants = [program_id]
    if PHYSICAL_ID_PATTERN.match(program_id):
        variants.append(program_id.upper())
    return _unique_strings(variants)


def _dsx_get_raw_items(session: dict, path: str, params: Optional[dict] = None) -> list[dict]:
    try:
        response = _dsx_request(session, "GET", path, params=params)
        if response.status_code != 200:
            return []
        return [item for item in _extract_items(response.json()) if isinstance(item, dict)]
    except (requests.RequestException, ValueError):
        return []


def _fetch_dsx_services(session: dict) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    probe: list[dict[str, Any]] = []
    path_candidates = _unique_strings([DSX_SERVICES_PATH, "/brands", "/services"])
    for path in path_candidates:
        batch, entry = _dsx_probe_pick_list(session, path)
        probe.append(entry)
        for entry_item in batch:
            entry_id = entry_item.get("id") or ""
            if entry_id and entry_id not in seen:
                seen.add(entry_id)
                items.append(entry_item)
        if items:
            break
    return items, probe


def _fetch_dsx_programs_for_product_service(
    session: dict,
    product_service_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    product_service_id = product_service_id.strip()
    if not product_service_id:
        return [], []

    items, probe = _fetch_programs_for_product_service_fast(session, product_service_id)
    if items:
        return items, probe

    budget = _ProbeBudget(DSX_PROBE_MAX_ATTEMPTS)
    items, fallback_probe = _fetch_dsx_programs_direct(
        session,
        product_service_id,
        mode="service",
        budget=budget,
    )
    probe.extend(fallback_probe)
    if budget.exhausted and not items:
        probe.append(_probe_budget_capped_entry())
    return items, probe


def _fetch_dsx_programs(session: dict, service_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    service_id = service_id.strip()
    if not service_id:
        return [], []

    items, probe = _fetch_dsx_programs_fast(session, service_id)
    if items:
        return items, probe

    budget = _ProbeBudget(DSX_PROBE_MAX_ATTEMPTS)

    items, brand_probe = _fetch_dsx_programs_direct(session, service_id, mode="brand", budget=budget)
    probe.extend(brand_probe)
    if items:
        return items, probe
    if budget.exhausted:
        probe.append(_probe_budget_capped_entry())
        return [], probe

    items, auto_probe = _fetch_dsx_programs_direct(session, service_id, mode="auto", budget=budget)
    probe.extend(auto_probe)
    if items:
        return items, probe
    if budget.exhausted:
        probe.append(_probe_budget_capped_entry())
        return [], probe

    product_services, svc_probe = _fetch_dsx_product_services(session, service_id, budget=budget)
    probe.extend(svc_probe)
    if budget.exhausted and not product_services:
        probe.append(_probe_budget_capped_entry())
        return [], probe

    merged, program_probe = _merge_programs_for_product_services(
        session,
        product_services,
        use_fast_path=False,
        budget=budget,
    )
    probe.extend(program_probe)
    if budget.exhausted and not merged:
        probe.append(_probe_budget_capped_entry())
    return merged, probe


def _fetch_dsx_release_raw_items(session: dict, program_id: str) -> list[dict]:
    program_id = program_id.strip()
    if not program_id:
        return []

    program_variants = _program_lookup_variants(session, program_id)
    path_candidates = []
    for variant in program_variants:
        path_candidates.extend(
            [
                f"/programs/{variant}/releases",
                f"/programs/{variant}/release",
                DSX_RELEASES_PATH.replace("{program_id}", variant),
                f"/releases/{variant}",
            ]
        )
    path_candidates = _unique_strings(path_candidates)
    param_keys = _unique_strings(
        [
            DSX_RELEASES_PROGRAM_PARAM,
            "program",
            "program_id",
            "programId",
            "programPhysicalId",
            "physicalid",
            "physicalId",
        ]
    )

    seen: set[str] = set()
    items: list[dict] = []
    for path in path_candidates:
        raw_items = _dsx_get_raw_items(session, path)
        if not raw_items:
            try:
                response = _dsx_request(session, "GET", path)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict):
                        raw_items = [payload]
            except (requests.RequestException, ValueError):
                raw_items = []
        for entry in raw_items:
            entry_id = _item_reference_id(entry)
            if entry_id and entry_id not in seen:
                seen.add(entry_id)
                items.append(entry)
        if items:
            break

    if not items:
        for params in [
            {key: variant}
            for key in param_keys
            for variant in program_variants
        ]:
            for entry in _dsx_get_raw_items(session, "/releases", params=params):
                entry_id = _item_reference_id(entry)
                if entry_id and entry_id not in seen:
                    seen.add(entry_id)
                    items.append(entry)
            if items:
                break

    if not items:
        for variant in program_variants:
            for path in (f"/programs/{variant}",):
                try:
                    response = _dsx_request(session, "GET", path)
                    if response.status_code != 200:
                        continue
                    payload = response.json()
                    candidates: list[dict] = []
                    if isinstance(payload, dict):
                        for key in ("releases", "release", "targetRelease", "currentRelease", "items", "data"):
                            nested = payload.get(key)
                            if isinstance(nested, list):
                                candidates.extend(entry for entry in nested if isinstance(entry, dict))
                            elif isinstance(nested, dict):
                                candidates.append(nested)
                        if any(payload.get(k) for k in ("rel_eno_id", "rel_name", "rel_title", "physicalid", "id")):
                            candidates.append(payload)
                    for entry in candidates:
                        entry_id = _item_reference_id(entry)
                        if entry_id and entry_id not in seen:
                            seen.add(entry_id)
                            items.append(entry)
                    if items:
                        break
                except (requests.RequestException, ValueError):
                    continue
            if items:
                break

    return items


PRG_CODE_PATTERN = re.compile(r"PRG\d+", re.IGNORECASE)
REL_CODE_PATTERN = re.compile(r"REL\d+", re.IGNORECASE)
VERSION_SUFFIX_PATTERN = re.compile(r"^(?:R\d+x?|\d+(?:\.\d+)*x?)$", re.IGNORECASE)


def _program_reference_id(program: dict) -> str:
    """Physical ID or ENO OID suitable for detection_level_eno_id."""
    pick = _normalize_pick_item(program)
    physical = pick.get("physicalid") or ""
    if physical and PHYSICAL_ID_PATTERN.match(str(physical)):
        return str(physical).upper()
    for candidate in (pick.get("id"), pick.get("legacy_id")):
        if not candidate:
            continue
        text = str(candidate).strip()
        if PHYSICAL_ID_PATTERN.match(text):
            return text.upper()
        if ENO_OID_PATTERN.match(text):
            return text
    return ""


def _physical_id_from_program(program: dict) -> str:
    return _program_reference_id(program)


def _hyphenated_version_parts(text: str) -> tuple[str, str]:
    if "-" not in text:
        return "", ""
    prefix, suffix = text.rsplit("-", 1)
    prefix = prefix.strip()
    suffix = suffix.strip()
    if suffix and VERSION_SUFFIX_PATTERN.match(suffix):
        return prefix, suffix
    return "", ""


def _program_search_terms(name: str, title: str) -> list[str]:
    terms: list[str] = []
    for text in _unique_strings([name, title]):
        terms.append(text)
        match = PRG_CODE_PATTERN.search(text)
        if match:
            terms.append(match.group(0).upper())
        rel_match = REL_CODE_PATTERN.search(text)
        if rel_match:
            terms.append(rel_match.group(0).upper())
        prefix, suffix = _hyphenated_version_parts(text)
        if suffix:
            terms.append(suffix)
        if prefix:
            terms.append(prefix)
    return _unique_strings(terms)


def _fetch_all_parent_brand_programs(session: dict) -> list[dict[str, Any]]:
    session_id = session.get("session_id")
    cache_key = "_parent_brand_programs"
    if session_id and session_id in SESSIONS:
        cached = SESSIONS[session_id].get(cache_key)
        if isinstance(cached, list):
            return cached

    _parent, product_services, _probe = _fetch_parent_brand_product_services(session)
    if not product_services:
        parent = _parent or _find_parent_brand(session)
        if parent:
            programs, _program_probe = _fetch_programs_for_brand(session, parent)
            if session_id and session_id in SESSIONS:
                SESSIONS[session_id][cache_key] = programs
            return programs
        return []

    programs, _program_probe = _merge_programs_for_product_services(
        session,
        product_services,
        use_fast_path=True,
        budget=_ProbeBudget(DSX_PROBE_MAX_ATTEMPTS),
    )
    if session_id and session_id in SESSIONS:
        SESSIONS[session_id][cache_key] = programs
    return programs


def _program_matches_exact(program: dict, name: str, title: str) -> bool:
    name_l = name.lower().strip()
    title_l = title.lower().strip()
    if not name_l and not title_l:
        return False
    for key in ("name", "code", "shortName", "id"):
        value = program.get(key)
        if value is None:
            continue
        candidate = str(value).lower().strip()
        if name_l and candidate == name_l:
            return True
    for key in ("title", "displayName", "display"):
        value = program.get(key)
        if value is None:
            continue
        candidate = str(value).lower().strip()
        if title_l and candidate == title_l:
            return True
    return False


def _program_matches_search(program: dict, name: str, title: str) -> bool:
    name_l = name.lower().strip()
    title_l = title.lower().strip()
    if not name_l and not title_l:
        return False
    pick = _normalize_pick_item(program)
    combined = " ".join(
        str(pick.get(key) or "")
        for key in ("name", "title", "legacy_id", "id")
    ).lower()
    for query in _unique_strings([name, title]):
        prefix, version = _hyphenated_version_parts(query)
        if prefix and version and combined:
            if prefix.lower() in combined and version.lower() in combined:
                return True
    for key in ("name", "title", "id", "physicalid", "physicalId", "code", "shortName"):
        value = program.get(key)
        if value is None:
            continue
        candidate = str(value).lower().strip()
        if not candidate:
            continue
        if name_l and (candidate == name_l or name_l in candidate or candidate in name_l):
            return True
        if title_l and (candidate == title_l or title_l in candidate or candidate in title_l):
            return True
    if combined:
        if name_l and (name_l in combined or combined in name_l):
            return True
        if title_l and (title_l in combined or combined in title_l):
            return True
    return False


def _search_programs_in_list(programs: list, name: str, title: str) -> str:
    for program in programs:
        if not isinstance(program, dict):
            continue
        if _program_matches_exact(program, name, title):
            found = _program_reference_id(program)
            if found:
                return found
    for program in programs:
        if not isinstance(program, dict):
            continue
        if _program_matches_search(program, name, title):
            found = _program_reference_id(program)
            if found:
                return found
    return ""


def _resolve_program_reference(
    session: dict,
    name: str,
    title: str = "",
    *,
    service_id: str = "",
    brand_id: str = "",
) -> str:
    for candidate in (name, title):
        if not candidate:
            continue
        text = str(candidate).strip()
        if not text:
            continue
        if PHYSICAL_ID_PATTERN.match(text):
            return text.upper()
        if ENO_OID_PATTERN.match(text):
            return text
        resolved = _resolve_object_reference(session, text)
        if PHYSICAL_ID_PATTERN.match(resolved):
            return resolved.upper()
        if ENO_OID_PATTERN.match(resolved):
            nested = _resolve_object_reference(session, resolved)
            if PHYSICAL_ID_PATTERN.match(nested):
                return nested.upper()
            return resolved

    service_id = service_id.strip()
    brand_id = brand_id.strip()
    if service_id and not brand_id:
        brand = _find_brand_by_id(session, service_id)
        if brand:
            brand_id = service_id
            service_id = ""

    if service_id:
        programs, _probe = _fetch_dsx_programs(session, service_id)
        found = _search_programs_in_list(programs, name, title)
        if found:
            return found

    if brand_id:
        brand = _find_brand_by_id(session, brand_id)
        if brand:
            programs, _probe = _fetch_programs_for_brand(session, brand)
            found = _search_programs_in_list(programs, name, title)
            if found:
                return found

    for search_term in _program_search_terms(name, title):
        if not search_term:
            continue
        for param_key in ("name", "q", "search", "code"):
            items = _dsx_get_pick_list(
                session,
                DSX_PROGRAMS_PATH or "/programs",
                {param_key: search_term},
            )
            found = _search_programs_in_list(items, name, title)
            if found:
                return found

    programs = _fetch_all_parent_brand_programs(session)
    found = _search_programs_in_list(programs, name, title)
    if found:
        return found

    return ""


def _search_pick_items_in_list(items: list, name: str, title: str = "") -> str:
    for item in items:
        if not isinstance(item, dict):
            continue
        if _program_matches_exact(item, name, title):
            found = _program_reference_id(item)
            if found:
                return found
    for item in items:
        if not isinstance(item, dict):
            continue
        if _program_matches_search(item, name, title):
            found = _program_reference_id(item)
            if found:
                return found
    return ""


def _resolve_release_reference(
    session: dict,
    rel_name: str,
    rel_title: str = "",
    *,
    service_id: str = "",
) -> str:
    for candidate in (rel_name, rel_title):
        if not candidate:
            continue
        text = str(candidate).strip()
        if not text:
            continue
        if PHYSICAL_ID_PATTERN.match(text):
            return text.upper()
        resolved = _resolve_object_reference(session, text)
        if PHYSICAL_ID_PATTERN.match(resolved):
            return resolved.upper()
        if ENO_OID_PATTERN.match(resolved):
            nested = _resolve_object_reference(session, resolved)
            if PHYSICAL_ID_PATTERN.match(nested):
                return nested.upper()

    service_id = service_id.strip()
    if service_id:
        programs, _probe = _fetch_dsx_programs(session, service_id)
        for program in programs:
            if not isinstance(program, dict):
                continue
            program_id = program.get("id") or program.get("physicalid") or program.get("physicalId") or ""
            if not program_id:
                continue
            releases = _fetch_dsx_release_raw_items(session, str(program_id))
            found = _search_pick_items_in_list(releases, rel_name, rel_title)
            if found:
                return found

    for search_term in _unique_strings([rel_name, rel_title]):
        if not search_term:
            continue
        for param_key in ("name", "q", "search", "code"):
            items = _dsx_get_pick_list(session, "/releases", {param_key: search_term})
            found = _search_pick_items_in_list(items, rel_name, rel_title)
            if found:
                return found

    programs = _fetch_all_parent_brand_programs(session)
    for program in programs:
        if not isinstance(program, dict):
            continue
        program_id = _program_reference_id(program)
        if not program_id:
            continue
        releases = _fetch_dsx_release_raw_items(session, str(program_id))
        found = _search_pick_items_in_list(releases, rel_name, rel_title)
        if found:
            return found

    return ""


def _parse_release_query(query: str) -> tuple[str, str]:
    text = query.strip()
    if not text:
        return "", ""
    prefix, suffix = _hyphenated_version_parts(text)
    if suffix:
        return (prefix or text).strip(), suffix.strip()
    return text, text


def _fetch_release_object(session: dict, release_ref: str) -> Optional[dict]:
    ref = release_ref.strip()
    if not ref:
        return None
    variants = _id_variants(ref) if PHYSICAL_ID_PATTERN.match(ref) else [ref]
    for variant in _unique_strings(variants):
        try:
            response = _dsx_request(session, "GET", f"/releases/{variant}")
            if response.status_code == 200:
                body = response.json()
                if isinstance(body, dict):
                    return body
        except (requests.RequestException, ValueError):
            continue
    return None


def _search_release_candidates(
    session: dict,
    rel_name: str,
    rel_title: str,
) -> tuple[list[dict], list[dict]]:
    probe: list[dict] = []
    seen: set[str] = set()
    items: list[dict] = []
    search_terms = _unique_strings(
        _program_search_terms(rel_name, rel_title) + [rel_name, rel_title]
    )
    for search_term in search_terms:
        if not search_term:
            continue
        for param_key in ("name", "q", "search", "code", "title"):
            batch, entry = _dsx_probe_pick_list(session, "/releases", {param_key: search_term})
            entry["api"] = "releases_search"
            probe.append(entry)
            for item in batch:
                if not isinstance(item, dict):
                    continue
                rid = _item_reference_id(item)
                if rid and rid not in seen:
                    seen.add(rid)
                    items.append(item)
    return items, probe


def _pick_release_match(items: list[dict], rel_name: str, rel_title: str) -> Optional[dict]:
    if not items:
        return None
    for item in items:
        if _program_matches_exact(item, rel_name, rel_title):
            return item
    for item in items:
        if _program_matches_search(item, rel_name, rel_title):
            return item
    return items[0]


def _summarize_release_probe(probe: list[dict]) -> str:
    if not probe:
        return "no list API results; tried scanning programs under the configured parent brand."
    parts: list[str] = []
    for entry in probe[:6]:
        path = entry.get("path") or "?"
        status = entry.get("status")
        count = entry.get("count")
        params = entry.get("params") or {}
        param_note = ""
        if params:
            key = next(iter(params))
            param_note = f"?{key}={params.get(key)}"
        parts.append(f"{path}{param_note}→{status}({count})")
    return "; ".join(parts)


def _lookup_release_by_query(session: dict, query: str) -> tuple[Optional[dict], list[dict]]:
    probe: list[dict] = []
    text = query.strip()
    if not text:
        return None, probe

    if PHYSICAL_ID_PATTERN.match(text):
        obj = _fetch_release_object(session, text)
        if obj:
            return obj, probe
        return {"physicalid": text.upper(), "name": "", "title": text}, probe

    rel_name, rel_title = _parse_release_query(text)
    rel_id = _resolve_release_reference(session, rel_name, rel_title, service_id="")
    if rel_id:
        obj = _fetch_release_object(session, rel_id)
        if obj:
            return obj, probe
        return {
            "physicalid": rel_id,
            "name": rel_name,
            "title": rel_title or rel_name,
        }, probe

    items, search_probe = _search_release_candidates(session, rel_name, rel_title)
    probe.extend(search_probe)
    picked = _pick_release_match(items, rel_name, rel_title)
    if picked:
        return picked, probe

    full_name, full_title = text, text
    if full_name != rel_name or full_title != rel_title:
        rel_id = _resolve_release_reference(session, full_name, full_title, service_id="")
        if rel_id:
            obj = _fetch_release_object(session, rel_id)
            if obj:
                return obj, probe
            return {
                "physicalid": rel_id,
                "name": full_name,
                "title": full_title,
            }, probe

    return None, probe


def _resolve_feature_reference(session: dict, feature_name: str) -> str:
    name = (feature_name or "").strip()
    if not name:
        return ""
    if PHYSICAL_ID_PATTERN.match(name):
        return name.upper()
    resolved = _resolve_object_reference(session, name)
    if PHYSICAL_ID_PATTERN.match(resolved):
        return resolved.upper()
    if ENO_OID_PATTERN.match(resolved):
        nested = _resolve_object_reference(session, resolved)
        if PHYSICAL_ID_PATTERN.match(nested):
            return nested.upper()

    for path in ("/features", "/classificationelements", "/collections", "/classification/elements"):
        for param_key in ("name", "q", "search", "title"):
            items = _dsx_get_pick_list(session, path, {param_key: name})
            found = _search_pick_items_in_list(items, name, name)
            if found:
                return found

    return ""


def _normalize_detection_fields(
    session: dict,
    service_id: str = "",
    program_name: Optional[str] = None,
    program_title: Optional[str] = None,
    *,
    program_id: str = "",
    brand_id: str = "",
) -> dict[str, str]:
    eno_id = ""
    program_id = program_id.strip()
    if program_id:
        resolved = _resolve_object_reference(session, program_id)
        if PHYSICAL_ID_PATTERN.match(resolved):
            eno_id = resolved.upper()
        elif ENO_OID_PATTERN.match(resolved):
            eno_id = resolved
        elif PHYSICAL_ID_PATTERN.match(program_id):
            eno_id = program_id.upper()
        elif ENO_OID_PATTERN.match(program_id):
            eno_id = program_id

    name = (program_name or program_title or "").strip()
    title = (program_title or program_name or name).strip()

    if not eno_id and (name or title):
        eno_id = _resolve_program_reference(
            session,
            name,
            title,
            service_id=service_id.strip(),
            brand_id=brand_id.strip(),
        )

    return {
        "detection_level_eno_id": eno_id,
        "detection_level_name": name,
        "detection_level_title": title,
    }


def _normalize_release_fields(session: dict, item: dict) -> dict[str, str]:
    pick = _normalize_pick_item(item)
    rel_eno_id = pick.get("physicalid") or pick.get("id") or ""
    if rel_eno_id and PHYSICAL_ID_PATTERN.match(str(rel_eno_id)):
        rel_eno_id = str(rel_eno_id).upper()
    elif rel_eno_id:
        resolved = _resolve_object_reference(session, str(rel_eno_id))
        if PHYSICAL_ID_PATTERN.match(resolved):
            rel_eno_id = resolved
        else:
            rel_eno_id = str(rel_eno_id)

    rel_name = str(item.get("name") or item.get("shortName") or item.get("code") or pick.get("name") or "").strip()
    rel_title = str(
        item.get("title")
        or item.get("displayName")
        or item.get("display")
        or pick.get("title")
        or rel_name
    ).strip()
    rel_level_id = str(
        item.get("levelId")
        or item.get("level_id")
        or item.get("rel_level_id")
        or item.get("level")
        or rel_name
    ).strip()

    return {
        "rel_eno_id": rel_eno_id,
        "rel_name": rel_name,
        "rel_title": rel_title,
        "rel_level_id": rel_level_id or rel_name,
    }


def _merge_field_value(normalized: dict[str, str], key: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text and key in SAVED_FORM_FIELD_KEYS:
        normalized[key] = text


def _physical_id_value(obj: dict) -> Optional[str]:
    for key in ("physicalId", "physicalid", "eno_id", "enoId", "id"):
        value = obj.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _flatten_form_template_fields(raw_fields: Any) -> dict[str, Any]:
    if isinstance(raw_fields, str):
        try:
            raw_fields = json.loads(raw_fields)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw_fields, dict):
        return {}

    flat: dict[str, Any] = {}
    for key, val in raw_fields.items():
        if isinstance(val, dict) and "value" in val:
            flat[key] = val.get("value")
        elif not isinstance(val, (dict, list)):
            flat[key] = val
    return flat


def _extract_nested_objects(source: dict, normalized: dict[str, str]) -> None:
    release_keys = ("target_release", "targetRelease", "release", "target_release_object", "TargetedRelease")
    feature_keys = ("feature", "concerned_feature", "concernedFeature", "ConcernedFeature")
    detection_keys = ("detection_level", "detectionLevel", "detection_program", "detectionProgram", "DetectionProgram", "DetectionLevel")

    for key in release_keys:
        obj = source.get(key)
        if isinstance(obj, dict):
            _merge_field_value(normalized, "rel_eno_id", _physical_id_value(obj))
            _merge_field_value(normalized, "rel_name", obj.get("name"))
            _merge_field_value(normalized, "rel_title", obj.get("title"))
            _merge_field_value(normalized, "rel_level_id", obj.get("level_id") or obj.get("levelId"))

    for key in feature_keys:
        obj = source.get(key)
        if isinstance(obj, dict):
            _merge_field_value(normalized, "feature_eno_id", _physical_id_value(obj))
            _merge_field_value(normalized, "feature_name", obj.get("name") or obj.get("title"))

    for key in detection_keys:
        obj = source.get(key)
        if isinstance(obj, dict):
            _merge_field_value(
                normalized,
                "detection_level_eno_id",
                _physical_id_value(obj),
            )
            _merge_field_value(normalized, "detection_level_name", obj.get("name"))
            _merge_field_value(normalized, "detection_level_title", obj.get("title"))


def _coalesce_form_field_source(raw: dict) -> dict:
    source = dict(raw)
    fields_raw = raw.get("fields")
    if isinstance(fields_raw, str) or isinstance(fields_raw, dict):
        flat = _flatten_form_template_fields(fields_raw)
        if flat:
            source = {**source, **flat}
            source["fields"] = flat
    for nested_key in ("attributes", "formData", "form_data", "defaultValues", "default_values", "values", "content"):
        nested = raw.get(nested_key)
        if isinstance(nested, dict):
            source = {**source, **nested}
            break
    return source


def _parse_dsx_display(display: str) -> dict[str, str]:
    if not display or not isinstance(display, str):
        return {}
    text = display.strip()
    if " - : " in text:
        parts = [part.strip() for part in re.split(r"\s*-\s*:\s*", text) if part.strip()]
        parsed: dict[str, str] = {}
        if parts:
            parsed["title"] = parts[0]
        if len(parts) >= 2:
            match = re.match(r"^(\S+)(?:\s+\(([^)]+)\))?$", parts[1])
            if match:
                parsed["name"] = match.group(1)
                if match.group(2):
                    parsed["code"] = match.group(2)
        if len(parts) >= 3:
            parsed["subtitle"] = parts[-1]
        return parsed

    if " - " in text:
        left, right = text.split(" - ", 1)
        parsed = {"title": right.strip()}
        match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", left.strip())
        if match:
            parsed["name"] = match.group(1).strip()
            parsed["code"] = match.group(2).strip()
        else:
            parsed["name"] = left.strip()
        return parsed

    return {"title": text}


def _first_present(source: dict, *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _enrich_ecr_template_fields(normalized: dict[str, str], source: dict) -> dict[str, str]:
    out = dict(normalized)

    release_ref = _first_present(source, "TargetReleaseOID", "TargetRelease", "TargetedReleaseOID", "TargetedRelease")
    if release_ref:
        out["rel_eno_id"] = release_ref

    detection_ref = _first_present(source, "DetectionProgramOID", "DetectionProgram", "DetectionLevelOID", "DetectionLevel")
    if detection_ref:
        out["detection_level_eno_id"] = detection_ref

    feature_ref = _first_present(
        source,
        "ConcernedFeatureOID",
        "ConcernedFeature",
        "ConcernedFeaturePhysicalId",
        "ClassificationElementOID",
        "ClassificationElement",
        "FeatureOID",
        "FeaturePhysicalId",
    )
    if feature_ref:
        out["feature_eno_id"] = feature_ref

    release_display = _parse_dsx_display(_first_present(source, "TargetReleaseDisplay", "TargetedReleaseDisplay"))
    if release_display.get("title"):
        out.setdefault("rel_title", release_display["title"])
    if release_display.get("name"):
        out.setdefault("rel_name", release_display["name"])

    detection_display = _parse_dsx_display(_first_present(source, "DetectionProgramDisplay", "DetectionLevelDisplay"))
    if detection_display.get("title"):
        out.setdefault("detection_level_title", detection_display["title"])
    if detection_display.get("name"):
        out.setdefault("detection_level_name", detection_display["name"])

    feature_display_raw = _first_present(
        source, "ConcernedFeatureDisplay", "ClassificationElementDisplay", "FeatureDisplay"
    )
    feature_display = _parse_dsx_display(feature_display_raw)
    if feature_display_raw and not feature_display:
        out.setdefault("feature_name", feature_display_raw)
    elif feature_display.get("title") and feature_display.get("name"):
        out.setdefault("feature_name", f"{feature_display['name']} ({feature_display.get('code', '')}) - {feature_display['title']}".replace(" () ", " "))
    elif feature_display.get("title"):
        out.setdefault("feature_name", feature_display["title"])
    elif feature_display.get("name"):
        out.setdefault("feature_name", feature_display["name"])

    for actor_key, target in (
        ("clarifier", "default_clarifier"),
        ("validator", "default_validator"),
        ("directAssignee", "default_corrector"),
        ("corrector", "default_corrector"),
    ):
        actor = _first_present(source, actor_key)
        if actor:
            out.setdefault(target, actor.split()[0])

    if out.get("rel_name") and not out.get("rel_level_id"):
        out["rel_level_id"] = out["rel_name"]

    if out.get("detected_environment"):
        out["detected_environment"] = _normalize_detected_environment(out["detected_environment"])

    return out


def _is_object_reference(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.upper() in BLOCKED_PLACEHOLDER_IDS:
        return False
    return bool(PHYSICAL_ID_PATTERN.match(text) or ENO_OID_PATTERN.match(text))


def _extract_physical_id(body: Any) -> Optional[str]:
    if isinstance(body, list) and body:
        return _extract_physical_id(body[0])
    if not isinstance(body, dict):
        return None
    for key in ("physicalId", "physicalid", "id"):
        value = body.get(key)
        if value and PHYSICAL_ID_PATTERN.match(str(value).strip()):
            return str(value).strip().upper()
    for key in ("member", "data", "items", "results"):
        nested = body.get(key)
        if isinstance(nested, list) and nested:
            resolved = _extract_physical_id(nested[0])
            if resolved:
                return resolved
    return None


def _resolve_object_reference(session: dict, reference: str) -> str:
    ref = reference.strip()
    if not ref:
        return ref
    if PHYSICAL_ID_PATTERN.match(ref):
        return ref.upper()
    if not ENO_OID_PATTERN.match(ref):
        return ref

    probe_paths = [
        f"/objects/{ref}",
        f"/resources/{ref}",
        f"/objects?physicalid={ref}",
        f"/objects?id={ref}",
    ]
    for path in probe_paths:
        try:
            response = _dsx_request(session, "GET", path)
            if response.status_code != 200:
                continue
            resolved = _extract_physical_id(response.json())
            if resolved:
                return resolved
        except (requests.RequestException, ValueError):
            continue

    ui_base = _ui_base_url()
    if ui_base:
        for path in (f"/objects/{ref}", f"/objects?id={ref}"):
            try:
                response = _ui_request(session, "GET", path)
                if response.status_code != 200:
                    continue
                resolved = _extract_physical_id(response.json())
                if resolved:
                    return resolved
            except (requests.RequestException, ValueError):
                continue

    return ref


def _resolve_create_references(
    session: dict,
    data: dict[str, str],
    *,
    service_id: str = "",
    brand_id: str = "",
) -> dict[str, str]:
    resolved = dict(data)
    for key in PHYSICAL_ID_FIELDS:
        value = resolved.get(key, "").strip()
        if value:
            resolved[key] = _resolve_object_reference(session, value)

    rel_name = resolved.get("rel_name", "").strip()
    rel_title = resolved.get("rel_title", "").strip()
    existing_rel = resolved.get("rel_eno_id", "").strip()
    if rel_name or rel_title:
        rel_id = _resolve_release_reference(session, rel_name, rel_title, service_id=service_id)
        if rel_id:
            resolved["rel_eno_id"] = rel_id
        elif not existing_rel or not _is_object_reference(existing_rel):
            resolved.pop("rel_eno_id", None)

    feature_name = resolved.get("feature_name", "").strip()
    existing_feature = resolved.get("feature_eno_id", "").strip()
    if feature_name:
        feature_id = _resolve_feature_reference(session, feature_name)
        if feature_id:
            resolved["feature_eno_id"] = feature_id
        elif not existing_feature or not _is_object_reference(existing_feature):
            resolved.pop("feature_eno_id", None)

    name = resolved.get("detection_level_name", "").strip()
    title = resolved.get("detection_level_title", "").strip()
    if name or title:
        resolved.pop("detection_level_eno_id", None)
        resolved_id = _resolve_program_reference(
            session,
            name,
            title,
            service_id=service_id,
            brand_id=brand_id,
        )
        if resolved_id:
            resolved["detection_level_eno_id"] = resolved_id

    return resolved


def _list_unresolved_physical_ids(data: dict[str, str]) -> list[dict[str, str]]:
    labels = _field_labels()
    unresolved: list[dict[str, str]] = []
    for key in PHYSICAL_ID_FIELDS:
        value = data.get(key, "").strip()
        if not value or not _is_object_reference(value):
            unresolved.append({"key": key, "label": labels.get(key, key)})
    return unresolved


def _finalize_create_payload(data: dict[str, str], username: str) -> dict[str, str]:
    finalized = dict(data)
    if finalized.get("rel_name") and not finalized.get("rel_level_id"):
        finalized["rel_level_id"] = finalized["rel_name"]
    if finalized.get("detection_level_name") and not finalized.get("detection_level_title"):
        finalized["detection_level_title"] = finalized["detection_level_name"]
    if not finalized.get("owner"):
        finalized["owner"] = username
    return finalized


def _normalize_saved_form_fields(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}

    source = _coalesce_form_field_source(raw)

    alias_map = {
        "Abstract": "title",
        "Description": "description",
        "ECRSeverity": "severity",
        "DetectedEnvironment": "detected_environment",
        "TargetReleaseOID": "rel_eno_id",
        "TargetedReleaseOID": "rel_eno_id",
        "TargetedReleasePhysicalId": "rel_eno_id",
        "ReleasePhysicalId": "rel_eno_id",
        "ReleaseOID": "rel_eno_id",
        "ConcernedFeatureOID": "feature_eno_id",
        "ConcernedFeaturePhysicalId": "feature_eno_id",
        "FeaturePhysicalId": "feature_eno_id",
        "ClassificationElementOID": "feature_eno_id",
        "DetectionProgramOID": "detection_level_eno_id",
        "DetectionLevelOID": "detection_level_eno_id",
        "DetectionProgramPhysicalId": "detection_level_eno_id",
        "Clarifier": "default_clarifier",
        "Corrector": "default_corrector",
        "Validator": "default_validator",
        "directAssignee": "default_corrector",
        "target_release_eno_id": "rel_eno_id",
        "targetReleaseEnoId": "rel_eno_id",
        "target_release_name": "rel_name",
        "targetReleaseName": "rel_name",
        "target_release_title": "rel_title",
        "targetReleaseTitle": "rel_title",
        "target_release_level_id": "rel_level_id",
        "targetReleaseLevelId": "rel_level_id",
        "feature_id": "feature_eno_id",
        "featureId": "feature_eno_id",
        "feature_title": "feature_name",
        "featureTitle": "feature_name",
        "detection_program_eno_id": "detection_level_eno_id",
        "detectionProgramEnoId": "detection_level_eno_id",
        "detection_program_name": "detection_level_name",
        "detectionProgramName": "detection_level_name",
        "detection_program_title": "detection_level_title",
        "detectionProgramTitle": "detection_level_title",
        "clarifier": "default_clarifier",
        "corrector": "default_corrector",
        "validator": "default_validator",
        "rel_physicalid": "rel_eno_id",
        "feature_physicalid": "feature_eno_id",
        "detection_level_physicalid": "detection_level_eno_id",
        "targeted_release_eno_id": "rel_eno_id",
        "targeted_release_name": "rel_name",
        "targeted_release_title": "rel_title",
        "targeted_release_level_id": "rel_level_id",
        "concerned_feature_eno_id": "feature_eno_id",
        "concerned_feature_name": "feature_name",
        "detection_program_level_eno_id": "detection_level_eno_id",
        "detection_program_level_name": "detection_level_name",
        "detection_program_level_title": "detection_level_title",
    }

    normalized: dict[str, str] = {}
    _extract_nested_objects(source, normalized)

    for key, value in source.items():
        if value is None or isinstance(value, (dict, list)):
            continue
        mapped_key = alias_map.get(key, key)
        _merge_field_value(normalized, mapped_key, value)

    if not normalized.get("owner") and source.get("owner"):
        normalized["owner"] = str(source["owner"]).strip()

    return _enrich_ecr_template_fields(normalized, source)


def _normalize_saved_form(item: dict) -> Optional[dict]:
    if not isinstance(item, dict):
        return None

    form_id = (
        item.get("physicalId")
        or item.get("physicalid")
        or item.get("id")
        or item.get("uri")
        or item.get("name")
    )
    title = item.get("title") or item.get("name") or item.get("label") or "Saved IR form"
    if not form_id:
        return None

    fields = _normalize_saved_form_fields(item)
    obj_type = (
        item.get("businessType")
        or item.get("business_type")
        or item.get("created_object_type")
        or item.get("createdObjectType")
        or item.get("object_type")
        or item.get("objectType")
        or item.get("type")
        or "IncidentFamily"
    )
    return {
        "id": str(form_id),
        "title": str(title),
        "description": item.get("description") or item.get("summary") or "",
        "created_object_type": obj_type,
        "fields": fields,
    }


FORM_TEMPLATE_TYPES = ("ECR", "DSXECO_ECR", "IncidentFamily", "DSXECO_ECO")
CREATION_FORM_OBJECT_TYPES = FORM_TEMPLATE_TYPES + ("Incident",)
INCIDENT_FORM_TYPE_HINTS = ("incident", "eco", "ecr", "defect", "family", "formtemplate")


def _is_incident_creation_form(form: dict) -> bool:
    obj_type = (form.get("created_object_type") or "").lower()
    if not obj_type:
        return True
    return any(hint in obj_type for hint in INCIDENT_FORM_TYPE_HINTS)


def _parse_saved_forms_response(body: Any) -> list[dict]:
    forms: list[dict] = []
    seen_ids: set[str] = set()
    for item in _extract_items(body):
        form = _normalize_saved_form(item)
        if not form:
            continue
        if not _is_incident_creation_form(form):
            continue
        if form["id"] in seen_ids:
            continue
        seen_ids.add(form["id"])
        forms.append(form)
    return forms


def _enrich_form_template(session: dict, form: dict) -> dict:
    has_physical_ids = any(form.get("fields", {}).get(key) for key in PHYSICAL_ID_FIELDS)
    if form.get("fields") and has_physical_ids:
        return form
    form_id = form.get("id")
    if not form_id:
        return form
    try:
        response = _ui_request(session, "GET", f"/formtemplates/{form_id}")
        if response.status_code != 200:
            return form
        detail = _normalize_saved_form(response.json())
        if not detail:
            return form
        merged_fields = {**detail.get("fields", {}), **form.get("fields", {})}
        return {**form, **detail, "fields": merged_fields, "id": str(form_id)}
    except (requests.RequestException, ValueError):
        return form


def _fetch_form_templates_from_ui(session: dict, probe_log: list[dict]) -> list[dict]:
    username = session.get("username", "")
    if not _ui_base_url():
        probe_log.append({"api": "ui", "method": "GET", "path": "/formtemplates", "status": "skipped", "detail": "no UI base URL"})
        return []

    custom_path = os.environ.get("DSX_CREATION_FORMS_PATH", "").strip()
    ui_paths: list[str] = []
    if custom_path and "formtemplates" in custom_path:
        ui_paths.append(custom_path if custom_path.startswith("/") else f"/{custom_path}")
    else:
        for obj_type in FORM_TEMPLATE_TYPES:
            ui_paths.extend(
                [
                    f"/formtemplates?owner={username}&type={obj_type}&current=true",
                    f"/formtemplates?owner={username}&type={obj_type}&state=Active&current=true",
                    f"/formtemplates?owner={username}&type={obj_type}",
                ]
            )
        ui_paths.extend(
            [
                f"/formtemplates?owner={username}&current=true",
                f"/formtemplates?owner={username}&state=Active&current=true",
                f"/formtemplates?owner={username}",
            ]
        )

    forms: list[dict] = []
    seen_ids: set[str] = set()
    for path in ui_paths:
        try:
            response = _ui_request(session, "GET", path)
            probe_log.append(
                {
                    "api": "ui",
                    "method": "GET",
                    "path": path,
                    "status": response.status_code,
                }
            )
            if response.status_code != 200:
                continue
            body = response.json()
            parsed = _parse_saved_forms_response(body)
            items = _extract_items(body)
            probe_log[-1]["items"] = len(items)
            probe_log[-1]["parsed"] = len(parsed)
            if items and not parsed:
                first = items[0] if isinstance(items[0], dict) else {}
                probe_log[-1]["sample_type"] = first.get("type") or first.get("businessType")
                probe_log[-1]["sample_title"] = first.get("title") or first.get("name")
            for form in parsed:
                if form["id"] in seen_ids:
                    continue
                seen_ids.add(form["id"])
                forms.append(_enrich_form_template(session, form))
            if forms:
                break
        except (requests.RequestException, ValueError) as err:
            probe_log.append({"api": "ui", "method": "GET", "path": path, "status": "error", "detail": str(err)})
    return forms


def _fetch_saved_forms_from_dsx(session: dict) -> tuple[list[dict], list[dict]]:
    username = session.get("username", "")
    custom_path = os.environ.get("DSX_CREATION_FORMS_PATH", "").strip()
    probe_log: list[dict] = []
    collected: list[dict] = []
    seen_ids: set[str] = set()

    def add_forms(forms: list[dict]) -> None:
        for form in forms:
            if form["id"] not in seen_ids:
                seen_ids.add(form["id"])
                collected.append(form)

    ui_forms = _fetch_form_templates_from_ui(session, probe_log)
    add_forms(ui_forms)
    if collected:
        return collected, probe_log

    get_paths: list[str] = []
    if custom_path and "formtemplates" not in custom_path:
        get_paths.append(custom_path if custom_path.startswith("/") else f"/{custom_path}")

    for obj_type in CREATION_FORM_OBJECT_TYPES:
        get_paths.extend(
            [
                f"/creationforms?created_object_type={obj_type}&owner={username}",
                f"/creationforms?createdObjectType={obj_type}&owner={username}",
                f"/creationforms?created_object_type={obj_type}",
                f"/creationforms?createdObjectType={obj_type}",
                f"/creationformsets?created_object_type={obj_type}&owner={username}",
                f"/creationformsets?created_object_type={obj_type}",
                f"/sets?type=creationform&created_object_type={obj_type}&owner={username}",
                f"/sets?type=creationform&created_object_type={obj_type}",
            ]
        )

    get_paths.extend(
        [
            f"/users/{username}/creationformsets",
            f"/users/{username}/creationforms",
            f"/users/{username}/sets?type=creationform",
            f"/persons/{username}/creationformsets",
            "/creationformsets",
            "/creationforms",
            f"/creationforms?owner={username}",
            "/sets?type=creationform",
        ]
    )

    post_attempts: list[tuple[str, Optional[dict]]] = []
    for obj_type in CREATION_FORM_OBJECT_TYPES:
        post_attempts.extend(
            [
                ("/sets", {"type": "creationform", "created_object_type": obj_type, "owner": username}),
                ("/sets", {"type": "creationform", "created_object_type": obj_type}),
                ("/creationformsets/search", {"created_object_type": obj_type, "owner": username}),
                ("/creationformsets", {"created_object_type": obj_type, "owner": username}),
                ("/creationforms/search", {"created_object_type": obj_type, "owner": username}),
            ]
        )
    post_attempts.extend(
        [
            ("/sets", {"type": "creationform", "owner": username}),
            ("/sets", {"type": "creationform"}),
        ]
    )

    for path in get_paths:
        try:
            response = _dsx_request(session, "GET", path)
            probe_log.append({"api": "devops", "method": "GET", "path": path, "status": response.status_code})
            if response.status_code != 200:
                continue
            body = response.json()
            forms = _parse_saved_forms_response(body)
            probe_log[-1]["items"] = len(_extract_items(body))
            probe_log[-1]["parsed"] = len(forms)
            add_forms(forms)
        except (requests.RequestException, ValueError) as err:
            probe_log.append({"api": "devops", "method": "GET", "path": path, "status": "error", "detail": str(err)})
            continue

    for path, payload in post_attempts:
        try:
            response = _dsx_request(session, "POST", path, data=payload)
            probe_log.append({"api": "devops", "method": "POST", "path": path, "status": response.status_code})
            if response.status_code not in (200, 201):
                continue
            body = response.json()
            forms = _parse_saved_forms_response(body)
            probe_log[-1]["items"] = len(_extract_items(body))
            probe_log[-1]["parsed"] = len(forms)
            add_forms(forms)
        except (requests.RequestException, ValueError) as err:
            probe_log.append({"api": "devops", "method": "POST", "path": path, "status": "error", "detail": str(err)})
            continue

    return collected, probe_log


def _field_labels() -> dict[str, str]:
    labels = {field: field.replace("_", " ").title() for field in REQUIRED_FIELDS}
    for section in IR_FORM_SECTIONS:
        for field in section.get("fields", []):
            labels[field["key"]] = field.get("label", field["key"])
    return labels


def _dsx_answer_text(block: Any) -> Optional[str]:
    if not isinstance(block, dict):
        return None
    msg = block.get("msg")
    if isinstance(msg, dict) and msg.get("value"):
        return str(msg["value"])
    if isinstance(msg, str):
        return msg
    return None


def _format_dsx_error(details: Any) -> str:
    if details is None:
        return "Unknown DSX error"
    if isinstance(details, str):
        return details
    if isinstance(details, list):
        parts = []
        for item in details:
            if isinstance(item, dict):
                parts.append(item.get("message") or item.get("msg") or item.get("text") or str(item))
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else str(details)
    if isinstance(details, dict):
        for section in ("diagnostic", "advice", "request"):
            text = _dsx_answer_text(details.get(section))
            if text:
                advice = _dsx_answer_text(details.get("advice"))
                if advice and advice != text:
                    return f"{text} ({advice})"
                return text
        for key in ("message", "error", "detail", "description"):
            if details.get(key):
                return str(details[key])
        if isinstance(details.get("messages"), list):
            return _format_dsx_error(details["messages"])
        answer = details.get("answer")
        if isinstance(answer, dict) and isinstance(answer.get("errors"), list):
            return _format_dsx_error(answer["errors"])
        if isinstance(answer, dict) and answer.get("message"):
            return str(answer["message"])
        try:
            return json.dumps(details, ensure_ascii=True)
        except (TypeError, ValueError):
            return str(details)
    return str(details)


def _validate_physical_ids(data: dict[str, str]) -> None:
    labels = _field_labels()
    errors: list[str] = []
    for key in PHYSICAL_ID_FIELDS:
        value = data.get(key, "").strip()
        if not value:
            continue
        upper = value.upper()
        if upper in BLOCKED_PLACEHOLDER_IDS:
            errors.append(f"{labels[key]}: invalid placeholder reference")
        elif not _is_object_reference(value):
            errors.append(
                f"{labels[key]}: could not resolve the linked 3DEXPERIENCE object — "
                "try a saved form template or pick release/feature/detection in DSX first"
            )
    if errors:
        raise HTTPException(400, detail="; ".join(errors))


def _normalize_detected_environment(value: str) -> str:
    return DETECTED_ENVIRONMENT_MAP.get(value.strip().lower(), value.strip())


def _normalize_create_payload(data: dict[str, Any], username: str) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[key] = text

    if "detected_environment" in normalized:
        normalized["detected_environment"] = _normalize_detected_environment(normalized["detected_environment"])

    for key in ("rel_eno_id", "feature_eno_id", "detection_level_eno_id"):
        if key in normalized and PHYSICAL_ID_PATTERN.match(normalized[key]):
            normalized[key] = normalized[key].upper()

    return normalized


def _generate_title_from_description(description: str) -> str:
    text = re.sub(r"\s+", " ", description.strip())
    if not text:
        return "Untitled incident"

    # Prefer the first complete sentence.
    sentence_match = re.match(r"^(.{10,120}?)(?:[.!?](?:\s|$)|$)", text)
    candidate = sentence_match.group(1).strip() if sentence_match else text

    # Drop filler openers common in bug reports.
    candidate = re.sub(
        r"^(when|while|after|if|i|we|user|the)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()

    if len(candidate) > 90:
        words = candidate.split()
        shortened: list[str] = []
        for word in words:
            if len(" ".join(shortened + [word])) > 87:
                break
            shortened.append(word)
        candidate = " ".join(shortened) + ("…" if shortened else "")

    return candidate[:1].upper() + candidate[1:] if candidate else "Untitled incident"


def _normalize_incident(item: dict) -> dict:
    physicalid = item.get("physicalid") or item.get("physicalId") or item.get("id")
    pid_for_url = ""
    if physicalid and PHYSICAL_ID_PATTERN.match(str(physicalid).strip()):
        pid_for_url = str(physicalid).strip().upper()
    return {
        "id": item.get("name") or item.get("id") or item.get("physicalid") or "",
        "title": item.get("title") or item.get("description", "")[:120] or "Untitled",
        "status": item.get("state") or item.get("status") or "Unknown",
        "severity": item.get("severity"),
        "owner": item.get("owner"),
        "physicalid": physicalid,
        "url": _build_ir_navigator_url(pid_for_url),
    }


def _release_hierarchy_configured() -> bool:
    return bool(
        BASE_URL
        and DSX_PRODUCT_SERVICES_PATH
        and DSX_PROGRAMS_PATH
        and DSX_RELEASES_PATH
    )


@app.get("/api/health")
def health():
    navigator_base = _dsx_navigator_base_url()
    return {
        "ok": True,
        "dsx_configured": bool(BASE_URL),
        "dsx_ui_configured": bool(_ui_base_url()),
        "dsx_ui_base": _ui_base_url() or None,
        "dsx_api_docs_url": _dsx_api_docs_url() or None,
        "dsx_navigator_base": navigator_base or None,
        "release_hierarchy_configured": _release_hierarchy_configured(),
        "env_login_available": _env_login_available(),
        "configured_username": _configured_env_username(),
        "mli_configured": mli_configured(),
        "mli_model": MLI_MODEL or None,
    }


@app.get("/api/dsx/navigator-url")
def dsx_navigator_url(physical_id: str = ""):
    """Build emxNavigator.jsp URL for a DSX physical ID (release, IR, program, etc.)."""
    url = _build_ir_navigator_url(physical_id)
    if not url:
        raise HTTPException(
            400,
            detail="Invalid physical_id or DSX_WEB_BASE_URL / DSX_BASE_URL is not configured for Navigator links.",
        )
    pid = physical_id.strip().upper()
    return {"physical_id": pid, "url": url}


@app.post("/api/login")
def login(payload: LoginRequest):
    encoded = _encode_credentials(payload.username, payload.password)
    username = payload.username.strip().lower()
    _validate_dsx_credentials(encoded, username)
    return _create_session_from_encoded_auth(encoded, username)


@app.post("/api/login/env")
def login_from_env():
    if not _env_login_available():
        raise HTTPException(
            503,
            detail="Server credentials are not configured for env login. Set DSX_CREDENTIALS in .env.",
        )

    username = _configured_env_username() or ""
    encoded = base64.b64encode(ENV_CREDENTIALS.encode()).decode()
    _validate_dsx_credentials(encoded, username)
    return _create_session_from_encoded_auth(encoded, username)


@app.post("/api/logout")
def logout(session: dict = Depends(get_session)):
    session_id = session.get("session_id")
    if session_id and session_id in SESSIONS:
        del SESSIONS[session_id]
    return {"ok": True}


@app.get("/api/me")
def me(session: dict = Depends(get_session)):
    return {"username": session["username"]}


@app.post("/api/generate-title")
def generate_title(payload: GenerateTitleRequest, session: dict = Depends(get_session)):
    _ = session
    description = payload.description.strip()
    if not description:
        raise HTTPException(400, detail="Description is required")

    context = {
        "severity": (payload.severity or "").strip(),
        "feature_name": (payload.feature_name or "").strip(),
        "detection_version": (payload.detection_version or "").strip(),
        "detected_environment": (payload.detected_environment or "").strip(),
    }

    if mli_configured() and generate_ir_title is not None:
        try:
            title = generate_ir_title(description, context)
            return {"title": title, "description": description, "source": "mli"}
        except Exception as exc:
            logger.warning("MLI title generation failed, using heuristic fallback: %s", exc)

    title = _generate_title_from_description(description)
    return {"title": title, "description": description, "source": "heuristic"}


@app.get("/api/dsx/brands")
def list_dsx_brands(session: dict = Depends(get_session)):
    brands = _get_cached_brands(session)
    return {
        "items": brands,
        "count": len(brands),
        "hint": (
            f"Brands from GET /brands. Set DSX_PARENT_BRAND_NAME in .env to auto-select one "
            f"(current default: {DSX_PARENT_BRAND_NAME!r})."
        ),
    }


@app.get("/api/dsx/services")
def list_dsx_services(brand_id: str = "", session: dict = Depends(get_session)):
    brand_id = brand_id.strip()
    if brand_id:
        parent = _find_brand_by_id(session, brand_id)
        if not parent:
            raise HTTPException(404, detail=f"Brand {brand_id} not found in GET /brands")
        items, probe = _fetch_product_services_for_brand(session, parent)
    else:
        parent, items, probe = _fetch_parent_brand_product_services(session)

    hint = None
    brands = _get_cached_brands(session)
    if not parent:
        hint = (
            f"Parent brand not found in DSX (DSX_PARENT_BRAND_NAME={DSX_PARENT_BRAND_NAME!r}). "
            f"Available brands: {_brands_availability_hint(brands, limit=3)}. "
            "Pick a brand from the list or set DSX_PARENT_BRAND_NAME in .env to match one of these."
        )
    elif not items:
        hint = (
            f"Product services API returned no data for {_brand_display_label(parent)} on this DSX server. "
            "You can pick a program directly under this brand instead."
        )
    parent_summary = None
    if parent:
        parent_summary = {
            "id": parent.get("id"),
            "name": parent.get("name"),
            "title": parent.get("title"),
            "physicalid": parent.get("physicalid"),
        }
    return {
        "items": items,
        "parent_brand": parent_summary,
        "probe": probe,
        "probe_summary": _summarize_probe(probe),
        "hint": hint,
        "services_unavailable": bool(parent and not items),
    }


@app.get("/api/dsx/programs")
def list_dsx_programs(
    service_id: str = "",
    brand_id: str = "",
    session: dict = Depends(get_session),
):
    service_id = service_id.strip()
    brand_id = brand_id.strip()
    if brand_id:
        brand = _find_brand_by_id(session, brand_id)
        if not brand:
            raise HTTPException(404, detail=f"Brand {brand_id} not found in GET /brands")
        items, probe = _fetch_programs_for_brand(session, brand)
        scope = "brand"
        hint = None
        if not items:
            hint = (
                f"No programs returned for brand {_brand_display_label(brand)} on this server. "
                "Use **Set target release** and enter the release name to resolve rel_eno_id."
            )
        return {
            "items": items,
            "brand_id": brand_id,
            "scope": scope,
            "probe": probe,
            "probe_summary": _summarize_probe(probe),
            "hint": hint,
        }

    if not service_id:
        raise HTTPException(400, detail="service_id or brand_id is required")

    items, probe = _fetch_dsx_programs_for_product_service(session, service_id)
    hint = None
    if not items:
        hint = (
            "No programs returned for this product service. Coal Porter resolves programs via "
            "GET /programs?service={physicalId}. Verify the service has programs in 3DEXPERIENCE."
        )
    return {
        "items": items,
        "service_id": service_id,
        "scope": "service",
        "probe": probe,
        "probe_summary": _summarize_probe(probe),
        "hint": hint,
    }


@app.get("/api/dsx/releases")
def list_dsx_releases(program_id: str, session: dict = Depends(get_session)):
    if not program_id.strip():
        raise HTTPException(400, detail="program_id is required")
    raw_items = _fetch_dsx_release_raw_items(session, program_id)
    items = []
    for raw in raw_items:
        pick = _normalize_pick_item(raw)
        release_fields = _normalize_release_fields(session, raw)
        items.append({**pick, "release_fields": release_fields})
    return {"items": items, "program_id": program_id}


@app.post("/api/dsx/resolve-release-context")
def resolve_release_context(payload: ResolveReleaseContextRequest, session: dict = Depends(get_session)):
    service_id = payload.service_id.strip()
    program_id = payload.program_id.strip()
    if not service_id or not program_id:
        raise HTTPException(400, detail="service_id and program_id are required")

    raw_items = _fetch_dsx_release_raw_items(session, program_id)
    if not raw_items:
        raise HTTPException(
            404,
            detail={
                "error": "No releases found",
                "message": f"No release returned for program {program_id}. Check DSX_RELEASES_PATH configuration.",
            },
        )

    selected_raw: Optional[dict] = None
    if payload.release_id:
        release_id = payload.release_id.strip()
        for raw in raw_items:
            pick = _normalize_pick_item(raw)
            if pick.get("id") == release_id or pick.get("physicalid") == release_id.upper():
                selected_raw = raw
                break
        if not selected_raw:
            raise HTTPException(404, detail=f"Release {release_id} not found for program {program_id}")
    elif len(raw_items) == 1:
        selected_raw = raw_items[0]
    else:
        raise HTTPException(
            400,
            detail={
                "error": "Multiple releases",
                "message": "Multiple releases match this program — pick one release.",
                "count": len(raw_items),
            },
        )

    release_fields = _normalize_release_fields(session, selected_raw)
    if not release_fields.get("rel_eno_id"):
        raise HTTPException(
            400,
            detail={
                "error": "Release missing physical id",
                "message": "Could not resolve rel_eno_id from the releases API response.",
            },
        )

    pick = _normalize_pick_item(selected_raw)
    detection_fields = _normalize_detection_fields(
        session,
        service_id,
        payload.program_name,
        payload.program_name,
        program_id=program_id,
    )
    return {
        "service_id": service_id,
        "service_name": payload.service_name,
        "program_id": program_id,
        "program_name": payload.program_name,
        "release": pick,
        "release_fields": release_fields,
        "detection_fields": detection_fields,
    }


@app.post("/api/dsx/resolve-release")
def resolve_release_by_name(payload: ResolveReleaseByNameRequest, session: dict = Depends(get_session)):
    query = (payload.query or "").strip()
    rel_name = (payload.release_name or "").strip()
    rel_title = (payload.release_title or "").strip()
    if not query and not rel_name and not rel_title:
        raise HTTPException(400, detail="query or release_name is required")
    if not query:
        query = rel_title or rel_name

    item, probe = _lookup_release_by_query(session, query)
    if not item:
        probe_summary = _summarize_release_probe(probe)
        raise HTTPException(
            404,
            detail={
                "error": "Release not found",
                "message": (
                    f"No release matched '{query}'. Use the exact release title, version (e.g. 1.9x), "
                    f"or REL code from 3DEXPERIENCE / Coal Porter GET /releases."
                    + (f" DSX probe: {probe_summary}" if probe_summary else "")
                ),
                "probe": probe[:20],
            },
        )

    release_fields = _normalize_release_fields(session, item)
    if not release_fields.get("rel_eno_id"):
        raise HTTPException(
            404,
            detail={
                "error": "Release missing physical id",
                "message": "A release was found but rel_eno_id could not be resolved.",
                "probe": probe[:20],
            },
        )

    pick = _normalize_pick_item(item)
    rel_eno_id = release_fields["rel_eno_id"]
    navigator_url = _build_ir_navigator_url(rel_eno_id) or None
    return {
        "resolved": True,
        "query": query,
        "release_fields": release_fields,
        "release": pick,
        "navigator_url": navigator_url,
        "probe": probe[:20] if probe else None,
    }


@app.post("/api/dsx/resolve-detection-level")
def resolve_detection_level(payload: ResolveDetectionLevelRequest, session: dict = Depends(get_session)):
    fields = _normalize_detection_fields(
        session,
        (payload.service_id or "").strip(),
        payload.detection_level_name,
        payload.detection_level_title,
        brand_id=(payload.brand_id or "").strip(),
    )
    return {
        "fields": fields,
        "resolved": bool(fields.get("detection_level_eno_id")),
    }


@app.get("/api/ir-field-schema")
def ir_field_schema():
    labels = _field_labels()
    user_required = [field for field in REQUIRED_FIELDS if field not in PHYSICAL_ID_FIELDS]
    return {
        "sections": IR_FORM_SECTIONS,
        "required_fields": [
            {"key": field, "label": labels.get(field, field)} for field in REQUIRED_FIELDS
        ],
        "user_required_fields": [
            {"key": field, "label": labels.get(field, field)} for field in user_required
        ],
    }


@app.get("/api/saved-ir-forms/probe")
def probe_saved_ir_forms(session: dict = Depends(get_session)):
    forms, probe_log = _fetch_saved_forms_from_dsx(session)
    return {"items": forms, "probe": probe_log}


@app.get("/api/saved-ir-forms")
def list_saved_ir_forms(session: dict = Depends(get_session)):
    forms, probe_log = _fetch_saved_forms_from_dsx(session)
    hint = None
    if not forms:
        status_counts: dict[str, int] = {}
        for entry in probe_log:
            status = str(entry.get("status", "error"))
            status_counts[status] = status_counts.get(status, 0) + 1
        status_summary = ", ".join(f"{status}: {count}" for status, count in sorted(status_counts.items()))
        sample_paths = "; ".join(
            f"{p['method']} {p['path']} → {p['status']}"
            for p in probe_log[:4]
        )
        hint = (
            "No saved IR form templates were returned. The chatbot now checks "
            "GET /rest/ui/v1/formtemplates (where Translate_IR lives) before legacy /creationforms paths. "
            f"API probe summary: {status_summary}. "
            f"Examples: {sample_paths}. "
            "If UI calls also fail, verify DSX_UI_BASE_URL or DSX_BASE_URL in .env and restart the backend."
        )
    return {
        "items": forms,
        "source": "ui-formtemplates" if forms and any(p.get("api") == "ui" for p in probe_log) else ("dsx" if forms else "empty"),
        "hint": hint,
        "probe": probe_log if not forms else None,
    }


@app.get("/api/saved-ir-forms/{form_id}")
def get_saved_ir_form(form_id: str, session: dict = Depends(get_session)):
    try:
        response = _ui_request(session, "GET", f"/formtemplates/{form_id}")
        if response.status_code == 200:
            form = _normalize_saved_form(response.json())
            if form:
                return _enrich_form_template(session, form)
    except (requests.RequestException, ValueError):
        pass

    try:
        response = _dsx_request(session, "GET", f"/creationforms/{form_id}")
        if response.status_code == 200:
            form = _normalize_saved_form(response.json())
            if form:
                return form
    except (requests.RequestException, ValueError):
        pass

    forms, _probe_log = _fetch_saved_forms_from_dsx(session)
    for form in forms:
        if form["id"] == form_id:
            return form

    raise HTTPException(404, detail=f"Saved IR form '{form_id}' not found")


@app.get("/api/my-incidents")
def my_incidents(
    session: dict = Depends(get_session),
    state: str = "OPEN",
    limit: int = 50,
):
    username = session["username"]
    params_variants = [
        {"state": state, "owner": username},
        {"state": state, "$filter": f"owner eq '{username}'"},
    ]

    items: list[dict] = []
    for params in params_variants:
        try:
            response = _dsx_request(session, "GET", "/incidentfamilies", params=params)
            if response.status_code != 200:
                continue
            body = response.json()
            raw_items = _extract_items(body)
            for item in raw_items:
                incident = _normalize_incident(item)
                if incident["id"]:
                    items.append(incident)
            if items:
                break
        except requests.RequestException:
            continue

    # Client-side owner filter when API ignores owner param.
    filtered = [
        ir
        for ir in items
        if not ir.get("owner") or str(ir["owner"]).lower() == username.lower()
    ]
    return {"items": (filtered or items)[:limit], "username": username}


@app.post("/api/resolve-ir-references")
def resolve_ir_references(payload: ResolveIrReferencesRequest, session: dict = Depends(get_session)):
    raw = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    service_id = str(raw.pop("service_id", "") or "").strip()
    brand_id = str(raw.pop("brand_id", "") or "").strip()
    data = _normalize_create_payload(raw, session["username"])
    data = _finalize_create_payload(data, session["username"])
    data = _resolve_create_references(session, data, service_id=service_id, brand_id=brand_id)
    return {
        "fields": data,
        "unresolved": _list_unresolved_physical_ids(data),
    }


@app.post("/api/create-ir")
def create_ir(payload: CreateIRRequest, session: dict = Depends(get_session)):
    raw = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    service_id = str(raw.pop("service_id", "") or "").strip()
    brand_id = str(raw.pop("brand_id", "") or "").strip()
    data = _normalize_create_payload(raw, session["username"])
    data = _finalize_create_payload(data, session["username"])
    data = _resolve_create_references(session, data, service_id=service_id, brand_id=brand_id)
    labels = _field_labels()

    missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
    if missing:
        missing_labels = ", ".join(labels.get(field, field) for field in missing)
        raise HTTPException(400, detail=f"Missing mandatory fields: {missing_labels}")

    if not data.get("rel_eno_id"):
        raise HTTPException(
            400,
            detail={
                "error": "Target release required",
                "message": (
                    "rel_eno_id is missing. Set target release on the dashboard "
                    "(service → program → release) before filing."
                ),
            },
        )

    _validate_physical_ids(data)

    # Coal Porter: POST /incidentfamilies (multipart form fields) — see {BASE_URL}/api-docs
    try:
        headers = _auth_headers(
            session["encoded_auth"],
            {
                "x-dsx-request-error-format": "ANSWER",
                "x-dsx-object-builder-type": "ATTRIBUTE",
            },
        )
        response = requests.post(
            f"{BASE_URL}/incidentfamilies",
            files={k: (None, str(v)) for k, v in data.items()},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        return _build_create_ir_response(body, session)
    except requests.HTTPError as err:
        status = err.response.status_code if err.response is not None else 500
        details: Any = str(err)
        if err.response is not None:
            try:
                details = err.response.json()
            except ValueError:
                details = err.response.text or str(err)
        dsx_message = _format_dsx_error(details)
        raise HTTPException(
            status,
            detail={
                "error": "DSX rejected the incident report",
                "message": dsx_message,
                "details": details,
            },
        )
    except requests.RequestException as err:
        raise HTTPException(500, detail={"error": "Failed to reach 3DEXPERIENCE API", "details": str(err)})


@app.get("/api/search-ir")
def search_ir(
    session: dict = Depends(get_session),
    q: Optional[str] = None,
    feature: Optional[str] = None,
    pgm_title: Optional[str] = None,
):
    params: dict[str, str] = {"state": "OPEN"}
    if pgm_title:
        params["pgm_title"] = pgm_title
    if feature:
        params["feature_eno_id"] = feature
    if q:
        params["$search"] = q

    try:
        response = _dsx_request(session, "GET", "/incidentfamilies", params=params)
        response.raise_for_status()
        body = response.json()
        items = [_normalize_incident(item) for item in _extract_items(body)]
        return {"items": items}
    except requests.HTTPError as err:
        status = err.response.status_code if err.response is not None else 500
        raise HTTPException(status, detail="Failed to search incident reports")
    except requests.RequestException as err:
        raise HTTPException(500, detail=str(err))


def _iter_dict_nodes(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dict_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dict_nodes(item)


def _extract_document_parent_from_incident(body: Any) -> Optional[str]:
    preferred_keys = (
        "description_document_physicalid",
        "descriptionDocumentPhysicalId",
        "description_document_eno_id",
        "descriptionDocumentEnoId",
        "description_document_id",
        "descriptionDocumentId",
        "document_physicalid",
        "documentPhysicalId",
        "document_eno_id",
        "documentEnoId",
    )
    candidates: list[tuple[int, str]] = []

    for node in _iter_dict_nodes(body):
        for key in preferred_keys:
            value = node.get(key)
            if value is not None and str(value).strip():
                priority = 0 if "description" in key.lower() else 2
                if "physical" in key.lower():
                    priority -= 1
                candidates.append((priority, str(value).strip()))

        for key, value in node.items():
            key_lower = key.lower()
            if "description" in key_lower and "document" in key_lower:
                if isinstance(value, str) and value.strip():
                    candidates.append((0, value.strip()))
                elif isinstance(value, dict):
                    ref = _physical_id_value(value) or value.get("id")
                    if ref:
                        candidates.append((0, str(ref)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _fetch_incident_family(session: dict, ir_ref: str) -> dict:
    for path in (
        f"/incidentfamilies/{ir_ref}",
        f"/incidentfamilies?name={ir_ref}",
        f"/incidentfamilies?id={ir_ref}",
        f"/incidentfamilies?physicalid={ir_ref}",
    ):
        try:
            response = _dsx_request(session, "GET", path)
            if response.status_code != 200:
                continue
            body = response.json()
            if isinstance(body, list) and body and isinstance(body[0], dict):
                return body[0]
            if isinstance(body, dict):
                return body
        except (requests.RequestException, ValueError):
            continue
    return {}


def _list_incident_document_parents(session: dict, ir_ref: str) -> list[str]:
    parents: list[str] = []
    for path in (
        f"/incidentfamilies/{ir_ref}/documents",
        f"/incidentfamilies/{ir_ref}/files",
    ):
        try:
            response = _dsx_request(session, "GET", path)
            if response.status_code != 200:
                continue
            for item in _extract_items(response.json()):
                if not isinstance(item, dict):
                    continue
                ref = (
                    _physical_id_value(item)
                    or item.get("id")
                    or item.get("physicalid")
                    or item.get("physicalId")
                )
                if ref:
                    parents.append(str(ref))
        except (requests.RequestException, ValueError):
            continue
    return parents


def _resolve_ir_attachment_parent(
    session: dict,
    ir_ref: str,
    *,
    incident_body: Optional[dict] = None,
    preferred_document_id: Optional[str] = None,
) -> str:
    if preferred_document_id and str(preferred_document_id).strip():
        return str(preferred_document_id).strip()

    bodies: list[Any] = []
    if incident_body:
        bodies.append(incident_body)
    fetched = _fetch_incident_family(session, ir_ref)
    if fetched:
        bodies.append(fetched)

    for body in bodies:
        document_ref = _extract_document_parent_from_incident(body)
        if document_ref:
            return _resolve_object_reference(session, document_ref)

    for document_ref in _list_incident_document_parents(session, ir_ref):
        return _resolve_object_reference(session, document_ref)

    raise HTTPException(
        404,
        detail=(
            "Could not find the IR description document to attach media. "
            "The IR was created, but attachments must be added manually in 3DEXPERIENCE."
        ),
    )


def _build_create_ir_response(body: dict, session: Optional[dict] = None) -> dict:
    document_id = _extract_document_parent_from_incident(body)
    display_id = body.get("name")
    if not display_id or ENO_OID_PATTERN.match(str(display_id)):
        display_id = body.get("title") or body.get("id")
    physical_id = _resolve_ir_physical_id(session, body)
    return {
        **body,
        "id": display_id,
        "object_id": body.get("id"),
        "physicalid": physical_id or body.get("physicalid") or body.get("physicalId"),
        "document_id": document_id,
        "title": body.get("title"),
        "url": _build_ir_navigator_url(physical_id),
    }


def _upload_file_to_document_parent(
    session: dict,
    parent_id: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict:
    headers = _auth_headers(
        session["encoded_auth"],
        {"x-dsx-request-error-format": "ANSWER"},
    )
    file_tuple = (filename, content, content_type or "application/octet-stream")
    parent_candidates = []
    for candidate in (parent_id, _resolve_object_reference(session, parent_id)):
        if candidate and candidate not in parent_candidates:
            parent_candidates.append(candidate)

    last_error: Any = "Unknown DSX upload error"
    for parent in parent_candidates:
        try:
            response = requests.post(
                f"{BASE_URL}/documents/{parent}/{filename}",
                files={"file": file_tuple},
                headers=headers,
                timeout=120,
            )
            response.raise_for_status()
            body = response.json() if response.text else {}
            return {
                "id": body.get("physicalid") or body.get("id"),
                "filename": filename,
                "document_parent_id": parent,
                **body,
            }
        except requests.HTTPError as err:
            if err.response is not None:
                try:
                    last_error = err.response.json()
                except ValueError:
                    last_error = err.response.text or str(err)
            else:
                last_error = str(err)
        except requests.RequestException as err:
            last_error = str(err)

    dsx_message = _format_dsx_error(last_error)
    raise HTTPException(
        400,
        detail={
            "error": "Failed to upload media",
            "message": dsx_message,
            "details": last_error,
        },
    )


@app.post("/api/upload-ir-media/{ir_id}")
async def upload_ir_media(
    ir_id: str,
    file: UploadFile = File(...),
    document_id: Optional[str] = None,
    session: dict = Depends(get_session),
):
    if not file.filename:
        raise HTTPException(400, detail="File name is required")

    try:
        content = await file.read()
        parent_id = _resolve_ir_attachment_parent(
            session,
            ir_id,
            preferred_document_id=document_id,
        )
        return _upload_file_to_document_parent(
            session,
            parent_id,
            file.filename,
            content,
            file.content_type or "application/octet-stream",
        )
    except HTTPException:
        raise
    except requests.RequestException as err:
        raise HTTPException(500, detail={"error": "Failed to reach 3DEXPERIENCE API", "details": str(err)})
