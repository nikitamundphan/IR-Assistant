"""Draft IR field merge, validation, and description assembly (server-side)."""

from __future__ import annotations

from typing import Any, Optional

from backend.ui_config import INTERNAL_FIELD_KEYS, USER_REQUIRED_KEYS


def _detection_changed_from_baseline(fields: dict[str, Any], baseline: dict[str, Any]) -> bool:
    for key in ("detection_level_name", "detection_level_title", "detection_level_eno_id"):
        a = str(fields.get(key) or "").strip()
        b = str(baseline.get(key) or "").strip()
        if a != b:
            return True
    return False


def merge_release_context(
    fields: dict[str, Any],
    *,
    release_fields: Optional[dict[str, Any]] = None,
    detection_fields: Optional[dict[str, Any]] = None,
    force: bool = False,
    user_edited_sections: Optional[set[str]] = None,
    baseline_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    merged = dict(fields or {})
    edited = user_edited_sections or set()
    baseline = baseline_fields or {}
    detection_edited = "detection" in edited or _detection_changed_from_baseline(merged, baseline)

    if release_fields:
        merged.update({k: v for k, v in release_fields.items() if v is not None})
    if detection_fields and (force or not detection_edited):
        merged.update({k: v for k, v in detection_fields.items() if v is not None})
    return merged


def draft_progress(payload: dict[str, Any]) -> int:
    filled = sum(
        1
        for key in USER_REQUIRED_KEYS
        if payload.get(key) and str(payload.get(key)).strip()
    )
    return round((filled / len(USER_REQUIRED_KEYS)) * 100) if USER_REQUIRED_KEYS else 0


def missing_field_labels(
    payload: dict[str, Any],
    field_labels: dict[str, str],
) -> list[str]:
    missing: list[str] = []
    for key in USER_REQUIRED_KEYS:
        if not payload.get(key) or not str(payload.get(key)).strip():
            missing.append(field_labels.get(key, key))
    return missing


def _description_has_section(text: str, markers: list[str]) -> bool:
    lower = text.lower()
    return any(m.lower() in lower for m in markers)


def build_description(answers: dict[str, Any]) -> str:
    parts: list[str] = []
    base = str(answers.get("description") or "").strip()
    if base:
        parts.append(base)

    env_lines = [
        answers.get("env")
        and str(answers.get("env")).lower() != "skip"
        and f"ENV: {answers['env']}",
        answers.get("aura") and f"AURA: {answers['aura']}",
        answers.get("swym") and f"SWYM: {answers['swym']}",
        answers.get("swymUi") and f"SWYM UI: {answers['swymUi']}",
    ]
    env_lines = [line for line in env_lines if line]

    combined = "\n\n".join(parts)
    if env_lines and not _description_has_section(combined, ["environment:", "env:"]):
        parts.append("Environment:\n" + "\n".join(env_lines))
    if answers.get("steps") and not _description_has_section(combined, ["steps to reproduce"]):
        parts.append(f"**Steps to Reproduce:**\n{answers['steps']}")
    if answers.get("expected") and not _description_has_section(combined, ["expected result"]):
        parts.append(f"**Expected Result:**\n{answers['expected']}")
    if answers.get("actual") and not _description_has_section(combined, ["actual result"]):
        parts.append(f"**Actual Result:**\n{answers['actual']}")
    attachment = answers.get("attachment")
    if attachment and not _description_has_section(combined, ["pfa attach"]):
        att_type = ""
        if isinstance(attachment, dict):
            att_type = str(attachment.get("type") or "")
        kind = "video" if att_type.startswith("video") else "screenshot"
        parts.append(f"PFA attach {kind} for reference")
    return "\n\n".join(parts)


def apply_linked_field_update(fields: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    from backend.ui_config import LINKED_FIELD_GROUPS

    next_fields = dict(fields)
    next_fields[key] = value
    for group in LINKED_FIELD_GROUPS:
        if key in group.get("displayKeys", []):
            eno_key = group.get("enoKey")
            if eno_key:
                next_fields.pop(eno_key, None)
    return next_fields


def merge_field_updates(fields: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    next_fields = dict(fields)
    for key, value in updates.items():
        next_fields = apply_linked_field_update(next_fields, key, value)
    return next_fields


def saved_form_summary(form_fields: dict[str, Any], field_labels: dict[str, str]) -> str:
    internal = set(INTERNAL_FIELD_KEYS)
    lines = [
        f"• {field_labels.get(key, key)}: {value}"
        for key, value in (form_fields or {}).items()
        if value and key not in internal
    ]
    return "\n".join(lines) if lines else "No saved details found."
