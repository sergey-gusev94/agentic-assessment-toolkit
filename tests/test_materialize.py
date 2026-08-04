from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from agentic_assessment_toolkit.base_images import base_image_reference
from agentic_assessment_toolkit.config import environment_path
from agentic_assessment_toolkit.materialize._common import MaterializeError, slugify, task_dir_name
from agentic_assessment_toolkit.materialize.grading import materialize_grading_task
from agentic_assessment_toolkit.materialize.solve import materialize_solve_task
from tests.conftest import COURSE_ID, FIXTURES_DIR, GOLDEN_DIR, assert_trees_equal

COURSE_DIR = FIXTURES_DIR / "course" / COURSE_ID


def test_slugify() -> None:
    assert slugify("HW 12") == "HW-12"
    assert slugify("PU_CHE597CO_S2026") == "PU-CHE597CO-S2026"
    assert slugify("_irrelevant") == "irrelevant"
    assert slugify("///") == "x"


def test_task_dir_name_disambiguates_slug_collisions() -> None:
    a = task_dir_name(["C1", "HW 1"], "C1/HW 1")
    b = task_dir_name(["C1", "HW-1"], "C1/HW-1")
    assert a != b
    # _reference and reference collapse to the same slug; only the item
    # id hash tells them apart.
    c = task_dir_name(["C1", "_reference", "HW1"], "C1/_reference/HW1")
    d = task_dir_name(["C1", "reference", "HW1"], "C1/reference/HW1")
    assert c != d


# One path component of an OCI image reference: alphanumeric runs
# separated by ".", "_", "__", or a run of "-". Harbor lowercases the
# task name and prefixes it to form the image name, so every generated
# name must fit this grammar or the Docker build fails before the agent
# starts.
_DOCKER_IMAGE_NAME = re.compile(r"[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*")


@pytest.mark.parametrize(
    "parts",
    [
        ["PU_CHE597DS_S2026", "_reference", "HW2"],
        ["PU_CHE597DS_S2026", "_irrelevant", "HW2"],
        ["SYN_C1", "S001", "HW1"],
        ["SYN_C1", "student-17", "HW1"],
        ["C1", "a b.c__d", "HW 1"],
        ["C1", "stu___1", "HW.1."],
        ["C1", "größe strauß", "HW1"],
        ["_", ".", "-"],
    ],
)
def test_task_dir_name_is_docker_image_safe(parts: list[str]) -> None:
    name = task_dir_name(parts, "/".join(parts))
    image = f"hb__{name}".lower()
    assert _DOCKER_IMAGE_NAME.fullmatch(image), image


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
        assignment_dir=COURSE_DIR / "assignments" / "HW1",
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
    # The Dockerfile builds FROM the flavor's shared base image, and the
    # task carries the base recipe verbatim (docs/design.md).
    assert f"FROM {base_image_reference('scientific-python')}\n" in dockerfile
    base = (task_dir / "environment" / "base.Dockerfile").read_bytes()
    assert base == environment_path("scientific-python").read_bytes()
    assert (task_dir / "environment" / "assignment" / "statement.md").is_file()
    assert (task_dir / "tests" / "solve_verifier.py").is_file()
    assert (task_dir / "tests" / "input_manifest.json").is_file()
    test_sh = task_dir / "tests" / "test.sh"
    assert os.access(test_sh, os.X_OK)
    assert set(task.input_hashes) == {"assignment", "prompt", "environment", "verifier"}


