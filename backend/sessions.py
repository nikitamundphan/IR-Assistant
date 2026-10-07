"""In-memory DSX session store (Basic auth per session)."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from backend.config import DSX_BASE_URL, DSX_SERVICES_PATH, join_devops

INVALID_CREDENTIALS_DETAIL = (
    "DSX rejected this login/API key. Use your DSX service key (from MyApps), "
    "not your Windows password."
)
DSX_UNREACHABLE_DETAIL = "Could not reach DSX. Check DSX_BASE_URL and your network connection."


class DsxAuthError(ValueError):
    """DSX answered 401 — bad login or API key."""


class DsxUnavailableError(ValueError):
    """DSX could not be reached or returned no usable response."""


@dataclass
class DsxSession:
    session_id: str
    username: str
    password: str
    http: requests.Session = field(repr=False)
    profile: dict[str, Any] = field(default_factory=dict)


_lock = threading.Lock()
_sessions: dict[str, DsxSession] = {}


def _normalize_credentials(username: str, password: str) -> tuple[str, str]:
    """Same normalization as the legacy server: lowercase trigram, trimmed key."""
    return username.strip().lower(), password.strip()


def _build_http(username: str, password: str) -> requests.Session:
    session = requests.Session()
    session.auth = (username, password)
    session.headers.update({"Accept": "application/json", "x-dsx-limit": "1500"})
    return session


def validate_dsx_credentials(username: str, password: str) -> dict[str, Any]:
    """Probe Coal Porter (same endpoints as the legacy server) with Basic auth."""
    if not DSX_BASE_URL:
        raise DsxUnavailableError("DSX_BASE_URL is not configured on the server")
    username, password = _normalize_credentials(username, password)
    http = _build_http(username, password)
    probes = [
        (join_devops(DSX_SERVICES_PATH), None),
        (join_devops("/creationforms"), None),
        (join_devops("/incidentfamilies"), {"state": "OPEN"}),
    ]
    last_status: int | None = None
    network_error = False
    for url, params in probes:
        try:
            resp = http.get(url, params=params, timeout=60)
        except requests.RequestException:
            network_error = True
            continue
        last_status = resp.status_code
        if resp.status_code in (200, 204):
            return {"username": username}
        if resp.status_code == 401:
            raise DsxAuthError(INVALID_CREDENTIALS_DETAIL)
    if last_status is None:
        raise DsxUnavailableError(DSX_UNREACHABLE_DETAIL)
    if network_error:
        raise DsxUnavailableError(DSX_UNREACHABLE_DETAIL)
    raise DsxUnavailableError(f"DSX returned HTTP {last_status} for all login probes. Check DSX_BASE_URL.")


def create_session(username: str, password: str) -> DsxSession:
    username, password = _normalize_credentials(username, password)
    profile = validate_dsx_credentials(username, password)
    session_id = secrets.token_urlsafe(24)
    dsx = DsxSession(
        session_id=session_id,
        username=username,
        password=password,
        http=_build_http(username, password),
        profile=profile,
    )
    with _lock:
        _sessions[session_id] = dsx
    return dsx


def get_session(session_id: Optional[str]) -> Optional[DsxSession]:
    if not session_id:
        return None
    with _lock:
        return _sessions.get(session_id)


def delete_session(session_id: Optional[str]) -> None:
    if not session_id:
        return
    with _lock:
        _sessions.pop(session_id, None)


def require_session(session_id: Optional[str]) -> DsxSession:
    dsx = get_session(session_id)
    if not dsx:
        raise PermissionError("Invalid or expired session")
    return dsx
