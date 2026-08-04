"""Which rubric versions the stored results reference, and whether they resolve.

A rubric name is a label that may advance to new bytes; the recorded
sha256 is what a stored grading result actually refers to
(docs/data-conventions.md, "Course content contract"). This module reads
that reference out of the grading run records so two consumers can use
it: `aat grade`, which refuses to advance a rubric that would leave a
stored result unresolvable, and `aat check-course`, which reports the
same condition. Both read only; neither ever writes a rubric.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .data_root import RUBRIC_ARCHIVE_DIRNAME, rubric_versions
from .harbor import job_dirs, read_run_record


@dataclass(frozen=True)
class RecordedRubric:
    """One rubric version some grading job graded against."""

    course_id: str
    assignment_id: str
    # The name a config selected; absent when the record does not say.
    # Only the hash identifies the version, so an unnamed record is
    # still protected by the archive invariant.
    name: str | None
    sha256: str
    job_names: tuple[str, ...]

    @property
    def short_sha(self) -> str:
        return self.sha256[:8]


def recorded_rubrics(root: Path) -> list[RecordedRubric]:
    """Every rubric version any grading run record references, sorted.

    Malformed records and items contribute nothing: this is provenance
    reporting, and a record the loader cannot read is already reported
    as a missing result elsewhere.
    """
    jobs: dict[tuple[str, str, str | None, str], list[str]] = {}
    for job_dir in job_dirs(root / "grading"):
        record = read_run_record(job_dir)
        if record is None or record.get("stage") != "grade":
            continue
        config = record.get("config")
        raw_name = config.get("rubric") if isinstance(config, dict) else None
        name = raw_name if isinstance(raw_name, str) else None
        items = record.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            hashes = item.get("input_hashes")
            digest = hashes.get("rubric") if isinstance(hashes, dict) else None
            course_id = item.get("course_id")
            assignment_id = item.get("assignment_id")
            if not (
                isinstance(digest, str)
                and isinstance(course_id, str)
                and isinstance(assignment_id, str)
            ):
                continue
            names = jobs.setdefault((course_id, assignment_id, name, digest), [])
            if job_dir.name not in names:
                names.append(job_dir.name)
    return [
        RecordedRubric(
            course_id=course_id,
            assignment_id=assignment_id,
            name=name,
            sha256=digest,
            job_names=tuple(job_names),
        )
        for (course_id, assignment_id, name, digest), job_names in sorted(
            jobs.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or "", item[0][3])
        )
    ]


def orphaned_rubrics(
    root: Path,
    *,
    course_id: str | None = None,
    assignments: set[tuple[str, str]] | None = None,
) -> list[RecordedRubric]:
    """Recorded rubric versions whose bytes are on disk nowhere.

    Optionally narrowed to one course, or to a set of (course,
    assignment) pairs — what `aat grade` checks for the items it is
    about to plan. Narrowing filters one scan rather than repeating it
    per assignment: reading every grading run record is the expensive
    part, and a whole-course run has many assignments.
    """
    available: dict[tuple[str, str], dict[str, Path]] = {}
    orphans = []
    for recorded in recorded_rubrics(root):
        key = (recorded.course_id, recorded.assignment_id)
        if course_id is not None and recorded.course_id != course_id:
            continue
        if assignments is not None and key not in assignments:
            continue
        if key not in available:
            available[key] = rubric_versions(root, *key)
        if recorded.sha256 not in available[key]:
            orphans.append(recorded)
    return orphans


def orphan_message(root: Path, orphan: RecordedRubric) -> str:
    """One orphaned version, with the recovery that restores it.

    Every materialized grading task holds the rubric bytes it was built
    with, so a superseded version is recoverable from any job that used
    it even when nobody archived it before overwriting.
    """
    archive = (
        root
        / "courses"
        / orphan.course_id
        / "rubrics"
        / orphan.assignment_id
        / RUBRIC_ARCHIVE_DIRNAME
        / f"{orphan.short_sha}.md"
    )
    task_rubric = root / "tasks" / orphan.job_names[0] / "*" / "environment" / "rubric.md"
    named = f"rubric {orphan.name!r} version" if orphan.name else "rubric version"
    return (
        f"{orphan.course_id}/{orphan.assignment_id}: {named} "
        f"{orphan.short_sha} is referenced by "
        f"{', '.join(orphan.job_names)} but exists nowhere under "
        f"courses/{orphan.course_id}/rubrics/{orphan.assignment_id}/. "
        f"Recover the bytes from a materialized task ({task_rubric}) and "
        f"put them at {archive}"
    )