def test_grading_task_structure(tmp_path: Path) -> None:
    task = materialize_grading_task(
        assignment_dir=COURSE_DIR / "assignments" / "HW1",
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
        rubric_path=COURSE_DIR / "rubrics" / "HW1" / "default.md",
        item_id=f"{COURSE_ID}/stu1/HW1",
        name_parts=(COURSE_ID, "stu1", "HW1"),
        prompt_name="grader",
        tasks_dir=tmp_path,
    )
    task_dir = task.task_dir
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY assignment /app/assignment" in dockerfile
    assert "COPY rubric.md /app/rubric.md" in dockerfile
    assert f"FROM {base_image_reference('grading')}\n" in dockerfile
    base = (task_dir / "environment" / "base.Dockerfile").read_bytes()
    assert base == environment_path("grading").read_bytes()
    assert (task_dir / "environment" / "assignment" / "statement.md").is_file()
    assert (task_dir / "environment" / "rubric.md").is_file()
    assert set(task.input_hashes) == {
        "assignment",
        "submission",
        "reference_solution",
        "rubric",
        "prompt",
        "environment",
        "verifier",
        "grading_schema",
    }
    assert (task_dir / "tests" / "grading_schema.py").read_bytes()
    assert os.access(task_dir / "tests" / "test.sh", os.X_OK)
    task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
    assert 'artifacts = ["/app/grading_output"]' in task_toml


def test_grading_task_missing_rubric_is_an_error(tmp_path: Path) -> None:
    """HW2 has no rubric; grading never starts without one (decision 5)."""
    with pytest.raises(MaterializeError, match="cannot read rubric"):
        materialize_grading_task(
            assignment_dir=COURSE_DIR / "assignments" / "HW2",
            submission_dir=FIXTURES_DIR / "submission",
            reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW2",
            rubric_path=COURSE_DIR / "rubrics" / "HW2" / "default.md",
            item_id=f"{COURSE_ID}/stu1/HW2",
            name_parts=(COURSE_ID, "stu1", "HW2"),
            prompt_name="grader",
            tasks_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_grading_task_unparseable_rubric_is_an_error(tmp_path: Path) -> None:
    rubric = tmp_path / "bad-rubric.md"
    rubric.write_text("- `a` (no points): malformed.\n", encoding="utf-8")
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    with pytest.raises(MaterializeError, match="line 1"):
        materialize_grading_task(
            assignment_dir=COURSE_DIR / "assignments" / "HW1",
            submission_dir=FIXTURES_DIR / "submission",
            reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
            rubric_path=rubric,
            item_id="x",
            name_parts=("x",),
            prompt_name="grader",
            tasks_dir=tasks_dir,
        )
    assert list(tasks_dir.iterdir()) == []


def test_grading_schema_copy_is_verbatim(tmp_path: Path) -> None:
    from agentic_assessment_toolkit.config import grading_schema_source_path

    task = materialize_grading_task(
        assignment_dir=COURSE_DIR / "assignments" / "HW1",
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
        rubric_path=COURSE_DIR / "rubrics" / "HW1" / "default.md",
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
            assignment_dir=COURSE_DIR / "assignments" / "HW1",
            submission_dir=tmp_path / "missing",
            reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
            rubric_path=COURSE_DIR / "rubrics" / "HW1" / "default.md",
            item_id="x",
            name_parts=("x",),
            prompt_name="grader",
            tasks_dir=tmp_path,
        )


def test_grading_task_with_rubric_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "rubric.pdf").write_text("professor rubric\n", encoding="utf-8")
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    task = materialize_grading_task(
        assignment_dir=COURSE_DIR / "assignments" / "HW1",
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
        rubric_path=COURSE_DIR / "rubrics" / "HW1" / "default.md",
        rubric_source_dir=source,
        item_id="x",
        name_parts=("x",),
        prompt_name="grader",
        tasks_dir=tasks_dir,
    )
    dockerfile = (task.task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY rubric_source /app/rubric_source" in dockerfile
    assert (task.task_dir / "environment" / "rubric_source" / "rubric.pdf").is_file()
    assert "rubric_source" in task.input_hashes


def test_grading_task_without_rubric_source(tmp_path: Path) -> None:
    task = materialize_grading_task(
        assignment_dir=COURSE_DIR / "assignments" / "HW1",
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=COURSE_DIR / "reference_solutions" / "HW1",
        rubric_path=COURSE_DIR / "rubrics" / "HW1" / "default.md",
        item_id="x",
        name_parts=("x",),
        prompt_name="grader",
        tasks_dir=tmp_path,
    )
    dockerfile = (task.task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert "rubric_source" not in dockerfile
    assert not (task.task_dir / "environment" / "rubric_source").exists()
    assert "rubric_source" not in task.input_hashes
