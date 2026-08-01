from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentic_assessment_toolkit.materialize._common import MaterializeError, slugify, task_dir_name
from agentic_assessment_toolkit.materialize.grading import materialize_grading_task
from agentic_assessment_toolkit.materialize.solve import materialize_solve_task
from tests.conftest import COURSE_ID, FIXTURES_DIR, GOLDEN_DIR, assert_trees_equal

COURSE_DIR = FIXTURES_DIR / "course" / COURSE_ID


def test_slugify() -> None:
    assert slugify("HW 12") == "HW-12"
    assert slugify("PU_CHE597CO_S2026") == "PU_CHE597CO_S2026"
    assert slugify("///") == "x"


def test_task_dir_name_disambiguates_slug_collisions() -> None:
    a = task_dir_name(["C1", "HW 1"], "C1/HW 1")
    b = task_dir_name(["C1", "HW-1"], "C1/HW-1")
    assert a != b


def test_solve_task_matches_golden(tmp_path: Path) -> None:
    materialize_solve_task(
        assignment_dir=COURSE_DIR / "assignments" / "HW1",
        course_id=COURSE_ID,
        assignment_id="HW1",
        environment_flavor="scientific-python",
        prompt_name="solver",
        tasks_dir=tmp_path,
    )
    assert_trees_equal(tmp_path, GOLDEN_DIR / "solve_tasks")


def test_grading_task_matches_golden(tmp_path: Path) -> None:
    materialize_grading_task(
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
        rubric_path=COURSE_DIR / "rubrics" / "HW1" / "default.md",
        item_id=f"{COURSE_ID}/stu1/HW1",
        name_parts=(COURSE_ID, "stu1", "HW1"),
        prompt_name="grader",
        tasks_dir=tmp_path,
    )
    assert_trees_equal(tmp_path, GOLDEN_DIR / "grading_tasks")


def test_solve_task_structure(tmp_path: Path) -> None:
    task = materialize_solve_task(
        assignment_dir=COURSE_DIR / "assignments" / "HW1",
        course_id=COURSE_ID,
        assignment_id="HW1",
        environment_flavor="scientific-python",
        prompt_name="solver",
        tasks_dir=tmp_path,
    )
    task_dir = task.task_dir
    assert task.item_id == f"{COURSE_ID}/HW1"
    assert (task_dir / "instruction.md").read_bytes()
    task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
    assert 'artifacts = ["/app/submission"]' in task_toml
    assert 'network_mode = "public"' in task_toml
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.endswith("COPY assignment /app/assignment\n")
    assert (task_dir / "environment" / "assignment" / "statement.md").is_file()
    assert (task_dir / "tests" / "solve_verifier.py").is_file()
    assert (task_dir / "tests" / "input_manifest.json").is_file()
    test_sh = task_dir / "tests" / "test.sh"
    assert os.access(test_sh, os.X_OK)
    assert set(task.input_hashes) == {"assignment", "prompt", "environment", "verifier"}


def test_grading_task_without_rubric(tmp_path: Path) -> None:
    task = materialize_grading_task(
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW2",
        rubric_path=None,
        item_id=f"{COURSE_ID}/stu1/HW2",
        name_parts=(COURSE_ID, "stu1", "HW2"),
        prompt_name="grader",
        tasks_dir=tmp_path,
    )
    task_dir = task.task_dir
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY rubric.md" not in dockerfile
    assert not (task_dir / "environment" / "rubric.md").exists()
    assert "rubric" not in task.input_hashes
    assert (task_dir / "tests" / "grading_schema.py").read_bytes()
    assert os.access(task_dir / "tests" / "test.sh", os.X_OK)
    task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
    assert 'artifacts = ["/app/grading_output"]' in task_toml


def test_grading_schema_copy_is_verbatim(tmp_path: Path) -> None:
    from agentic_assessment_toolkit.config import grading_schema_source_path

    task = materialize_grading_task(
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
        rubric_path=None,
        item_id="x",
        name_parts=("x",),
        prompt_name="grader",
        tasks_dir=tmp_path,
    )
    copied = (task.task_dir / "tests" / "grading_schema.py").read_bytes()
    assert copied == grading_schema_source_path().read_bytes()


def test_existing_task_dir_is_an_error(tmp_path: Path) -> None:
    def materialize() -> None:
        materialize_solve_task(
            assignment_dir=COURSE_DIR / "assignments" / "HW1",
            course_id=COURSE_ID,
            assignment_id="HW1",
            environment_flavor="scientific-python",
            prompt_name="solver",
            tasks_dir=tmp_path,
        )

    materialize()
    with pytest.raises(MaterializeError, match="already exists"):
        materialize()


def test_missing_submission_dir_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MaterializeError, match="not a directory"):
        materialize_grading_task(
            submission_dir=tmp_path / "missing",
            reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
            rubric_path=None,
            item_id="x",
            name_parts=("x",),
            prompt_name="grader",
            tasks_dir=tmp_path,
        )
