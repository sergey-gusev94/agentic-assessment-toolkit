"""`aat check-course`: read-only completeness and contract report.

The checker closes the intake loop (docs/course-intake.md): the intake
agent extracts best-effort, the checker says exactly what is broken,
what is missing, and what the agent left for the maintainer. Three
tiers, reported separately:

- **Contract violations** — the course tree breaks a convention the
  pipeline relies on; `aat check-course` exits nonzero.
- **Completeness gaps** — nothing is broken, but something the pipeline
  or the registry could use is not there yet.
- **Intake notes** — `intake-notes.md`, surfaced verbatim: judgment
  calls and everything intake looked for and could not find.

It changes no experiment and no doneness, and never writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config, data_root
from .course import Course, CourseError, load_course
from .rubric import RubricError, parse_rubric_file

WEIGHT_SUM_TOLERANCE = 0.01

_EXPECTED_COURSE_ENTRIES = frozenset(
    {
        "course.toml",
        "assignments",
        "reference_solutions",
        "rubrics",
        "syllabus",
        "intake-notes.md",
    }
)


@dataclass
class CourseCheck:
    """One course's report; render with :func:`format_report`."""

    course_id: str
    violations: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    notes: str | None = None  # intake-notes.md, verbatim

    @property
    def ok(self) -> bool:
        return not self.violations


def check_course(root: Path, course_id: str) -> CourseCheck:
    course_dir = root / "courses" / course_id
    if not course_dir.is_dir():
        raise data_root.DataRootError(f"course {course_id!r} not found under {root / 'courses'}")
    report = CourseCheck(course_id=course_id)

    course = _check_course_toml(course_dir, report)
    assignment_ids = _check_assignments(course_dir, course, report)
    _check_registry(course, assignment_ids, report)
    _check_rubrics(course_dir, course, assignment_ids, report)
    _check_reference_solutions(course_dir, course, assignment_ids, report)
    _check_syllabus(course_dir, report)
    _check_unexpected_entries(course_dir, report)
    _read_notes(course_dir, report)
    _summarize_coverage(course, assignment_ids, report)
    return report


def _check_course_toml(course_dir: Path, report: CourseCheck) -> Course | None:
    course_toml = course_dir / "course.toml"
    if not course_toml.is_file():
        report.gaps.append("course.toml is missing: no course record and no assessment registry")
        return None
    try:
        course = load_course(course_toml)
    except CourseError as error:
        report.violations.append(str(error))
        return None

    absent = [key for key in ("title", "institution", "term") if getattr(course, key) is None]
    if absent:
        report.gaps.append(f"[course] leaves {', '.join(absent)} absent")
    if course.assessments is None:
        report.gaps.append(
            "course.toml has no [[assessments]] registry: weights and coverage are not computable"
        )
    return course


