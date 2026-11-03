"""Resolve a submission's declared rubric from reviewed local enrollment data.

Enrollment evidence stays in the data root. It is never inferred from student
answers and never copied into a grading task. An assignment opts in by declaring
its eligible rubric names in the assessment registry.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .course import Course, load_course
from .ingest import IngestError, Student, read_students

FILENAME = "enrollments.csv"
COLUMNS = ("lms_username", "rubric", "source")


class EnrollmentError(Exception):
    """An enrollment record cannot safely select a rubric."""


def username(value: str) -> str:
    return value.strip().lstrip("#").casefold()


@dataclass(frozen=True)
class Enrollment:
    rubric: str
    source: str


@dataclass(frozen=True)
class Selection:
    name: str
    routed: bool

    @property
    def identity(self) -> str | None:
        return self.name if self.routed else None


def read_enrollments(root: Path, course_id: str) -> dict[str, Enrollment]:
    path = root / "tables" / course_id / FILENAME
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames is None or tuple(reader.fieldnames) != COLUMNS:
                raise EnrollmentError(f"{path} requires columns {', '.join(COLUMNS)}")
            result = {}
            for row in reader:
                if set(row) != set(COLUMNS) or not all(
                    isinstance(value, str) and value.strip() for value in row.values()
                ):
                    raise EnrollmentError(f"{path}, row {reader.line_num}: all fields are required")
                key = username(row["lms_username"])
                if not key or key in result:
                    raise EnrollmentError(
                        f"{path}, row {reader.line_num}: empty or duplicate LMS username"
                    )
                result[key] = Enrollment(row["rubric"].strip(), row["source"].strip())
            return result
    except (OSError, UnicodeError, csv.Error) as error:
        raise EnrollmentError(f"cannot read {path}: {error}") from error


class Resolver:
    """One command's policy and identity-table cache."""

    def __init__(self, root: Path):
        self.root = root
        self._courses: dict[str, Course | None] = {}
        self._students: dict[str, list[Student]] = {}
        self._enrollments: dict[str, dict[str, Enrollment]] = {}

    def rubrics(self, course_id: str, assignment_id: str) -> tuple[str, ...] | None:
        if course_id not in self._courses:
            path = self.root / "courses" / course_id / "course.toml"
            self._courses[course_id] = load_course(path) if path.is_file() else None
        course = self._courses[course_id]
        assessment = course.assessment(assignment_id) if course is not None else None
        return assessment.rubrics if assessment is not None else None

    def select(
        self,
        course_id: str,
        assignment_id: str,
        student_id: str | None,
        requested: str,
        *,
        explicit: bool = False,
    ) -> Selection:
        eligible = self.rubrics(course_id, assignment_id)
        if eligible is None:
            return Selection(requested, False)
        if student_id is None or student_id.startswith("_"):
            if not explicit or requested not in eligible:
                raise EnrollmentError(
                    f"{course_id}/{assignment_id}: no enrolled student; choose an eligible "
                    f"--rubric ({', '.join(eligible)}) explicitly"
                )
            return Selection(requested, True)
        if course_id not in self._students:
            try:
                self._students[course_id] = read_students(self.root, course_id)
            except IngestError as error:
                raise EnrollmentError(str(error)) from error
            self._enrollments[course_id] = read_enrollments(self.root, course_id)
        matches = [s for s in self._students[course_id] if s.student_id == student_id]
        if len(matches) != 1 or matches[0].course_id != course_id:
            raise EnrollmentError(
                f"{course_id}/{student_id}: missing or conflicting student identity"
            )
        key = username(matches[0].lms_username)
        if not key or sum(username(s.lms_username) == key for s in self._students[course_id]) != 1:
            raise EnrollmentError(f"{course_id}/{student_id}: missing or conflicting LMS username")
        enrolled = self._enrollments[course_id].get(key)
        if enrolled is None:
            raise EnrollmentError(
                f"{course_id}/{student_id}/{assignment_id}: no verified enrollment rubric in "
                f"tables/{course_id}/{FILENAME}; grading is blocked"
            )
        if enrolled.rubric not in eligible:
            raise EnrollmentError(
                f"{course_id}/{student_id}/{assignment_id}: enrollment rubric "
                f"{enrolled.rubric!r} is not eligible ({', '.join(eligible)})"
            )
        if explicit and requested != enrolled.rubric:
            raise EnrollmentError(
                f"{course_id}/{student_id}/{assignment_id}: --rubric {requested!r} conflicts "
                f"with verified enrollment rubric {enrolled.rubric!r}"
            )
        return Selection(enrolled.rubric, True)
