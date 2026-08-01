from __future__ import annotations

from pathlib import Path

import pytest

from agentic_assessment_toolkit import cli
from agentic_assessment_toolkit.check_course import check_course, format_report
from agentic_assessment_toolkit.data_root import DataRootError
from tests.conftest import COURSE_ID

COURSE_TOML = "courses/" + COURSE_ID + "/course.toml"


def course_dir(data_root: Path) -> Path:
    return data_root / "courses" / COURSE_ID


def violations(data_root: Path) -> list[str]:
    return check_course(data_root, COURSE_ID).violations


def test_fixture_course_has_no_violations(data_root: Path) -> None:
    report = check_course(data_root, COURSE_ID)
    assert report.ok
    assert report.violations == []
    # The fixture's known gaps: no institution, no HW2 rubric, no syllabus.
    assert any("institution" in gap for gap in report.gaps)
    assert any("rubrics/HW2/default.md" in gap for gap in report.gaps)
    assert any("syllabus/" in gap for gap in report.gaps)
    assert any("2 of 3" in line for line in report.coverage)
    assert any("80% of the final grade" in line for line in report.coverage)


def test_unknown_course_is_an_error(data_root: Path) -> None:
    with pytest.raises(DataRootError, match="not found"):
        check_course(data_root, "NOPE")


def test_missing_course_toml_is_a_gap_not_a_violation(data_root: Path) -> None:
    (course_dir(data_root) / "course.toml").unlink()
    # Without a course default, assignments resolve no environment except
    # HW2, whose sidecar names one.
    report = check_course(data_root, COURSE_ID)
    assert any("course.toml is missing" in gap for gap in report.gaps)
    assert any("HW1 resolves no environment" in v for v in report.violations)
    assert not any("HW2" in v for v in report.violations)
    assert any("no registry" in line for line in report.coverage)


def test_empty_assignment_directory_is_a_violation(data_root: Path) -> None:
    empty = course_dir(data_root) / "assignments" / "HW9"
    empty.mkdir()
    report = check_course(data_root, COURSE_ID)
    assert any("assignments/HW9 is empty" in v for v in report.violations)
    assert any("no [[assessments]] registry entry" in v for v in report.violations)


def test_excluded_with_materials_is_a_violation(data_root: Path) -> None:
    exam = course_dir(data_root) / "assignments" / "EXAM1"
    exam.mkdir()
    (exam / "exam.md").write_text("questions\n", encoding="utf-8")
    assert any("marked excluded" in v for v in violations(data_root))


def test_registry_entry_without_materials_or_reason_is_a_gap(data_root: Path) -> None:
    toml_path = course_dir(data_root) / "course.toml"
    text = toml_path.read_text(encoding="utf-8")
    toml_path.write_text(
        text + '\n[[assessments]]\nid = "HW3"\ntype = "homework"\nweight_pct = 0\n',
        encoding="utf-8",
    )
    report = check_course(data_root, COURSE_ID)
    assert any("HW3" in gap and "no 'excluded' reason" in gap for gap in report.gaps)
    # 100 + 0 still sums to 100.
    assert report.ok


def test_weight_sum_off_100_is_a_violation(data_root: Path) -> None:
    toml_path = course_dir(data_root) / "course.toml"
    text = toml_path.read_text(encoding="utf-8").replace("weight_pct = 20.0", "weight_pct = 30.0")
    toml_path.write_text(text, encoding="utf-8")
    assert any("sum to 110" in v for v in violations(data_root))


def test_partial_weights_skip_the_sum_and_report_a_gap(data_root: Path) -> None:
    toml_path = course_dir(data_root) / "course.toml"
    text = toml_path.read_text(encoding="utf-8").replace("weight_pct = 20.0\n", "")
    toml_path.write_text(text, encoding="utf-8")
    report = check_course(data_root, COURSE_ID)
    assert report.ok
    assert any("without 'weight_pct': EXAM1" in gap for gap in report.gaps)


