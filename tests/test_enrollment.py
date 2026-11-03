from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from agentic_assessment_toolkit import check_course, cli, enrollment, results
from agentic_assessment_toolkit.course import CourseError, load_course
from agentic_assessment_toolkit.hashing import sha256_file
from agentic_assessment_toolkit.ingest import STUDENTS_COLUMNS
from tests.conftest import COURSE_ID, build_data_root
from tests.test_cli import grade_one_initial, job_dirs
from tests.test_config import GRADE_TOML, JUDGE_TOML, write_config


def configure(root: Path) -> Path:
    course = root / "courses" / COURSE_ID / "course.toml"
    course.write_text(
        course.read_text().replace(
            'id = "HW1"', 'id = "HW1"\nrubrics = ["undergraduate", "graduate"]'
        )
    )
    rubric = course.parent / "rubrics" / "HW1"
    for name in ("undergraduate", "graduate"):
        # Identical bytes must still have distinct selection identities.
        shutil.copyfile(rubric / "default.md", rubric / f"{name}.md")
    table = root / "tables" / COURSE_ID
    table.mkdir(parents=True, exist_ok=True)
    with (table / "students.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(STUDENTS_COLUMNS)
        writer.writerow(["stu1", COURSE_ID, "1", "alice", "Alice", "brightspace"])
        writer.writerow(["stu2", COURSE_ID, "2", "bob", "Bob", "brightspace"])
    mapping = table / enrollment.FILENAME
    mapping.write_text(
        "lms_username,rubric,source\nalice,undergraduate,Reviewed roster\nbob,graduate,Reviewed roster\n"
    )
    return mapping


@pytest.fixture
def routed(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = build_data_root(tmp_path)
    mapping = configure(root)
    shutil.copytree(
        root / "submissions" / COURSE_ID / "stu1", root / "submissions" / COURSE_ID / "stu2"
    )
    config = write_config(tmp_path, GRADE_TOML, "initial")
    return root, mapping, config


def grade(root: Path, config: Path, *extra: str) -> int:
    return cli.main(
        [
            "grade",
            "--data-root",
            str(root),
            "--config",
            str(config),
            "--course",
            COURSE_ID,
            "--assignment",
            "HW1",
            *extra,
        ]
    )


def test_mixed_materialization_records_selected_rubric_without_identity_leak(
    routed: tuple[Path, Path, Path],
) -> None:
    root, _, config = routed
    assert grade(root, config, "--materialize-only") == 0
    job = job_dirs(root, "grading")[-1]
    record = json.loads((job / "aat-run.json").read_text())
    assert record["config"]["name"] == "initial"
    items = {item["student_id"]: item for item in record["items"]}
    assert items["stu1"]["rubric_name"] == "undergraduate"
    assert items["stu2"]["rubric_name"] == "graduate"
    assert items["stu1"]["item_identity"] != items["stu2"]["item_identity"]
    for item in items.values():
        task = root / "tasks" / job.name / item["task_dir_name"]
        rubric = task / "environment" / "rubric.md"
        assert sha256_file(rubric) == item["input_hashes"]["rubric"]
        assert "Reviewed roster" not in (task / "instruction.md").read_text()
        assert not (task / enrollment.FILENAME).exists()


def test_unmapped_selection_blocks_before_materialization_and_override_cannot_bypass(
    routed: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root, mapping, config = routed
    mapping.write_text("lms_username,rubric,source\nbob,graduate,Reviewed roster\n")
    assert grade(root, config, "--rubric", "undergraduate", "--materialize-only") == 2
    assert "2 selected submission(s)" in capsys.readouterr().err
    assert job_dirs(root, "grading") == []
    mapping.write_text(
        "lms_username,rubric,source\nalice,undergraduate,Reviewed roster\nbob,graduate,Reviewed roster\n"
    )
    assert grade(root, config, "--rubric", "undergraduate", "--dry-run") == 2
    assert "conflicts with verified enrollment" in capsys.readouterr().err


def test_mapping_change_invalidates_only_changed_student(routed: tuple[Path, Path, Path]) -> None:
    root, mapping, config = routed
    assert grade(root, config, "--materialize-only") == 0
    first = json.loads((job_dirs(root, "grading")[-1] / "aat-run.json").read_text())
    mapping.write_text(
        "lms_username,rubric,source\nalice,graduate,Corrected roster\nbob,graduate,Another citation\n"
    )
    assert grade(root, config, "--materialize-only") == 0
    second = json.loads((job_dirs(root, "grading")[-1] / "aat-run.json").read_text())
    before = {i["student_id"]: i["item_identity"] for i in first["items"]}
    after = {i["student_id"]: i["item_identity"] for i in second["items"]}
    assert before["stu1"] != after["stu1"]
    assert before["stu2"] == after["stu2"]


def test_judge_uses_selected_pool_and_rejects_old_scheme(
    routed: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, mapping, initial = routed
    grade_one_initial(root, initial, "initial__t1")
    judge = write_config(
        tmp_path, JUDGE_TOML.replace('agent = "codex"', 'agent = "claude-code"'), "judge"
    )
    submission = root / "submissions" / COURSE_ID / "stu1" / "HW1"
    args = [
        "grade",
        "--data-root",
        str(root),
        "--config",
        str(judge),
        "--submissions",
        str(submission),
        "--context-from",
        str(initial),
        "--gradings",
        "1",
        "--materialize-only",
    ]
    assert cli.main(args) == 0
    record = json.loads((job_dirs(root, "grading")[-1] / "aat-run.json").read_text())
    assert record["items"][0]["rubric_name"] == "undergraduate"
    assert record["items"][0]["prior_trials"][0]["trial_name"] == "initial__t1"
    # Results must use per-item names rather than the config's default.
    frame = results.load_results(root).trials
    assert frame.iloc[0]["rubric_name"] == "undergraduate"
    mapping.write_text(
        "lms_username,rubric,source\nalice,graduate,Corrected roster\nbob,graduate,Reviewed roster\n"
    )
    count = len(job_dirs(root, "grading"))
    assert cli.main(args) == 1
    assert len(job_dirs(root, "grading")) == count
    assert "0 of 1 required prior" in capsys.readouterr().out


@pytest.mark.parametrize(
    "row",
    [
        "alice,graduate,",
        "#Alice,graduate,Duplicate\nalice,graduate,Duplicate",
        "alice,graduate,Reviewed,extra",
    ],
)
def test_enrollment_table_rejects_unverified_or_ambiguous_rows(tmp_path: Path, row: str) -> None:
    path = tmp_path / "tables" / COURSE_ID
    path.mkdir(parents=True)
    (path / enrollment.FILENAME).write_text("lms_username,rubric,source\n" + row + "\n")
    with pytest.raises(enrollment.EnrollmentError):
        enrollment.read_enrollments(tmp_path, COURSE_ID)


def test_unenrolled_reference_needs_explicit_eligible_rubric(
    routed: tuple[Path, Path, Path],
) -> None:
    root, _, _ = routed
    resolver = enrollment.Resolver(root)
    with pytest.raises(enrollment.EnrollmentError, match="choose an eligible"):
        resolver.select(COURSE_ID, "HW1", "_reference", "default")
    assert resolver.select(COURSE_ID, "HW1", None, "graduate", explicit=True).name == "graduate"


def test_checker_reports_missing_mapping_and_missing_declared_rubric(
    routed: tuple[Path, Path, Path],
) -> None:
    root, mapping, _ = routed
    mapping.write_text("lms_username,rubric,source\n")
    (root / "courses" / COURSE_ID / "rubrics" / "HW1" / "graduate.md").unlink()
    report = check_course.check_course(root, COURSE_ID)
    assert any("2 submission(s) lack" in gap for gap in report.gaps)
    assert any("missing declared rubric 'graduate'" in error for error in report.violations)


@pytest.mark.parametrize(
    "value",
    [
        '["graduate"]',
        '["graduate", "graduate"]',
        '["../graduate", "undergraduate"]',
        '[1, "graduate"]',
    ],
)
def test_course_rejects_invalid_routing_names(routed: tuple[Path, Path, Path], value: str) -> None:
    root, _, _ = routed
    course = root / "courses" / COURSE_ID / "course.toml"
    course.write_text(course.read_text().replace('["undergraduate", "graduate"]', value))
    with pytest.raises(CourseError, match="rubrics"):
        load_course(course)


def test_routed_judge_compares_effective_rubrics_not_config_fallbacks(
    routed: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    root, _, _ = routed
    initial = write_config(
        tmp_path, GRADE_TOML + 'rubric = "graduate"\n', "initial-graduate-fallback"
    )
    grade_one_initial(root, initial, "undergraduate__initial")
    judge = write_config(
        tmp_path,
        JUDGE_TOML.replace('agent = "codex"', 'agent = "claude-code"'),
        "judge-default-fallback",
    )
    submission = root / "submissions" / COURSE_ID / "stu1" / "HW1"
    assert (
        cli.main(
            [
                "grade",
                "--data-root",
                str(root),
                "--config",
                str(judge),
                "--submissions",
                str(submission),
                "--context-from",
                str(initial),
                "--gradings",
                "1",
                "--materialize-only",
            ]
        )
        == 0
    )
    record = json.loads((job_dirs(root, "grading")[-1] / "aat-run.json").read_text())
    item = record["items"][0]
    assert item["rubric_name"] == "undergraduate"
    assert item["prior_trials"][0]["trial_name"] == "undergraduate__initial"
