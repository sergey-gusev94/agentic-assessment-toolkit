"""Materialize a submission into a Harbor grading task.

One code path for both submission sources — a Harbor solve artifact or a
real student folder (design decision 5) — and for both grader roles:
an initial grading task, or a final-judge task that additionally
presents prior gradings of the same submission as numbered rounds and
declares the student-facing feedback document as a third required
deliverable. The task presents the
assignment handout, submission, reference solution, and rubric under
/app as data; the grader writes into /app/grading_output; the generic
grading verifier validates the output schema, cross-checks the
grader's criteria against the expected-criteria file written here from
the parsed rubric, checks any declared extra deliverables, and derives
score_pct as the reward. Grading never
starts without a rubric (decision 5): a
missing or unparseable rubric fails materialization before anything is
written.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..grading_schema import (
    EXPECTED_CRITERIA_FILENAME,
    FEEDBACK_FILENAME,
    JUSTIFICATION_FILENAME,
    REQUIRED_FILES_FILENAME,
    RESULT_FILENAME,
)
from ..hashing import sha256_dir, sha256_file, sha256_parts
from ..rubric import RubricError, parse_rubric_file
from . import _common

GRADING_OUTPUT_PATH = config.STAGE_TASK_SETTINGS["grade"][0]


@dataclass(frozen=True)
class MaterializedGradingTask:
    item_id: str
    task_dir: Path
    task_dir_name: str
    input_hashes: dict[str, str]


def prior_gradings_hash(prior_gradings: Sequence[tuple[Path, Path]]) -> str:
    """The hash folded into a final-judge item's identity.

    Covers the exact bytes of every prior grading presented in the task,
    in presentation order — so adding, removing, or revising any prior
    grading changes the judge item's identity (docs/design.md,
    "Experiment configs and config identity").
    """
    parts = []
    for index, (result_path, justification_path) in enumerate(prior_gradings):
        parts.append((f"prior-result-{index}", result_path.read_bytes()))
        parts.append((f"prior-justification-{index}", justification_path.read_bytes()))
    return sha256_parts(parts)


def materialize_grading_task(
    *,
    assignment_dir: Path,
    submission_dir: Path,
    reference_solution_dir: Path,
    rubric_path: Path,
    rubric_source_dir: Path | None = None,
    item_id: str,
    name_parts: Sequence[str],
    agent: str,
    prompt_name: str,
    tasks_dir: Path,
    prior_gradings: Sequence[tuple[Path, Path]] | None = None,
) -> MaterializedGradingTask:
    """Write one grading task; see the module docstring.

    ``agent`` selects which of the grading flavor's per-agent templates
    the task builds on — each bakes in that agent's CLI.

    ``prior_gradings`` — (grading_result.json path, justification.md
    path) pairs in presentation order — makes this a final-judge task:
    the pairs are presented under ``/app/prior_gradings/`` as numbered
    rounds, and the required-files declaration makes the student-facing
    feedback document a third required deliverable. Round numbering is
    positional, so callers pass a deterministically ordered sequence;
    no job or trial name enters the task (byte-determinism).
    """
    try:
        criteria = parse_rubric_file(rubric_path)
    except RubricError as error:
        raise _common.MaterializeError(str(error)) from error

    name = _common.task_dir_name(list(name_parts), item_id)
    task_dir = _common.create_task_dir(tasks_dir, name)

    prompt_path = config.prompt_path(prompt_name)
    (task_dir / "instruction.md").write_bytes(prompt_path.read_bytes())

    (task_dir / "task.toml").write_text(config.rendered_task_toml("grade"), encoding="utf-8")

    environment_template = config.environment_path(config.GRADING_FLAVOR, agent)
    copy_lines = [
        "COPY assignment /app/assignment",
        "COPY submission /app/submission",
        "COPY reference_solution /app/reference_solution",
        "COPY rubric.md /app/rubric.md",
    ]
    if rubric_source_dir is not None:
        copy_lines.append("COPY rubric_source /app/rubric_source")
    if prior_gradings:
        copy_lines.append("COPY prior_gradings /app/prior_gradings")
    _common.write_dockerfile(task_dir, config.GRADING_FLAVOR, agent, copy_lines)

    environment_dir = task_dir / "environment"
    _common.copy_tree(assignment_dir, environment_dir / "assignment")
    _common.copy_tree(submission_dir, environment_dir / "submission")
    _common.copy_tree(reference_solution_dir, environment_dir / "reference_solution")
    (environment_dir / "rubric.md").write_bytes(rubric_path.read_bytes())
    if rubric_source_dir is not None:
        _common.copy_tree(rubric_source_dir, environment_dir / "rubric_source")
    if prior_gradings:
        for index, (result_path, justification_path) in enumerate(prior_gradings, start=1):
            round_dir = environment_dir / "prior_gradings" / f"{index:02d}"
            round_dir.mkdir(parents=True)
            (round_dir / RESULT_FILENAME).write_bytes(result_path.read_bytes())
            (round_dir / JUSTIFICATION_FILENAME).write_bytes(justification_path.read_bytes())

    _common.write_test_runner(task_dir, "grading_verifier.py")
    verifier_path = config.verifier_path("grade")
    schema_path = config.grading_schema_source_path()
    (task_dir / "tests" / "grading_verifier.py").write_bytes(verifier_path.read_bytes())
    (task_dir / "tests" / "grading_schema.py").write_bytes(schema_path.read_bytes())
    # The rubric's criteria, written beside the verifier so it can fail
    # the trial when the grader's authored criteria diverge from the
    # rubric (dropped, renamed, added, or reweighted criteria).
    expected_criteria = [
        {"id": criterion.id, "max_points": criterion.max_points, "bonus": criterion.bonus}
        for criterion in criteria
    ]
    (task_dir / "tests" / EXPECTED_CRITERIA_FILENAME).write_text(
        json.dumps(expected_criteria, indent=2) + "\n", encoding="utf-8"
    )
    if prior_gradings:
        # A final-judge task owes the student-facing feedback document
        # beside the two standard deliverables; the declaration makes
        # the generic verifier require it (docs/design.md, "Grading
        # output schema").
        (task_dir / "tests" / REQUIRED_FILES_FILENAME).write_text(
            json.dumps([FEEDBACK_FILENAME]) + "\n", encoding="utf-8"
        )

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
    if rubric_source_dir is not None:
        input_hashes["rubric_source"] = sha256_dir(rubric_source_dir)
    if prior_gradings:
        input_hashes["prior_gradings"] = prior_gradings_hash(prior_gradings)

    return MaterializedGradingTask(
        item_id=item_id,
        task_dir=task_dir,
        task_dir_name=name,
        input_hashes=input_hashes,
    )
