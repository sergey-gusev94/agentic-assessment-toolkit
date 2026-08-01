"""Harbor command construction, invocation, run records, and job-output reading.

The toolkit invokes Harbor only through its CLI; reading Harbor's
per-trial ``result.json`` (the same file its viewer consumes) is the one
deliberate coupling to Harbor's on-disk output format (docs/design.md,
"Run records and idempotence").
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from . import __version__
from .config import ExperimentConfig, Stage
from .hashing import sha256_bytes
from .jobs import HARBOR_JOB_NAME

RUN_RECORD_FILENAME = "aat-run.json"
RUN_RECORD_SCHEMA_VERSION = 1


def harbor_version() -> str:
    """The installed Harbor package version.

    Read from package metadata so that offline paths (tests,
    ``--materialize-only``) never invoke Harbor.
    """
    return metadata.version("harbor")


def cli_harbor_version() -> str | None:
    """What ``harbor --version`` actually prints, from the binary on PATH.

    The PATH binary is what ``invoke_harbor`` runs, and it can belong to
    a different environment than this package's ``harbor`` dependency —
    so executing runs record this version, not the package metadata.
    ``None`` when the binary is missing or misbehaves.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            ["harbor", "--version"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def build_harbor_command(job_config_path: Path) -> list[str]:
    # --yes: harbor prompts on stdin when task env templates reference
    # host variables; a wrapper must never block on that prompt.
    return ["harbor", "run", "-c", str(job_config_path), "--yes"]


def harbor_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Subprocess environment for Harbor runs.

    Telemetry is disabled: nothing leaves the machine except calls to
    the model providers (docs/brief.md, constraints).
    """
    environment = dict(os.environ if base is None else base)
    environment["HARBOR_TELEMETRY"] = "0"
    return environment


def invoke_harbor(command: list[str]) -> int:
    completed = subprocess.run(command, env=harbor_environment(), check=False)  # noqa: S603
    return completed.returncode


def utc_stamp(now: datetime | None = None) -> str:
    moment = now if now is not None else datetime.now(UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def job_dir_name(config_name: str, config_identity: str, now: datetime | None = None) -> str:
    return f"{utc_stamp(now)}__{config_name}__{config_identity[:8]}"


@dataclass(frozen=True)
class RunRecordItem:
    """One requested item: its identity and the exact input hashes."""

    item_id: str
    task_dir_name: str
    item_identity: str
    input_hashes: dict[str, str]


def write_run_record(
    job_dir: Path,
    *,
    stage: Stage,
    config: ExperimentConfig,
    config_identity: str,
    command: list[str],
    executed: bool,
    repeats: int,
    items: list[RunRecordItem],
    cli_version: str | None = None,
) -> Path:
    record = {
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "created_utc": datetime.now(UTC).isoformat(),
        "toolkit_version": __version__,
        "harbor_version": cli_version if cli_version is not None else harbor_version(),
        "harbor_version_source": "cli" if cli_version is not None else "package-metadata",
        "stage": stage,
        "config": {
            "name": config.name,
            "sha256": sha256_bytes(config.raw_bytes),
            "agent": config.agent,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "prompt": config.prompt_name,
            "rubric": config.rubric_name,
            "agent_args": list(config.agent_args),
        },
        "config_identity": config_identity,
        "repeats": repeats,
        "command": command,
        "executed": executed,
        "items": [asdict(item) for item in items],
    }
    path = job_dir / RUN_RECORD_FILENAME
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_run_record(job_dir: Path) -> dict[str, object] | None:
    path = job_dir / RUN_RECORD_FILENAME
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def job_dirs(jobs_root: Path) -> list[Path]:
    if not jobs_root.is_dir():
        return []
    return sorted(
        (entry for entry in jobs_root.iterdir() if (entry / RUN_RECORD_FILENAME).is_file()),
        key=lambda entry: entry.name,
    )


def trial_results(job_dir: Path) -> Iterator[tuple[Path, dict[str, object]]]:
    """Yield (trial_dir, result) for every Harbor trial result in a job dir."""
    harbor_dir = job_dir / HARBOR_JOB_NAME
    if not harbor_dir.is_dir():
        return
    for trial_dir in sorted(harbor_dir.iterdir(), key=lambda entry: entry.name):
        result_path = trial_dir / "result.json"
        if not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(result, dict):
            yield trial_dir, result


def is_completed_trial(result: dict[str, object]) -> bool:
    """Completed, non-error: the verifier recorded a reward.

    A non-empty ``verifier_result.rewards`` proves verification ran to
    completion; any pre-verification failure (agent error, timeout,
    verifier crash) leaves it absent. A late exception recorded after a
    reward exists does not un-complete the trial.
    """
    verifier_result = result.get("verifier_result")
    return isinstance(verifier_result, dict) and bool(verifier_result.get("rewards"))


def completed_task_names(job_dir: Path) -> set[str]:
    names = set()
    for _, result in trial_results(job_dir):
        if is_completed_trial(result):
            task_name = result.get("task_name")
            if isinstance(task_name, str):
                names.add(task_name)
    return names


def _record_items(record: dict[str, object]) -> list[dict[str, object]]:
    items = record.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def completed_items(jobs_root: Path) -> set[tuple[str, str]]:
    """(item_id, item_identity) pairs with at least one completed trial.

    Doneness is keyed on the pair, per docs/design.md: the identity alone
    is not item-specific (it hashes config + environment + rubric bytes),
    so distinct items routinely share one identity.
    """
    done = set()
    for job_dir in job_dirs(jobs_root):
        record = read_run_record(job_dir)
        if record is None:
            continue
        completed = completed_task_names(job_dir)
        for item in _record_items(record):
            item_id = item.get("item_id")
            task_dir_name = item.get("task_dir_name")
            item_identity = item.get("item_identity")
            if (
                isinstance(item_id, str)
                and isinstance(task_dir_name, str)
                and isinstance(item_identity, str)
                and task_dir_name in completed
            ):
                done.add((item_id, item_identity))
    return done


@dataclass(frozen=True)
class SolveSubmission:
    """A completed solve trial's submission artifact, ready for grading."""

    item_id: str
    course_id: str
    assignment_id: str
    solve_job_name: str
    trial_name: str
    directory: Path


def completed_solve_submissions(
    runs_root: Path,
    solve_config_name: str,
    *,
    course_id: str | None = None,
    assignment_id: str | None = None,
) -> list[SolveSubmission]:
    """Submission artifacts of completed solve trials under a named solve config.

    Each completed trial is one gradable item. Trials whose submission
    artifact is missing or empty (e.g. an output-contract failure) are
    skipped here; they remain visible as explicit outcomes in the solve
    job itself.
    """
    submissions = []
    for job_dir in job_dirs(runs_root):
        record = read_run_record(job_dir)
        if record is None or record.get("stage") != "solve":
            continue
        config = record.get("config")
        if not isinstance(config, dict) or config.get("name") != solve_config_name:
            continue
        items_by_task_dir = {
            item.get("task_dir_name"): item.get("item_id") for item in _record_items(record)
        }
        for trial_dir, result in trial_results(job_dir):
            if not is_completed_trial(result):
                continue
            item_id = items_by_task_dir.get(result.get("task_name"))
            if not isinstance(item_id, str) or "/" not in item_id:
                continue
            item_course_id, item_assignment_id = item_id.split("/", 1)
            if course_id is not None and item_course_id != course_id:
                continue
            if assignment_id is not None and item_assignment_id != assignment_id:
                continue
            # Harbor mirrors the artifact's absolute container path under
            # the trial's artifacts/ directory: /app/submission →
            # artifacts/app/submission.
            artifact_dir = trial_dir / "artifacts" / "app" / "submission"
            if not artifact_dir.is_dir() or not any(artifact_dir.rglob("*")):
                continue
            submissions.append(
                SolveSubmission(
                    item_id=f"{job_dir.name}/{trial_dir.name}",
                    course_id=item_course_id,
                    assignment_id=item_assignment_id,
                    solve_job_name=job_dir.name,
                    trial_name=trial_dir.name,
                    directory=artifact_dir,
                )
            )
    return submissions
