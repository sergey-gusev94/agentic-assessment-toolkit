from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from agentic_assessment_toolkit.course import CourseError, load_course
from tests.conftest import COURSE_ID, FIXTURES_DIR


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "course.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_fixture_course_loads() -> None:
    course = load_course(FIXTURES_DIR / "course" / COURSE_ID / "course.toml")
    assert course.title == "Synthetic Course One"
    assert course.term == "F2025"
    assert course.institution is None
    assert course.environment == "scientific-python"
    assert course.assessments is not None
    assert [entry.id for entry in course.assessments] == ["HW1", "HW2", "EXAM1"]
    exam = course.assessment("EXAM1")
    assert exam is not None
    assert exam.excluded is not None
    assert exam.ai_use_possible is False
    assert course.assessment("HW9") is None


def test_minimal_course_record(tmp_path: Path) -> None:
    course = load_course(write(tmp_path, '[course]\nenvironment = "data-science"\n'))
    assert course.environment == "data-science"
    assert course.title is None
    assert course.assessments is None  # registry unauthored, not empty


def test_empty_file_loads_as_all_absent(tmp_path: Path) -> None:
    course = load_course(write(tmp_path, ""))
    assert course.environment is None
    assert course.assessments is None


def test_registry_fields_parse(tmp_path: Path) -> None:
    course = load_course(
        write(
            tmp_path,
            "[[assessments]]\n"
            'id = "HW1"\n'
            'title = "HW 1"\n'
            'type = "homework"\n'
            'scope = "take_home"\n'
            "weight_pct = 12.5\n"
            'category = "homework"\n'
            'ai_policy = "allowed"\n'
            "ai_use_possible = true\n"
            "due = 2026-02-06\n",
        )
    )
    assert course.assessments is not None
    entry = course.assessments[0]
    assert entry.weight_pct == 12.5
    assert entry.due == datetime.date(2026, 2, 6)
    assert entry.excluded is None


def test_top_level_environment_is_rejected(tmp_path: Path) -> None:
    """The pre-registry flat format is gone; facts live under [course]."""
    with pytest.raises(CourseError, match="unknown top-level keys: environment"):
        load_course(write(tmp_path, 'environment = "data-science"\n'))


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("[[assessments]]\ntitle = 'x'\n", "needs a non-empty string 'id'"),
        ("[[assessments]]\nid = 'HW 1'\n", "no whitespace"),
        ("[[assessments]]\nid = 'a/b'\n", "no whitespace or '/'"),
        ("[[assessments]]\nid = 'HW1'\ntype = 'quiz'\n", "'type' must be one of"),
        ("[[assessments]]\nid = 'HW1'\nai_policy = 'maybe'\n", "'ai_policy' must be one of"),
        ("[[assessments]]\nid = 'HW1'\nweight_pct = -1\n", "finite and >= 0"),
        ("[[assessments]]\nid = 'HW1'\nweight_pct = true\n", "must be a number"),
        ("[[assessments]]\nid = 'HW1'\nai_use_possible = 'yes'\n", "must be a boolean"),
        ("[[assessments]]\nid = 'HW1'\ndue = '2026-02-06'\n", "must be a TOML date"),
        ("[[assessments]]\nid = 'HW1'\nexcluded = ''\n", "non-empty string"),
        ("[[assessments]]\nid = 'HW1'\npoints = 3\n", "unknown keys: points"),
        ("[[assessments]]\nid = 'HW1'\n[[assessments]]\nid = 'HW1'\n", "duplicate assessment id"),
        ("[course]\nsemester = 'F25'\n", r"\[course\] has unknown keys: semester"),
        ("[course]\ntitle = ''\n", "non-empty string"),
        ("weight = 1\n", "unknown top-level keys"),
        ("not toml [", "not valid TOML"),
    ],
)
def test_invalid_course_toml(tmp_path: Path, body: str, match: str) -> None:
    with pytest.raises(CourseError, match=match):
        load_course(write(tmp_path, body))


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CourseError, match="cannot read"):
        load_course(tmp_path / "absent.toml")