def test_unparseable_rubric_is_a_violation(data_root: Path) -> None:
    rubric = course_dir(data_root) / "rubrics" / "HW1" / "default.md"
    rubric.write_text("- `broken (1 point): no closing backtick.\n", encoding="utf-8")
    assert any("line 1" in v for v in violations(data_root))


def test_orphan_rubric_and_reference_are_violations(data_root: Path) -> None:
    orphan_rubric = course_dir(data_root) / "rubrics" / "GHOST"
    orphan_rubric.mkdir()
    (orphan_rubric / "default.md").write_text("- `a` (1 point): x.\n", encoding="utf-8")
    orphan_reference = course_dir(data_root) / "reference_solutions" / "GHOST2"
    orphan_reference.mkdir()
    (orphan_reference / "solution.md").write_text("x\n", encoding="utf-8")
    found = violations(data_root)
    assert any("rubrics/GHOST matches no" in v for v in found)
    assert any("reference_solutions/GHOST2 matches no" in v for v in found)


def test_grading_flavor_for_an_assignment_is_a_violation(data_root: Path) -> None:
    sidecar = course_dir(data_root) / "assignments" / "HW1.toml"
    sidecar.write_text('environment = "grading"\n', encoding="utf-8")
    assert any("reserved for grading" in v for v in violations(data_root))


def test_unknown_flavor_is_a_violation(data_root: Path) -> None:
    sidecar = course_dir(data_root) / "assignments" / "HW1.toml"
    sidecar.write_text('environment = "quantum"\n', encoding="utf-8")
    assert any("unknown environment flavor" in v for v in violations(data_root))


def test_invalid_course_toml_is_a_violation(data_root: Path) -> None:
    (course_dir(data_root) / "course.toml").write_text("not toml [", encoding="utf-8")
    assert any("not valid TOML" in v for v in violations(data_root))


def test_missing_reference_solution_is_a_gap(data_root: Path) -> None:
    reference = course_dir(data_root) / "reference_solutions" / "HW2"
    (reference / "solution.md").unlink()
    reference.rmdir()
    report = check_course(data_root, COURSE_ID)
    assert report.ok
    assert any("HW2 has no reference solution" in gap for gap in report.gaps)


def test_unexpected_entries_are_a_gap(data_root: Path) -> None:
    (course_dir(data_root) / "scratch.txt").write_text("tmp\n", encoding="utf-8")
    report = check_course(data_root, COURSE_ID)
    assert any("scratch.txt" in gap for gap in report.gaps)


def test_syllabus_and_notes_are_read(data_root: Path) -> None:
    syllabus = course_dir(data_root) / "syllabus"
    syllabus.mkdir()
    (syllabus / "syllabus.md").write_text("# Syllabus\n", encoding="utf-8")
    (course_dir(data_root) / "intake-notes.md").write_text(
        "## Open\n- weight of HW3 not stated\n", encoding="utf-8"
    )
    report = check_course(data_root, COURSE_ID)
    assert not any("syllabus/" in gap for gap in report.gaps)
    assert report.notes is not None and "HW3" in report.notes


def test_format_report_sections(data_root: Path) -> None:
    text = format_report(check_course(data_root, COURSE_ID))
    assert text.startswith(f"course {COURSE_ID}")
    assert "contract violations: none" in text
    assert "completeness gaps (" in text
    assert "intake notes: none" in text
    assert "coverage:" in text


def test_cli_exit_codes(data_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["check-course", "--course", COURSE_ID, "--data-root", str(data_root)]
    assert cli.main(args) == 0
    assert "contract violations: none" in capsys.readouterr().out

    (course_dir(data_root) / "assignments" / "HW9").mkdir()
    assert cli.main(args) == 1
    assert "assignments/HW9 is empty" in capsys.readouterr().out

    assert cli.main(["check-course", "--course", "NOPE", "--data-root", str(data_root)]) == 2
    assert "not found" in capsys.readouterr().err
