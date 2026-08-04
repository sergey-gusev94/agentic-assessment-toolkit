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

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import config, data_root, provenance
from .course import RUBRIC_PROVENANCES, Course, CourseError, load_course
from .rubric import RubricError, parse_rubric_file

WEIGHT_SUM_TOLERANCE = 0.01


def _has_files(directory: Path) -> bool:
    """Whether any file exists under ``directory``.

    Follows directory symlinks, matching what the materializers copy
    (``shutil.copytree`` and ``hashing.file_manifest`` both follow
    links), so the checker never calls a directory empty that the
    pipeline would materialize with content.
    """
    return any(filenames for _, _, filenames in os.walk(directory, followlinks=True))


_EXPECTED_COURSE_ENTRIES = frozenset(
    {
        "course.toml",
        "assignments",
        "reference_solutions",
        "rubrics",
        "syllabus",
        "intake-notes.md",
        "intake-record.json",
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
    _check_rubric_history(root, course_id, report)
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
    entries = sorted(assignments_dir.iterdir(), key=lambda p: p.name)
    ids = [entry.name for entry in entries if entry.is_dir()]
    for entry in entries:
        if entry.is_dir():
            if not _has_files(entry):
                report.violations.append(
                    f"assignments/{entry.name} is empty: an assignment directory "
                    "must hold the as-received handout"
                )
            _check_environment(entry.name, course_dir, course, report)
        elif entry.suffix == ".toml":
            # A sidecar whose assignment directory does not exist would
            # silently never apply — the worst kind of misnaming.
            if entry.stem not in ids:
                report.violations.append(
                    f"assignments/{entry.name} is a sidecar for a missing "
                    f"assignment directory assignments/{entry.stem}"
                )
        else:
            report.gaps.append(
                f"stray file assignments/{entry.name}: assignments/ holds only "
                "handout directories and <id>.toml sidecars"
            )
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
    for key in ("type", "ai_policy", "ai_use_possible"):
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
                report.gaps.append(
                    f"stray file rubrics/{entry.name}: rubrics live in "
                    "rubrics/<assignment_id>/<name>.md"
                )
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
            archive = entry / data_root.RUBRIC_ARCHIVE_DIRNAME
            if archive.is_file():
                report.violations.append(
                    f"rubrics/{entry.name}/{data_root.RUBRIC_ARCHIVE_DIRNAME} is a file: "
                    "superseded rubric versions go inside an "
                    f"{data_root.RUBRIC_ARCHIVE_DIRNAME}/ directory"
                )
            source = entry / "source"
            if source.is_file():
                report.violations.append(
                    f"rubrics/{entry.name}/source is a file: the professor's "
                    "rubric document(s) go inside a source/ directory, which is "
                    "what the grading task presents"
                )
            elif source.is_dir() and not _has_files(source):
                report.violations.append(
                    f"rubrics/{entry.name}/source is empty: it should hold the "
                    "professor's rubric document(s) verbatim, or not exist at all"
                )
    for assignment_id in assignment_ids:
        if not (rubrics_dir / assignment_id / "default.md").is_file():
            report.gaps.append(
                f"assignments/{assignment_id} has no rubrics/{assignment_id}/default.md: "
                "gradable only after the rubric is authored"
            )
    if course is not None and course.assessments is not None:
        for assessment in course.assessments:
            has_default = (rubrics_dir / assessment.id / "default.md").is_file()
            source = rubrics_dir / assessment.id / "source"
            if assessment.rubric_provenance is not None and not has_default:
                report.violations.append(
                    f"assessment {assessment.id!r} records rubric_provenance "
                    f"{assessment.rubric_provenance!r} but rubrics/{assessment.id}/default.md "
                    "does not exist"
                )
            if (
                assessment.rubric_provenance not in (None, "professor_rubric")
                and source.is_dir()
                and _has_files(source)
            ):
                report.violations.append(
                    f"assessment {assessment.id!r} records rubric_provenance "
                    f"{assessment.rubric_provenance!r} but rubrics/{assessment.id}/source/ "
                    "holds the professor's own rubric — that document states the "
                    "point split, so the provenance is 'professor_rubric'"
                )
            if has_default and assessment.rubric_provenance is None:
                report.gaps.append(
                    f"rubrics/{assessment.id}/default.md exists but assessment "
                    f"{assessment.id!r} records no 'rubric_provenance' (one of "
                    f"{', '.join(RUBRIC_PROVENANCES)})"
                )


def _check_rubric_history(root: Path, course_id: str, report: CourseCheck) -> None:
    """Every rubric version the stored grading results refer to still exists.

    A rubric name may advance to new bytes, but the superseded bytes
    must stay under ``archive/``: a stored result refers to the version
    it was graded against by hash, and losing those bytes makes the
    result unresolvable. Counted as a contract violation, since it
    breaks the provenance the results tables rely on.
    """
    for orphan in provenance.orphaned_rubrics(root, course_id=course_id):
        report.violations.append(provenance.orphan_message(root, orphan))


def _check_reference_solutions(
    course_dir: Path, course: Course | None, assignment_ids: list[str], report: CourseCheck
) -> None:
    references_dir = course_dir / "reference_solutions"
    known = set(assignment_ids)
    if course is not None and course.assessments is not None:
        known |= {entry.id for entry in course.assessments}
    if references_dir.is_dir():
        for entry in sorted(references_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                report.gaps.append(
                    f"stray file reference_solutions/{entry.name}: reference "
                    "solutions live in reference_solutions/<assignment_id>/"
                )
            elif entry.name not in known:
                report.violations.append(
                    f"reference_solutions/{entry.name} matches no assignment "
                    "directory and no registry entry"
                )
    for assignment_id in assignment_ids:
        reference = references_dir / assignment_id
        if not (reference.is_dir() and _has_files(reference)):
            report.gaps.append(
                f"assignments/{assignment_id} has no reference solution under "
                f"reference_solutions/{assignment_id}: gradable only after one exists"
            )


def _check_syllabus(course_dir: Path, report: CourseCheck) -> None:
    syllabus_dir = course_dir / "syllabus"
    if not (syllabus_dir.is_dir() and _has_files(syllabus_dir)):
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
        try:
            report.notes = notes.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            report.violations.append(f"cannot read intake-notes.md as UTF-8: {error}")


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
    if all(entry.weight_pct is not None for entry in with_materials):
        covered = sum(entry.weight_pct or 0.0 for entry in with_materials)
        if all(entry.weight_pct is not None for entry in entries):
            report.coverage.append(f"materials cover {covered:g}% of the final grade")
        else:
            report.coverage.append(
                f"materials cover at least {covered:g}% of the final grade "
                "(some assessments have no weight yet)"
            )


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
