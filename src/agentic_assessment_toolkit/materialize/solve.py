"""Materialize an as-received assignment directory into a Harbor solve task.

The task presents the whole assignment, unmodified, at /app/assignment;
the solver prompt directs the agent to produce its solution in
/app/submission (docs/design.md, "Solve task layout and verifier"). The
input-hash manifest and the generic verifier live under tests/, which
Harbor uploads only after the agent finishes — hidden from the solver.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..hashing import sha256_dir, sha256_file
from . import _common

SUBMISSION_PATH = config.STAGE_TASK_SETTINGS["solve"][0]


@dataclass(frozen=True)
class MaterializedSolveTask:
    item_id: str
    task_dir: Path
    task_dir_name: str
    input_hashes: dict[str, str]


def materialize_solve_task(
    *,
    assignment_dir: Path,
    course_id: str,
    assignment_id: str,
    environment_flavor: str,
    agent: str,
    prompt_name: str,
    tasks_dir: Path,
) -> MaterializedSolveTask:
    """Write one solve task; see the module docstring.

    ``environment_flavor`` names the capability the assignment needs and
    ``agent`` selects that flavor's per-agent template (the agent CLI is
    baked into the image). The flavor name is never suffixed: it is
    compared by value elsewhere — the ``grading`` flavor is rejected for
    solve, and the Gurobi license mount requires ``optimization``.
    """
    item_id = f"{course_id}/{assignment_id}"
    name = _common.task_dir_name([course_id, assignment_id], item_id)
    task_dir = _common.create_task_dir(tasks_dir, name)

    prompt_path = config.prompt_path(prompt_name)
    (task_dir / "instruction.md").write_bytes(prompt_path.read_bytes())

    (task_dir / "task.toml").write_text(config.rendered_task_toml("solve"), encoding="utf-8")

    environment_template = config.environment_path(environment_flavor, agent)
    _common.write_dockerfile(
        task_dir, environment_flavor, agent, ["COPY assignment /app/assignment"]
    )
    _common.copy_tree(assignment_dir, task_dir / "environment" / "assignment")

    _common.write_test_runner(task_dir, "solve_verifier.py")
    verifier_path = config.verifier_path("solve")
    (task_dir / "tests" / "solve_verifier.py").write_bytes(verifier_path.read_bytes())
    _common.write_input_manifest(task_dir, assignment_dir)

    return MaterializedSolveTask(
        item_id=item_id,
        task_dir=task_dir,
        task_dir_name=name,
        input_hashes={
            "assignment": sha256_dir(assignment_dir),
            "prompt": sha256_file(prompt_path),
            "environment": sha256_file(environment_template),
            "verifier": sha256_file(verifier_path),
        },
    )
