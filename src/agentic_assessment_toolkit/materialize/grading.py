"""Materialize a submission into a Harbor grading task.

One code path for both submission sources — a Harbor solve artifact or a
real student folder (design decision 5). The task presents the
assignment handout, submission, reference solution, and rubric under
/app as data; the grader writes into /app/grading_output; the generic
grading verifier validates the output schema and derives score_pct as
the reward. Grading never starts without a rubric (decision 5): a
missing or unparseable rubric fails materialization before anything is
written.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..hashing import sha256_dir, sha256_file
from ..rubric import RubricError, parse_rubric_file
from . import _common

GRADING_OUTPUT_PATH = config.STAGE_TASK_SETTINGS["grade"][0]


@dataclass(frozen=True)
class MaterializedGradingTask:
    item_id: str
    task_dir: Path
    task_dir_name: str
    input_hashes: dict[str, str]


def materialize_grading_task(
    *,
    assignment_dir: Path,
    submission_dir: Path,
    reference_solution_dir: Path,
    rubric_path: Path,
    item_id: str,
    name_parts: Sequence[str],
    prompt_name: str,
    tasks_dir: Path,
) -> MaterializedGradingTask:
    try:
        parse_rubric_file(rubric_path)
    except RubricError as error:
        raise _common.MaterializeError(str(error)) from error

    name = _common.task_dir_name(list(name_parts), item_id)
    task_dir = _common.create_task_dir(tasks_dir, name)

    prompt_path = config.prompt_path(prompt_name)
    (task_dir / "instruction.md").write_bytes(prompt_path.read_bytes())

    (task_dir / "task.toml").write_text(config.rendered_task_toml("grade"), encoding="utf-8")

    environment_template = config.environment_path(config.GRADING_FLAVOR)
    copy_lines = [
        "COPY assignment /app/assignment",
        "COPY submission /app/submission",
        "COPY reference_solution /app/reference_solution",
        "COPY rubric.md /app/rubric.md",
    ]
    _common.write_dockerfile(task_dir, environment_template.read_bytes(), copy_lines)

    environment_dir = task_dir / "environment"
    _common.copy_tree(assignment_dir, environment_dir / "assignment")
    _common.copy_tree(submission_dir, environment_dir / "submission")
    _common.copy_tree(reference_solution_dir, environment_dir / "reference_solution")
    (environment_dir / "rubric.md").write_bytes(rubric_path.read_bytes())

    _common.write_test_runner(task_dir, "grading_verifier.py")
    verifier_path = config.verifier_path("grade")
    schema_path = config.grading_schema_source_path()
    (task_dir / "tests" / "grading_verifier.py").write_bytes(verifier_path.read_bytes())
    (task_dir / "tests" / "grading_schema.py").write_bytes(schema_path.read_bytes())

    input_hashes = {
        "assignment": sha256_dir(assignment_dir),
        "submission": sha256_dir(submission_dir),
        "reference_solution": sha256_dir(reference_solution_dir),
        "rubric": sha256_file(rubric_path),
        "prompt": sha256_file(prompt_path),
        "environment": sha256_file(environment_template),
        "verifier": sha256_file(verifier_path),
        "grading_schema": sha256_file(schema_path),
    }

    return MaterializedGradingTask(
        item_id=item_id,
        task_dir=task_dir,
        task_dir_name=name,
        input_hashes=input_hashes,
    )
