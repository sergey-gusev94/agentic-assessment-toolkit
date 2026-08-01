"""Course record and assessment registry: ``course.toml`` loading.

``course.toml`` holds the course record (the ``[course]`` table: title,
institution, term, and the default environment flavor) and the
assessment registry (the ``[[assessments]]`` array) — one entry per
syllabus assessment, including those that never get an assignment
directory (exams, attendance, presentations), so grade weights sum to
100 and benchmark coverage of the final grade is computable
(docs/data-conventions.md, "Course content contract"). Registry fields
are informational: the pipeline consumes only the environment default,
and `aat check-course` is the registry's reader. A fact the course
materials do not state is left absent, never written as a sentinel.
"""

from __future__ import annotations

import datetime
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

ASSESSMENT_TYPES = ("homework", "exam", "practice", "project", "attendance", "other")
ASSESSMENT_SCOPES = (
    "take_home",
    "online_exam",
    "in_person_exam",
    "presentation",
    "in_person",
)
# Permission is what the syllabus allows; ai_use_possible records
# separately whether AI use was physically feasible (an online exam may
# forbid AI yet not prevent it).
AI_POLICIES = ("allowed", "not_allowed", "not_applicable")

_KNOWN_COURSE_KEYS = frozenset({"title", "institution", "term", "environment"})
_KNOWN_ASSESSMENT_KEYS = frozenset(
    {
        "id",
        "title",
        "type",
        "scope",
        "weight_pct",
        "category",
        "ai_policy",
        "ai_use_possible",
        "due",
        "excluded",
    }
)


class CourseError(Exception):
    """A ``course.toml`` is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class Assessment:
    """One registry entry; every field but the id may be absent (None)."""

    id: str
    title: str | None
    type: str | None
    scope: str | None
    weight_pct: float | None
    category: str | None
    ai_policy: str | None
    ai_use_possible: bool | None
    due: datetime.date | None
    excluded: str | None


@dataclass(frozen=True)
class Course:
    """One loaded ``course.toml``; ``assessments`` is None when the
    registry has not been authored yet (an explicit ``assessments = []``
    is rejected at load time, so an authored registry is never empty)."""

    title: str | None
    institution: str | None
    term: str | None
    environment: str | None
    assessments: tuple[Assessment, ...] | None

    def assessment(self, assessment_id: str) -> Assessment | None:
        for entry in self.assessments or ():
            if entry.id == assessment_id:
                return entry
        return None


def load_course(path: Path) -> Course:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CourseError(f"cannot read {path} as UTF-8: {error}") from error
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CourseError(f"{path} is not valid TOML: {error}") from error

    unknown_top = sorted(set(data) - {"course", "assessments"})
    if unknown_top:
        raise CourseError(
            f"{path} has unknown top-level keys: {', '.join(unknown_top)} "
            "(course facts go in [course], assessments in [[assessments]])"
        )

    course_table = data.get("course", {})
    if not isinstance(course_table, dict):
        raise CourseError(f"{path}: [course] must be a table")
    unknown = sorted(set(course_table) - _KNOWN_COURSE_KEYS)
    if unknown:
        raise CourseError(f"{path}: [course] has unknown keys: {', '.join(unknown)}")

    raw_assessments = data.get("assessments")
    assessments: tuple[Assessment, ...] | None = None
    if raw_assessments is not None:
        if not isinstance(raw_assessments, list):
            raise CourseError(f"{path}: 'assessments' must be an array of tables")
        if not raw_assessments:
            raise CourseError(
                f"{path}: 'assessments = []' says nothing: omit the key entirely "
                "until the registry is authored (absent means unauthored)"
            )
        entries = [
            _parse_assessment(entry, index, path) for index, entry in enumerate(raw_assessments)
        ]
        seen: set[str] = set()
        for entry in entries:
            if entry.id in seen:
                raise CourseError(f"{path}: duplicate assessment id {entry.id!r}")
            seen.add(entry.id)
        assessments = tuple(entries)

    return Course(
        title=_optional_str(course_table, "title", path, "[course]"),
        institution=_optional_str(course_table, "institution", path, "[course]"),
        term=_optional_str(course_table, "term", path, "[course]"),
        environment=_optional_str(course_table, "environment", path, "[course]"),
        assessments=assessments,
    )


def _parse_assessment(entry: object, index: int, path: Path) -> Assessment:
    where = f"[[assessments]] entry {index + 1}"
    if not isinstance(entry, dict):
        raise CourseError(f"{path}: {where} must be a table")
    unknown = sorted(set(entry) - _KNOWN_ASSESSMENT_KEYS)
    if unknown:
        raise CourseError(f"{path}: {where} has unknown keys: {', '.join(unknown)}")

    assessment_id = entry.get("id")
    if not isinstance(assessment_id, str) or not assessment_id:
        raise CourseError(f"{path}: {where} needs a non-empty string 'id'")
    if any(ch.isspace() for ch in assessment_id) or "/" in assessment_id:
        raise CourseError(
            f"{path}: {where}: id {assessment_id!r} must contain no whitespace or '/'"
            " (it names directories and appears in item ids)"
        )
    where = f"assessment {assessment_id!r}"

    type_value = _optional_choice(entry, "type", ASSESSMENT_TYPES, path, where)
    scope = _optional_choice(entry, "scope", ASSESSMENT_SCOPES, path, where)
    ai_policy = _optional_choice(entry, "ai_policy", AI_POLICIES, path, where)

    weight = entry.get("weight_pct")
    if weight is not None:
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise CourseError(f"{path}: {where}: 'weight_pct' must be a number")
        weight = float(weight)
        if not math.isfinite(weight) or weight < 0:
            raise CourseError(f"{path}: {where}: 'weight_pct' must be finite and >= 0")

    possible = entry.get("ai_use_possible")
    if possible is not None and not isinstance(possible, bool):
        raise CourseError(f"{path}: {where}: 'ai_use_possible' must be a boolean")

    due = entry.get("due")
    # datetime is a date subclass: reject it explicitly rather than
    # silently truncating a stated time.
    if due is not None and (
        isinstance(due, datetime.datetime) or not isinstance(due, datetime.date)
    ):
        raise CourseError(f"{path}: {where}: 'due' must be a plain TOML date (e.g. 2026-02-06)")

    return Assessment(
        id=assessment_id,
        title=_optional_str(entry, "title", path, where),
        type=type_value,
        scope=scope,
        weight_pct=weight,
        category=_optional_str(entry, "category", path, where),
        ai_policy=ai_policy,
        ai_use_possible=possible,
        due=due,
        excluded=_optional_str(entry, "excluded", path, where),
    )


def _optional_str(data: dict[str, object], key: str, path: Path, where: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise CourseError(f"{path}: {where}: {key!r} must be a non-empty string")
    return value


def _optional_choice(
    data: dict[str, object],
    key: str,
    choices: tuple[str, ...],
    path: Path,
    where: str,
) -> str | None:
    value = _optional_str(data, key, path, where)
    if value is not None and value not in choices:
        raise CourseError(
            f"{path}: {where}: {key!r} must be one of {', '.join(choices)} (got {value!r})"
        )
    return value
