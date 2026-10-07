"""Shared DSX JSON helpers and probe utilities."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from backend.config import DSX_PROBE_MAX_ATTEMPTS

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

DETECTED_ENVIRONMENT_MAP = {
    "cloud": "CLOUD",
    "on-premise": "ON_PREMISE",
    "on premise": "ON_PREMISE",
    "windows": "WINDOWS",
    "linux": "LINUX",
    "macos": "MACOS",
}


def is_object_reference(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.upper() in BLOCKED_PLACEHOLDER_IDS:
        return False
    return bool(PHYSICAL_ID_PATTERN.match(text) or ENO_OID_PATTERN.match(text))


def normalize_detected_environment(value: str) -> str:
    return DETECTED_ENVIRONMENT_MAP.get(value.strip().lower(), value.strip())


def uppercase_physical_id_fields(data: dict[str, str]) -> dict[str, str]:
    out = dict(data)
    for key in PHYSICAL_ID_FIELDS:
        val = out.get(key, "").strip()
        if val and PHYSICAL_ID_PATTERN.match(val):
            out[key] = val.upper()
    return out


def pick_id(item: dict[str, Any]) -> str:
    # Prefer the 32-char physical id (what IR creation needs); "id" can be a dotted ENOVIA id.
    for key in ("physicalId", "physicalid", "physicalID", "eno_id", "id", "objectId"):
        val = item.get(key)
        if val:
            return str(val).strip()
    return ""


def pick_title(item: dict[str, Any]) -> str:
    for key in ("title", "displayName", "display_name", "name", "label"):
        val = item.get(key)
        if val:
            return str(val).strip()
    return pick_id(item)


def pick_name(item: dict[str, Any]) -> str:
    for key in ("name", "code", "shortName", "short_name", "title"):
        val = item.get(key)
        if val:
            return str(val).strip()
    return pick_title(item)


def extract_items(body: Any) -> list[dict[str, Any]]:
    if body is None:
        return []
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("items", "data", "results", "releases", "programs", "brands", "services", "features"):
        val = body.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def normalize_list_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pick_id(item),
        "title": pick_title(item),
        "name": pick_name(item),
        "raw": item,
    }


def normalize_catalog_items(body: Any) -> list[dict[str, Any]]:
    return [normalize_list_item(x) for x in extract_items(body) if pick_id(x) or pick_title(x)]


def score_match(query: str, item: dict[str, Any]) -> float:
    q = query.strip().lower()
    if not q:
        return 0.0
    hay = " ".join(
        str(item.get(k) or "")
        for k in ("title", "name", "id", "code", "rel_level_id", "level_id")
    ).lower()
    if q == hay.strip():
        return 1.0
    if q in hay:
        return 0.85
    tokens = [t for t in re.split(r"[\s_\-./]+", q) if t]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in hay)
    return hits / len(tokens)


def best_match(query: str, items: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    ranked = sorted(
        ((score_match(query, it), it) for it in items),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.4:
        return None
    return ranked[0][1]


class ProbeLog:
    def __init__(self, max_attempts: int | None = None) -> None:
        self.max_attempts = max_attempts or DSX_PROBE_MAX_ATTEMPTS
        self.entries: list[dict[str, Any]] = []

    def add(self, path: str, params: dict[str, Any] | None, status: int, count: int) -> None:
        if len(self.entries) >= self.max_attempts:
            return
        self.entries.append(
            {
                "path": path,
                "params": params or {},
                "status": status,
                "count": count,
            }
        )

    def summary(self) -> list[dict[str, Any]]:
        return list(self.entries)