def _check_assignments(course_dir: Path, course: Course | None, report: CourseCheck) -> list[str]:
    assignments_dir = course_dir / "assignments"
    if not assignments_dir.is_dir():
        report.gaps.append("assignments/ is missing: no solvable material yet")
        return []
    ids = []
    for entry in sorted(assignments_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        ids.append(entry.name)
        if not any(path.is_file() for path in entry.rglob("*")):
            report.violations.append(
                f"assignments/{entry.name} is empty: an assignment directory "
                "must hold the as-received handout"
            )
        _check_environment(entry.name, course_dir, course, report)
    return ids


def _check_environment(
    assignment_id: str, course_dir: Path, course: Course | None, report: CourseCheck
) -> None:
    sidecar = course_dir / "assignments" / f"{assignment_id}.toml"
    flavor = None
    if sidecar.is_file():
        try:
            flavor = data_root.sidecar_flavor(sidecar)
        except data_root.DataRootError as error:
            report.violations.append(str(error))
            return
    if flavor is None and course is not None:
        flavor = course.environment
    if flavor is None:
        report.violations.append(
            f"assignments/{assignment_id} resolves no environment: set "
            f"'environment' in {assignment_id}.toml or a [course] default"
        )
        return
    if flavor == config.GRADING_FLAVOR:
        report.violations.append(
            f"assignments/{assignment_id} resolves environment 'grading', "
            "which is reserved for grading tasks"
        )
        return
    try:
        config.environment_path(flavor)
    except config.ConfigError as error:
        report.violations.append(f"assignments/{assignment_id}: {error}")


def _check_registry(course: Course | None, assignment_ids: list[str], report: CourseCheck) -> None:
    if course is None or course.assessments is None:
        return
    registry_ids = {entry.id for entry in course.assessments}

    for assignment_id in assignment_ids:
        if assignment_id not in registry_ids:
            report.violations.append(
                f"assignments/{assignment_id} has no [[assessments]] registry entry"
            )
    for entry in course.assessments:
        has_materials = entry.id in assignment_ids
        if entry.excluded is not None and has_materials:
            report.violations.append(
                f"assessment {entry.id!r} is marked excluded "
                f"({entry.excluded}) but assignments/{entry.id} exists"
            )
        if entry.excluded is None and not has_materials:
            report.gaps.append(
                f"assessment {entry.id!r} has no assignments/{entry.id} directory "
                "and no 'excluded' reason"
            )

    missing_weight = [entry.id for entry in course.assessments if entry.weight_pct is None]
    if missing_weight:
        report.gaps.append(f"assessments without 'weight_pct': {', '.join(missing_weight)}")
    elif course.assessments:
        total = sum(entry.weight_pct or 0.0 for entry in course.assessments)
        if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
            report.violations.append(
                f"assessment weights sum to {total:g}, not 100 (tolerance {WEIGHT_SUM_TOLERANCE})"
            )
    for key in ("type", "ai_policy"):
        absent = [entry.id for entry in course.assessments if getattr(entry, key) is None]
        if absent:
            report.gaps.append(f"assessments without {key!r}: {', '.join(absent)}")


def _check_rubrics(
    course_dir: Path, course: Course | None, assignment_ids: list[str], report: CourseCheck
) -> None:
    rubrics_dir = course_dir / "rubrics"
    known = set(assignment_ids)
    if course is not None and course.assessments is not None:
        known |= {entry.id for entry in course.assessments}
    if rubrics_dir.is_dir():
        for entry in sorted(rubrics_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            if entry.name not in known:
                report.violations.append(
                    f"rubrics/{entry.name} matches no assignment directory and no registry entry"
                )
            for rubric in sorted(entry.glob("*.md")):
                try:
                    parse_rubric_file(rubric)
                except RubricError as error:
                    report.violations.append(str(error))
    for assignment_id in assignment_ids:
        if not (rubrics_dir / assignment_id / "default.md").is_file():
            report.gaps.append(
                f"assignments/{assignment_id} has no rubrics/{assignment_id}/default.md: "
                "gradable only after the rubric is authored"
            )


def _check_reference_solutions(
    course_dir: Path, course: Course | None, assignment_ids: list[str], report: CourseCheck
) -> None:
    references_dir = course_dir / "reference_solutions"
    known = set(assignment_ids)
    if course is not None and course.assessments is not None:
        known |= {entry.id for entry in course.assessments}
    if references_dir.is_dir():
        for entry in sorted(references_dir.iterdir(), key=lambda p: p.name):
            if entry.is_dir() and entry.name not in known:
                report.violations.append(
                    f"reference_solutions/{entry.name} matches no assignment "
                    "directory and no registry entry"
                )
    for assignment_id in assignment_ids:
        reference = references_dir / assignment_id
        if not (reference.is_dir() and any(p.is_file() for p in reference.rglob("*"))):
            report.gaps.append(
                f"assignments/{assignment_id} has no reference solution under "
                f"reference_solutions/{assignment_id}: gradable only after one exists"
            )


def _check_syllabus(course_dir: Path, report: CourseCheck) -> None:
    syllabus_dir = course_dir / "syllabus"
    if not syllabus_dir.is_dir() or not any(p.is_file() for p in syllabus_dir.rglob("*")):
        report.gaps.append("syllabus/ is missing or empty: registry facts have no stored source")


def _check_unexpected_entries(course_dir: Path, report: CourseCheck) -> None:
    unexpected = sorted(
        entry.name for entry in course_dir.iterdir() if entry.name not in _EXPECTED_COURSE_ENTRIES
    )
    if unexpected:
        report.gaps.append(
            "unexpected entries in the course directory (misplaced intake "
            f"output?): {', '.join(unexpected)}"
        )


def _read_notes(course_dir: Path, report: CourseCheck) -> None:
    notes = course_dir / "intake-notes.md"
    if notes.is_file():
        report.notes = notes.read_text(encoding="utf-8")


def _summarize_coverage(
    course: Course | None, assignment_ids: list[str], report: CourseCheck
) -> None:
    if course is None or course.assessments is None:
        report.coverage.append(
            f"{len(assignment_ids)} assignment directorie(s); no registry, "
            "so grade coverage is not computable"
        )
        return
    entries = course.assessments
    with_materials = [entry for entry in entries if entry.id in assignment_ids]
    excluded = [entry for entry in entries if entry.excluded is not None]
    report.coverage.append(
        f"{len(with_materials)} of {len(entries)} registered assessments have "
        f"materials; {len(excluded)} excluded by reason"
    )
    if all(entry.weight_pct is not None for entry in entries):
        covered = sum(entry.weight_pct or 0.0 for entry in with_materials)
        report.coverage.append(f"materials cover {covered:g}% of the final grade")


def format_report(report: CourseCheck) -> str:
    lines = [f"course {report.course_id}"]

    def section(title: str, entries: list[str]) -> None:
        lines.append(f"{title} ({len(entries)}):" if entries else f"{title}: none")
        lines.extend(f"  - {entry}" for entry in entries)

    section("contract violations", report.violations)
    section("completeness gaps", report.gaps)
    if report.notes is None:
        lines.append("intake notes: none (intake-notes.md not present)")
    else:
        lines.append("intake notes (intake-notes.md):")
        lines.extend(f"  {line}" for line in report.notes.rstrip("\n").splitlines())
    for line in report.coverage:
        lines.append(f"coverage: {line}")
    return "\n".join(lines)
