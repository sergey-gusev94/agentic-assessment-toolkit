"""Grading output schema: structural validation and authoritative sums.

Specified in docs/design.md ("Grading output schema"). Structural
violations fail the contract, and so does any divergence between the
grader's authored criteria and the rubric's expected criteria (see
expected_criteria_errors); the grader's authored sums are a self-check
only — the sums computed from the criteria are authoritative
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
# Written by the grading-task materializer beside the verifier: the
# rubric's criteria as an ordered list of {id, max_points, bonus}
# objects. The verifier compares the grader's authored criteria against
# it; see expected_criteria_errors.
EXPECTED_CRITERIA_FILENAME = "expected_criteria.json"
# Also materializer-written beside the verifier, only for tasks that owe
# deliverables beyond the two standard files: a JSON list of extra
# grading_output filenames the verifier requires to be present and
# non-empty. Final-judge tasks declare the student-facing feedback
# document this way; see required_files_errors.
REQUIRED_FILES_FILENAME = "required_files.json"
# The student-facing feedback document a final-judge task requires.
FEEDBACK_FILENAME = "feedback.md"

_REQUIRED_FIELDS = (
    "schema_version",
    "criteria",
    "overall_comment",
)
# Authored by the grader as a self-check; validated for consistency in
# sums_report(), never required and never authoritative.
_SUM_FIELDS = ("base_points", "base_max", "bonus_points", "bonus_max")
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


def _format_points(value: float) -> str:
    return format(value, "g")


def expected_criteria_errors(
    expected: list[dict[str, object]], data: dict[str, object]
) -> list[str]:
    """Compare the grader's authored criteria against the rubric's.

    ``expected`` is the materializer-written expected-criteria list —
    the rubric's criteria as {id, max_points, bonus} objects. The
    grader must reproduce the rubric exactly: the id sets must match,
    and each criterion's max_points and bonus flag must equal the
    rubric's. Any divergence is a contract failure — a dropped,
    renamed, added, or reweighted criterion silently changes the score
    denominator, so it must fail the trial loudly. Call only on data
    that passed validate_grading_result.
    """
    criteria = data["criteria"]
    if not isinstance(criteria, list):
        raise ValueError("criteria must be a list")
    authored: dict[str, dict[str, object]] = {}
    for entry in criteria:
        if not isinstance(entry, dict):
            raise ValueError("criteria entries must be objects")
        authored[str(entry["id"])] = entry

    errors = []
    expected_ids = {str(entry["id"]) for entry in expected}
    for entry in expected:
        criterion_id = str(entry["id"])
        if criterion_id not in authored:
            errors.append(
                f"rubric mismatch: criterion {criterion_id!r} from the rubric"
                " is missing from the grading result"
            )
    for criterion_id in authored:
        if criterion_id not in expected_ids:
            errors.append(f"rubric mismatch: criterion {criterion_id!r} is not in the rubric")
    for entry in expected:
        criterion_id = str(entry["id"])
        got = authored.get(criterion_id)
        if got is None:
            continue
        expected_max = _as_number(entry["max_points"])
        got_max = _as_number(got["max_points"])
        if not _close(got_max, expected_max):
            errors.append(
                f"rubric mismatch: criterion {criterion_id!r}: max_points"
                f" {_format_points(got_max)} does not match the rubric's"
                f" {_format_points(expected_max)}"
            )
        got_bonus = bool(got.get("bonus", False))
        expected_bonus = bool(entry.get("bonus", False))
        if got_bonus != expected_bonus:
            errors.append(
                f"rubric mismatch: criterion {criterion_id!r}: bonus"
                f" {json.dumps(got_bonus)} does not match the rubric's"
                f" {json.dumps(expected_bonus)}"
            )
    return errors


def _as_number(value: object) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"expected a number, got {value!r}")
    return float(value)


def text_file_errors(path: Path) -> list[str]:
    """Contract errors for a required text deliverable; empty means valid.

    Required text files must exist, decode as UTF-8, and be non-blank —
    the same rule for ``justification.md`` and any declared extra
    deliverable such as the final judge's feedback document.
    """
    if not path.is_file():
        return [f"missing required file {path.name}"]
    try:
        if not path.read_text(encoding="utf-8").strip():
            return [f"{path.name} is empty"]
    except UnicodeDecodeError:
        return [f"{path.name} is not UTF-8 text"]
    except OSError as error:
        return [f"cannot read {path.name}: {error}"]
    return []


def required_files_errors(declared: object, output_dir: Path) -> list[str]:
    """Check a task's declared extra deliverables; empty means satisfied.

    ``declared`` is the parsed content of ``REQUIRED_FILES_FILENAME``:
    a list of plain filenames inside the grading output directory. The
    file is materializer-written, so a malformed declaration is a
    contract error in its own right, never an uncaught crash.
    """
    if not isinstance(declared, list) or not all(
        isinstance(name, str) and name and "/" not in name and "\\" not in name for name in declared
    ):
        return [f"malformed {REQUIRED_FILES_FILENAME}: expected a list of plain filenames"]
    errors = []
    for name in declared:
        errors.extend(text_file_errors(output_dir / name))
    return errors


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
            sums["base_points"] += points
            sums["base_max"] += max_points
    return sums


def _finite_number(value: object) -> float | None:
    """The value as a finite float; None for anything else.

    Authored sums are untrusted JSON: booleans, non-numbers, NaN and
    Infinity (json.loads accepts the bare literals), and integers too
    large for a float (OverflowError) all yield None.
    """
    if not _is_number(value):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


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
    authored: dict[str, object] = {}
    consistent = True
    for field in _SUM_FIELDS:
        value = data.get(field)
        # Non-finite floats are not valid strict JSON; record their repr
        # so the report stays serializable everywhere.
        if isinstance(value, float) and not math.isfinite(value):
            authored[field] = repr(value)
        else:
            authored[field] = value
        number = _finite_number(value)
        if number is None or not _close(number, computed[field]):
            consistent = False
    return {"consistent": consistent, "authored": authored, "computed": computed}


def derive_scores(data: dict[str, object]) -> dict[str, float]:
    """Derive the percentage scores from a validated grading result.

    ``score_pct`` counts earned bonus points over the base maximum, so
    it can exceed 100; ``base_pct`` covers base (non-bonus) criteria
    only (0-100). Percentages are never authored by the grader, and the sums
    they derive from are computed from the criteria, never read from the
    authored self-check fields — all summation and division lives here.
    Call only on data that passed validate_grading_result, which
    guarantees a non-bonus criterion exists and so ``base_max > 0``.
    """
    sums = computed_sums(data)
    return {
        "score_pct": 100.0 * (sums["base_points"] + sums["bonus_points"]) / sums["base_max"],
        "base_pct": 100.0 * sums["base_points"] / sums["base_max"],
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
