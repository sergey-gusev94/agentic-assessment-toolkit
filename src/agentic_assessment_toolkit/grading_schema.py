"""Grading output schema: fields and internal-consistency validation.

Specified in docs/design.md ("Grading output schema"). This module is
stdlib-only and self-contained by design: it is imported by the package
and also copied verbatim into every materialized grading task beside the
generic grading verifier, so validation has exactly one source of truth.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

SCHEMA_VERSION = 1
RESULT_FILENAME = "grading_result.json"
JUSTIFICATION_FILENAME = "justification.md"

_TOP_LEVEL_FIELDS = (
    "schema_version",
    "criteria",
    "raw_points",
    "raw_max",
    "bonus_points",
    "bonus_max",
    "score_pct",
    "overall_comment",
)
_ABS_TOL = 1e-6


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=_ABS_TOL)


def validate_grading_result(data: object) -> list[str]:
    """Return every contract violation in a grading result; empty means valid."""
    if not isinstance(data, dict):
        return ["grading result must be a JSON object"]

    errors = [
        f"missing required field {field!r}" for field in _TOP_LEVEL_FIELDS if field not in data
    ]

    schema_version = data.get("schema_version")
    if "schema_version" in data and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        errors.append(f"schema_version must be the integer {SCHEMA_VERSION}")

    criteria = data.get("criteria")
    raw_points = 0.0
    raw_max = 0.0
    bonus_points = 0.0
    bonus_max = 0.0
    criteria_usable = False
    if "criteria" in data:
        if not isinstance(criteria, list) or not criteria:
            errors.append("criteria must be a non-empty list")
        else:
            criteria_usable = True
            seen_ids: set[str] = set()
            non_bonus_count = 0
            for index, entry in enumerate(criteria):
                label = f"criteria[{index}]"
                if not isinstance(entry, dict):
                    errors.append(f"{label} must be an object")
                    criteria_usable = False
                    continue
                entry_errors = _validate_criterion(label, entry, seen_ids)
                if entry_errors:
                    errors.extend(entry_errors)
                    criteria_usable = False
                    continue
                points = float(entry["points"])
                max_points = float(entry["max_points"])
                if entry.get("bonus", False):
                    bonus_points += points
                    bonus_max += max_points
                else:
                    non_bonus_count += 1
                    raw_points += points
                    raw_max += max_points
            if criteria_usable and non_bonus_count == 0:
                errors.append("at least one criterion must be non-bonus")
                criteria_usable = False

    if criteria_usable:
        errors.extend(_validate_aggregates(data, raw_points, raw_max, bonus_points, bonus_max))

    comment = data.get("overall_comment")
    if "overall_comment" in data and not isinstance(comment, str):
        errors.append("overall_comment must be a string")

    return errors


def _validate_criterion(label: str, entry: dict[str, object], seen_ids: set[str]) -> list[str]:
    errors = []

    criterion_id = entry.get("id")
    if not isinstance(criterion_id, str) or not criterion_id.strip():
        errors.append(f"{label}: id must be a non-empty string")
    elif criterion_id in seen_ids:
        errors.append(f"{label}: duplicate criterion id {criterion_id!r}")
    else:
        seen_ids.add(criterion_id)

    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"{label}: title must be a non-empty string")

    # json.loads accepts NaN/Infinity, so finiteness is checked explicitly:
    # a non-finite value would otherwise propagate into score_pct and the
    # Harbor reward file.
    max_points = entry.get("max_points")
    if (
        not _is_number(max_points)
        or not math.isfinite(float(max_points))  # type: ignore[arg-type]
        or float(max_points) <= 0  # type: ignore[arg-type]
    ):
        errors.append(f"{label}: max_points must be a finite number > 0")

    points = entry.get("points")
    if not _is_number(points) or not math.isfinite(float(points)):  # type: ignore[arg-type]
        errors.append(f"{label}: points must be a finite number")
    elif _is_number(max_points) and not (
        0 <= float(points) <= float(max_points)  # type: ignore[arg-type]
    ):
        errors.append(f"{label}: points must satisfy 0 <= points <= max_points")

    evidence = entry.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        errors.append(f"{label}: evidence must be a non-empty string")

    bonus = entry.get("bonus", False)
    if not isinstance(bonus, bool):
        errors.append(f"{label}: bonus must be a boolean")

    return errors


def _validate_aggregates(
    data: dict[str, object],
    raw_points: float,
    raw_max: float,
    bonus_points: float,
    bonus_max: float,
) -> list[str]:
    errors = []
    expected = {
        "raw_points": raw_points,
        "raw_max": raw_max,
        "bonus_points": bonus_points,
        "bonus_max": bonus_max,
    }
    usable = True
    for field, value in expected.items():
        declared = data.get(field)
        if not _is_number(declared):
            if field in data:
                errors.append(f"{field} must be a number")
            usable = False
        elif not _close(float(declared), value):  # type: ignore[arg-type]
            errors.append(f"{field} is {declared}, but the criteria sum to {value}")
            usable = False

    score_pct = data.get("score_pct")
    if not _is_number(score_pct):
        if "score_pct" in data:
            errors.append("score_pct must be a number")
    elif usable and raw_max > 0:
        expected_pct = 100.0 * raw_points / raw_max
        if not _close(float(score_pct), expected_pct):  # type: ignore[arg-type]
            errors.append(
                f"score_pct is {score_pct}, but 100 * raw_points / raw_max is {expected_pct}"
            )
    return errors


def load_grading_result(path: Path) -> tuple[dict[str, object] | None, list[str]]:
    """Read and validate a grading result file; returns (data, errors).

    ``data`` is None when the file is missing or unparseable; parse
    problems are reported through ``errors`` like any other violation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return None, [f"cannot read {path.name}: {error}"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return None, [f"{path.name} is not valid JSON: {error}"]
    if not isinstance(data, dict):
        return None, ["grading result must be a JSON object"]
    return data, validate_grading_result(data)
