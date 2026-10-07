"""IR wizard field schema served to the React client."""

from __future__ import annotations

from typing import Any

IR_FIELD_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "release",
        "label": "Target release (fix version)",
        "intro": "The release where the issue should be fixed — not the build where you found it.",
        "fields": [
            {
                "key": "rel_title",
                "label": "Release title",
                "prompt": "What is the release title (display name)?",
                "hint": "Human-readable release name (e.g. R2026x GA).",
                "placeholder": "e.g. R2026x GA",
                "type": "text",
            },
            {
                "key": "rel_name",
                "label": "Release short name",
                "prompt": "What is the release short name / code?",
                "hint": "Internal release code (e.g. REL002009).",
                "placeholder": "e.g. REL002009",
                "type": "text",
            },
            {
                "key": "rel_level_id",
                "label": "Release level ID",
                "prompt": "What is the release level ID?",
                "hint": "Usually the same as the release code.",
                "placeholder": "e.g. REL002009",
                "type": "text",
            },
            {
                "key": "rel_eno_id",
                "label": "Target release ID",
                "internal": True,
            },
        ],
    },
    {
        "id": "feature",
        "label": "Concerned feature",
        "intro": "The product feature or functional area affected.",
        "fields": [
            {
                "key": "feature_name",
                "label": "Feature name",
                "prompt": "Which feature is affected?",
                "hint": "Display name of the feature or collection.",
                "placeholder": "Feature name",
                "type": "text",
            },
            {
                "key": "feature_eno_id",
                "label": "Feature reference",
                "internal": True,
            },
        ],
    },
    {
        "id": "detection",
        "label": "Issue detected version",
        "intro": "The program or build level where you detected the issue.",
        "fields": [
            {
                "key": "detection_level_name",
                "label": "Issue detected version",
                "prompt": "Which version did you detect this issue in?",
                "hint": "Program code or version label (e.g. PRG044546). The ID is looked up automatically.",
                "placeholder": "e.g. PRG044546",
                "type": "text",
            },
            {
                "key": "detection_level_title",
                "label": "Detection version title",
                "internal": True,
            },
            {
                "key": "detection_level_eno_id",
                "label": "Detection program reference",
                "internal": True,
            },
        ],
    },
    {
        "id": "actors",
        "label": "Clarifier, corrector & validator",
        "intro": "DS login (trigram) of the people who clarify, correct, and validate this IR.",
        "fields": [
            {
                "key": "default_clarifier",
                "label": "Clarifier",
                "prompt": "Who is the clarifier (DS login)?",
                "hint": "Usually your trigram.",
                "placeholder": "e.g. nmn38",
                "type": "text",
            },
            {
                "key": "default_corrector",
                "label": "Corrector",
                "prompt": "Who is the corrector (DS login)?",
                "hint": "Developer responsible for the fix.",
                "placeholder": "Trigram",
                "type": "text",
            },
            {
                "key": "default_validator",
                "label": "Validator",
                "prompt": "Who is the validator (DS login)?",
                "hint": "Person who validates the fix.",
                "placeholder": "Trigram",
                "type": "text",
            },
            {
                "key": "owner",
                "label": "Owner",
                "prompt": "IR owner (DS login)? Leave blank to use your login.",
                "hint": "Defaults to your account if skipped.",
                "placeholder": "Optional",
                "type": "text",
                "optional": True,
            },
        ],
    },
]


def ir_field_schema_payload() -> dict[str, Any]:
    return {"sections": IR_FIELD_SECTIONS}
