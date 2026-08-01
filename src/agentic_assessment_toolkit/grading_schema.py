"""Grading output schema: structural validation and authoritative sums.

Specified in docs/design.md ("Grading output schema"). Structural
violations fail the contract; the grader's authored sums are a
self-check only — the sums computed from the criteria are authoritative
everywhere, and an authored-sum mismatch is reported as an
inconsistency, never a failure. This module is stdlib-only and
self-contained by design: it is imported by the package and also copied
verbatim into every materialized grading task beside the generic
grading verifier, so validation has exactly one source of truth.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

SCHEMA_VERSION = 1
RESULT_FILENAME = "grading_result.json"
JUSTIFICATION_FILENAME = "justification.md"

_REQUIRED_FIELDS = (
    "schema_version",
    "criteria",
    "overall_comment",
)
# Authored by the grader as a self-check; validated for consistency in
# sums_report(), never required and never authoritative.
_SUM_FIELDS = ("raw_points", "raw_max", "bonus_points", "bonus_max")
_ABS_TOL = 1e-6


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=_ABS_TOL)


def validate_grading_result(data: object) -> list[str]:
    """Return every contract violation in a grading result; empty means valid.

    Structural checks only: the authored sum fields are a self-check
    compared separately by ``sums_report`` and never fail the contract.
    """
    if not isinstance(data, dict):
        return ["grading result must be a JSON object"]

    errors = [
        f"missing required field {field!r}" for field in _REQUIRED_FIELDS if field not in data
    ]

    schema_version = data.get("schema_version")
    if "schema_version" in data and (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        errors.append(f"schema_version must be the integer {SCHEMA_VERSION}")

    criteria = data.get("criteria")
    if "criteria" in data:
        if not isinstance(criteria, list) or not criteria:
            errors.append("criteria must be a non-empty list")
        else:
            seen_ids: set[str] = set()
            usable = True
            non_bonus_count = 0
            for index, entry in enumerate(criteria):
                label = f"criteria[{index}]"
                if not isinstance(entry, dict):
                    errors.append(f"{label} must be an object")
                    usable = False
                    continue
                entry_errors = _validate_criterion(label, entry, seen_ids)
                if entry_errors:
                    errors.extend(entry_errors)
                    usable = False
                elif not entry.get("bonus", False):
                    non_bonus_count += 1
            if usable and non_bonus_count == 0:
                errors.append("at least one criterion must be non-bonus")

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


def _as_number(value: object) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"expected a number, got {value!r}")
    return float(value)


def computed_sums(data: dict[str, object]) -> dict[str, float]:
    """The point sums computed from the criteria — the authoritative sums.

    Call only on data that passed validate_grading_result.
    """
    criteria = data["criteria"]
    if not isinstance(criteria, list):
        raise ValueError("criteria must be a list")
    sums = dict.fromkeys(_SUM_FIELDS, 0.0)
    for entry in criteria:
        if not isinstance(entry, dict):
            raise ValueError("criteria entries must be objects")
        points = _as_number(entry["points"])
        max_points = _as_number(entry["max_points"])
        if entry.get("bonus", False):
            sums["bonus_points"] += points
            sums["bonus_max"] += max_points
        else:
            sums["raw_points"] += points
            sums["raw_max"] += max_points
    return sums


def sums_report(data: dict[str, object]) -> dict[str, object]:
    """Compare the grader's authored sums against the computed sums.

    The authored sums are a self-check: a missing, non-numeric, or
    mismatching value makes the report inconsistent but is never a
    contract violation (docs/design.md, "Grading output schema"). The
    inconsistency rate per grader configuration is a judge-quality
    signal for the statistics layer. Call only on data that passed
    validate_grading_result.
    """
    computed = computed_sums(data)
    authored = {field: data.get(field) for field in _SUM_FIELDS}
    consistent = True
    for field in _SUM_FIELDS:
        value = authored[field]
        if not _is_number(value) or not _close(float(value), computed[field]):  # type: ignore[arg-type]
            consistent = False
    return {"consistent": consistent, "authored": authored, "computed": computed}


def derive_scores(data: dict[str, object]) -> dict[str, float]:
    """Derive the percentage scores from a validated grading result.

    ``score_pct`` counts earned bonus points over the required maximum,
    so it can exceed 100; ``required_pct`` covers required criteria only
    (0-100). Percentages are never authored by the grader, and the sums
    they derive from are computed from the criteria, never read from the
    authored self-check fields — all summation and division lives here.
    Call only on data that passed validate_grading_result, which
    guarantees a non-bonus criterion exists and so ``raw_max > 0``.
    """
    sums = computed_sums(data)
    return {
        "score_pct": 100.0 * (sums["raw_points"] + sums["bonus_points"]) / sums["raw_max"],
        "required_pct": 100.0 * sums["raw_points"] / sums["raw_max"],
    }


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
