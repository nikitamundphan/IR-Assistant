"""UI-facing constants and copy (single source of truth for the React client)."""

from __future__ import annotations

from typing import Any

SEVERITIES: list[dict[str, str]] = [
    {"label": "Critical", "value": "1", "color": "#EF4444", "desc": "Unusable / blocking"},
    {"label": "High", "value": "2", "color": "#F97316", "desc": "Major function broken"},
    {"label": "Medium", "value": "3", "color": "#EAB308", "desc": "Workaround exists"},
    {"label": "Low", "value": "4", "color": "#10B981", "desc": "Minor / cosmetic"},
]

ENVIRONMENTS: list[str] = ["Cloud", "On-Premise", "Windows", "Linux", "MacOS"]

DETAIL_KEYS: list[str] = [
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

USER_REQUIRED_KEYS: list[str] = [
    "title",
    "description",
    "severity",
    "detected_environment",
    "default_clarifier",
    "default_corrector",
    "default_validator",
    "rel_name",
    "rel_title",
    "rel_level_id",
    "feature_name",
    "detection_level_name",
]

INTERNAL_FIELD_KEYS: list[str] = ["rel_eno_id", "feature_eno_id", "detection_level_eno_id"]

FLOW_STEPS: list[dict[str, Any]] = [
    {
        "key": "context",
        "label": "Service",
        "phases": ["brand_pick", "service_pick", "program_pick", "release_pick"],
    },
    {
        "key": "setup",
        "label": "Setup",
        "phases": ["dashboard", "saved_form_list", "saved_review", "modify_pick", "manual_form", "release_name_ask"],
    },
    {"key": "details", "label": "Details", "phases": ["asking"]},
    {"key": "review", "label": "Review", "phases": ["duplicates", "review"]},
    {"key": "filed", "label": "Filed", "phases": ["creating", "uploading", "done"]},
]

LINKED_FIELD_GROUPS: list[dict[str, Any]] = [
    {
        "sectionId": "release",
        "displayKeys": ["rel_name", "rel_title", "rel_level_id"],
        "enoKey": "rel_eno_id",
    },
    {"sectionId": "feature", "displayKeys": ["feature_name"], "enoKey": "feature_eno_id"},
    {
        "sectionId": "detection",
        "displayKeys": ["detection_level_name", "detection_level_title"],
        "enoKey": "detection_level_eno_id",
    },
]

LINKED_FIELD_LABELS: dict[str, str] = {
    "rel_eno_id": "Target release",
    "feature_eno_id": "Feature",
    "detection_level_eno_id": "Detection program",
}


def main_menu_greeting(username: str | None) -> str:
    name = f"Hello, **{username}**!" if username else "Hello!"
    return (
        f"{name} I'm here to help you create an **Incident Report (IR)**.\n\n"
        "Choose what you'd like to do:\n"
        "1. **Create IR using a saved form**\n"
        "2. **Create IR without a saved form**\n"
        "3. **Set target release** (get the correct release ID / version for filing)"
    )


def ui_bootstrap_payload(username: str | None = None) -> dict[str, Any]:
    return {
        "severities": SEVERITIES,
        "environments": ENVIRONMENTS,
        "detail_keys": DETAIL_KEYS,
        "user_required_keys": USER_REQUIRED_KEYS,
        "internal_field_keys": INTERNAL_FIELD_KEYS,
        "flow_steps": FLOW_STEPS,
        "linked_field_groups": LINKED_FIELD_GROUPS,
        "linked_field_labels": LINKED_FIELD_LABELS,
        "main_menu_greeting": main_menu_greeting(username),
    }
