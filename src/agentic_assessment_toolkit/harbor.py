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
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from . import __version__
from .config import ExperimentConfig, Stage
from .hashing import sha256_bytes

RUN_RECORD_FILENAME = "aat-run.json"
RUN_RECORD_SCHEMA_VERSION = 2

CODEX_AUTH_JSON_PATH_ENV = "CODEX_AUTH_JSON_PATH"
CODEX_FORCE_AUTH_JSON_ENV = "CODEX_FORCE_AUTH_JSON"
CODEX_HOME_ENV = "CODEX_HOME"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

_TRUE_ENV_VALUES = frozenset({"true", "1", "yes"})
_FALSE_ENV_VALUES = frozenset({"false", "0", "no"})


class HarborAuthenticationError(Exception):
    """A live Harbor run cannot authenticate its configured agent."""


@dataclass(frozen=True)
class HarborAuthentication:
    """Resolved, non-interactive authentication for one Harbor launch.

    ``environment_changes`` may contain credentials and is deliberately
    excluded from the representation and run record. Only ``method`` and
    ``source`` are durable provenance.
    """

    method: str
    source: str
    description: str
    environment_changes: Mapping[str, str | None] = field(repr=False, compare=False)

    def provenance(self) -> dict[str, str]:
        return {"method": self.method, "source": self.source}


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


def resolve_harbor_authentication(
    agent: str,
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> HarborAuthentication | None:
    """Resolve authentication before a live Harbor job is materialized.

    Codex runs prefer the same file-based cached login used by the local
    Codex CLI. Explicit Harbor overrides retain precedence, and an API key
    is used only when explicitly selected or no cached login exists.
    Other agents retain Harbor's own authentication behavior.
    """
    if agent != "codex":
        return None

    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home

    if CODEX_AUTH_JSON_PATH_ENV in environment:
        raw_path = environment[CODEX_AUTH_JSON_PATH_ENV]
        if not raw_path.strip():
            raise HarborAuthenticationError(f"{CODEX_AUTH_JSON_PATH_ENV} is set but empty")
        auth_path = Path(raw_path).expanduser().resolve()
        _validate_codex_auth_file(auth_path, source=CODEX_AUTH_JSON_PATH_ENV)
        return _auth_file_authentication(auth_path, source=CODEX_AUTH_JSON_PATH_ENV)

    if CODEX_FORCE_AUTH_JSON_ENV in environment:
        force_file = _parse_env_bool(
            environment[CODEX_FORCE_AUTH_JSON_ENV], name=CODEX_FORCE_AUTH_JSON_ENV
        )
        if force_file:
            auth_path = user_home / ".codex" / "auth.json"
            _validate_codex_auth_file(auth_path, source=CODEX_FORCE_AUTH_JSON_ENV)
            return _auth_file_authentication(auth_path, source=CODEX_FORCE_AUTH_JSON_ENV)
        return _api_key_authentication(environment, explicitly_selected=True)

    codex_home = environment.get(CODEX_HOME_ENV)
    auth_path = (
        Path(codex_home).expanduser() / "auth.json"
        if codex_home and codex_home.strip()
        else user_home / ".codex" / "auth.json"
    ).resolve()
    try:
        auth_path.stat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise HarborAuthenticationError(
            f"cannot inspect the automatic Codex auth file: {auth_path}"
        ) from error
    else:
        _validate_codex_auth_file(auth_path, source="automatic-cache")
        return _auth_file_authentication(auth_path, source="automatic-cache")

    api_key = environment.get(OPENAI_API_KEY_ENV)
    if api_key is not None:
        return _api_key_authentication(environment, explicitly_selected=False)

    raise HarborAuthenticationError(
        "no file-based Codex login or OpenAI API key is available; run 'codex login' "
        f"so {auth_path} exists, or set {OPENAI_API_KEY_ENV}. If Codex stores its login "
        'in the OS keyring, set cli_auth_credentials_store = "file" in Codex config '
        "and log in again"
    )


def _parse_env_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    raise HarborAuthenticationError(
        f"invalid {name} value {value!r}; expected true/false/1/0/yes/no"
    )


def _validate_codex_auth_file(path: Path, *, source: str) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HarborAuthenticationError(
            f"{source} selected a missing Codex auth file: {path}"
        ) from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarborAuthenticationError(
            f"{source} selected an unreadable or invalid Codex auth file: {path}"
        ) from error
    if not isinstance(data, dict) or not data:
        raise HarborAuthenticationError(
            f"{source} selected an empty or invalid Codex auth file: {path}"
        )


def _auth_file_authentication(path: Path, *, source: str) -> HarborAuthentication:
    return HarborAuthentication(
        method="codex-auth-json",
        source=source,
        description=(
            "cached Codex login" if source == "automatic-cache" else "explicit Codex auth file"
        ),
        environment_changes={
            CODEX_AUTH_JSON_PATH_ENV: str(path),
            CODEX_FORCE_AUTH_JSON_ENV: None,
            OPENAI_API_KEY_ENV: None,
        },
    )


def _api_key_authentication(
    environment: Mapping[str, str], *, explicitly_selected: bool
) -> HarborAuthentication:
    api_key = environment.get(OPENAI_API_KEY_ENV)
    if api_key is None or not api_key.strip():
        selection = (
            f" because {CODEX_FORCE_AUTH_JSON_ENV}=false selected API-key authentication"
            if explicitly_selected
            else ""
        )
        raise HarborAuthenticationError(f"{OPENAI_API_KEY_ENV} is missing or empty{selection}")
    return HarborAuthentication(
        method="openai-api-key",
        source=(CODEX_FORCE_AUTH_JSON_ENV if explicitly_selected else OPENAI_API_KEY_ENV),
        description="OpenAI API key",
        environment_changes={
            CODEX_AUTH_JSON_PATH_ENV: None,
            CODEX_FORCE_AUTH_JSON_ENV: "0",
            OPENAI_API_KEY_ENV: api_key,
        },
    )


def harbor_subprocess_env(
    base: Mapping[str, str] | None = None,
    changes: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Subprocess environment for Harbor runs.

    Telemetry is disabled: nothing leaves the machine except calls to
    the model providers (docs/brief.md, constraints).
    """
    environment = dict(os.environ if base is None else base)
    for name, value in (changes or {}).items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    environment["HARBOR_TELEMETRY"] = "0"
    return environment


def invoke_harbor(command: list[str], authentication: HarborAuthentication | None = None) -> int:
    changes = None if authentication is None else authentication.environment_changes
    completed = subprocess.run(  # noqa: S603
        command, env=harbor_subprocess_env(changes=changes), check=False
    )
    return completed.returncode


def utc_stamp(now: datetime | None = None) -> str:
    moment = now if now is not None else datetime.now(UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def job_dir_name(config_name: str, config_identity: str, now: datetime | None = None) -> str:
    return f"{utc_stamp(now)}__{config_name}__{config_identity[:8]}"


def create_unique_dir(parent: Path, base_name: str) -> Path:
    """Create ``parent/base_name``, uniquifying on same-second collisions.

    Job and report directory names are timestamped to the second, and
    their identity lives in their records (``aat-run.json``,
    ``provenance.json``), not in the name — so a rare ``-N`` suffix is
    harmless.
    """
    for attempt in range(1, 100):
        name = base_name if attempt == 1 else f"{base_name}-{attempt}"
        target = parent / name
        try:
            target.mkdir(parents=True)
        except FileExistsError:
            continue
        return target
    raise OSError(f"cannot create a fresh directory under {parent}")


@dataclass(frozen=True)
class RunRecordItem:
    """One requested item: identity, lineage, and the exact input hashes.

    ``course_id`` and ``assignment_id`` are recorded explicitly so every
    run record is self-describing — for solve-derived grading items the
    ``item_id`` is ``<solve-job>/<trial>`` and carries neither. The
    lineage fields say where a grading item's submission came from; they
    are always serialized, so no consumer ever parses an item id.
    """

    item_id: str
    task_dir_name: str
    item_identity: str
    course_id: str
    assignment_id: str
    input_hashes: dict[str, str]
    submission_source: str | None = None  # "student" | "solve-trial"; None for solve items
    student_id: str | None = None  # student grading items only
    solve_job_name: str | None = None  # solve-derived grading items only
    solve_trial_name: str | None = None


def write_run_record(
    job_dir: Path,
    *,
    stage: Stage,
    config: ExperimentConfig,
    config_identity: str,
    command: list[str],
    executed: bool,
    repeats: int,
    max_concurrent_trials: int,
    items: list[RunRecordItem],
    cli_version: str | None = None,
    authentication: HarborAuthentication | None = None,
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
        "max_concurrent_trials": max_concurrent_trials,
        "command": command,
        "executed": executed,
        "authentication": None if authentication is None else authentication.provenance(),
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
    """Yield (trial_dir, result) for every Harbor trial result in a job dir.

    The AAT job directory is the Harbor job directory (flat layout), so
    trials are its immediate subdirectories that hold a ``result.json``;
    the ``tasks/`` directory and Harbor's job-level files have none and
    are skipped naturally.
    """
    if not job_dir.is_dir():
        return
    for trial_dir in sorted(job_dir.iterdir(), key=lambda entry: entry.name):
        result_path = trial_dir / "result.json"
        if not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(result, dict):
            yield trial_dir, result


def is_verified_trial(result: dict[str, object]) -> bool:
    """Verified: the verifier recorded a reward (docs/design.md, denominator policy).

    A non-empty ``verifier_result.rewards`` proves verification ran to
    completion; any pre-verification failure (agent error, timeout,
    verifier crash) leaves it absent. A late exception recorded after a
    reward exists does not un-verify the trial.
    """
    verifier_result = result.get("verifier_result")
    return isinstance(verifier_result, dict) and bool(verifier_result.get("rewards"))


def is_graded_trial(result: dict[str, object]) -> bool:
    """Verified with a *valid* grading result.

    The grading verifier writes ``base_pct`` into the rewards only
    when ``grading_result.json`` passed validation; a contract violation
    emits the bare ``{"reward": 0.0}``. An invalid grading is a failed
    measurement, not a grade, so it never counts as done
    (docs/design.md, "Run records and idempotence").
    """
    if not is_verified_trial(result):
        return False
    verifier_result = result.get("verifier_result")
    if not isinstance(verifier_result, dict):
        return False
    rewards = verifier_result.get("rewards")
    return isinstance(rewards, dict) and "base_pct" in rewards


def done_trial_counts(
    job_dir: Path, check: Callable[[dict[str, object]], bool] = is_verified_trial
) -> dict[str, int]:
    """How many of each task's trials pass ``check`` in one job directory.

    With ``--repeats N`` a task has N trials in its job; the count is
    what tells a fully sampled item apart from one that lost trials to
    failures, which the run summary must report (docs/design.md, "Run
    records and idempotence").
    """
    counts: dict[str, int] = {}
    for _, result in trial_results(job_dir):
        if check(result):
            task_name = result.get("task_name")
            if isinstance(task_name, str):
                counts[task_name] = counts.get(task_name, 0) + 1
    return counts


def verified_task_names(
    job_dir: Path, check: Callable[[dict[str, object]], bool] = is_verified_trial
) -> set[str]:
    return set(done_trial_counts(job_dir, check))


def _record_items(record: dict[str, object]) -> list[dict[str, object]]:
    items = record.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def items_by_task_dir(record: dict[str, object]) -> dict[str, dict[str, object]]:
    """A run record's items indexed by ``task_dir_name``.

    A trial is matched to its item through this name (Harbor records it
    as the trial's ``task_name``), so an item without a string
    ``task_dir_name`` can never claim a trial and is dropped here.
    """
    index: dict[str, dict[str, object]] = {}
    for item in _record_items(record):
        task_dir_name = item.get("task_dir_name")
        if isinstance(task_dir_name, str):
            index[task_dir_name] = item
    return index


def done_items(jobs_root: Path, stage: Stage) -> set[tuple[str, str]]:
    """(item_id, item_identity) pairs with at least one done trial.

    Doneness is keyed on the pair, per docs/design.md: the identity alone
    is not item-specific (it hashes config + environment + rubric bytes),
    so distinct items routinely share one identity. The per-trial check
    is stage-specific: solve failures (reward 0) are countable outcomes
    and stay done; grading requires a valid grading result. Fails
    closed: a job whose recorded stage does not match contributes
    nothing to doneness.
    """
    check = is_graded_trial if stage == "grade" else is_verified_trial
    done = set()
    for job_dir in job_dirs(jobs_root):
        record = read_run_record(job_dir)
        if record is None or record.get("stage") != stage:
            continue
        verified = verified_task_names(job_dir, check)
        for task_dir_name, item in items_by_task_dir(record).items():
            if task_dir_name not in verified:
                continue
            item_id = item.get("item_id")
            item_identity = item.get("item_identity")
            if isinstance(item_id, str) and isinstance(item_identity, str):
                done.add((item_id, item_identity))
    return done


@dataclass(frozen=True)
class SolveSubmission:
    """A verified solve trial's submission artifact, ready for grading."""

    item_id: str
    course_id: str
    assignment_id: str
    solve_job_name: str
    trial_name: str
    directory: Path


@dataclass(frozen=True)
class SkippedSolveTrial:
    """An in-scope solve trial that yields no gradable submission.

    ``reason`` is ``"solve-failed"`` (the trial was never verified, so
    the agent errored, timed out, or the infrastructure failed) or
    ``"empty-submission"`` (verified, but the submission artifact is
    missing or empty — an output-contract failure). A failed solve is
    not done and reruns without ``--force``; an empty submission counts
    as a verified outcome, so producing a new attempt needs ``--force``.
    """

    course_id: str
    assignment_id: str
    solve_job_name: str
    trial_name: str
    reason: str


@dataclass(frozen=True)
class SolveSubmissionSelection:
    """What ``--from-solve`` selected, and what it had to pass over."""

    submissions: list[SolveSubmission]
    skipped: list[SkippedSolveTrial]


def verified_solve_submissions(
    solving_root: Path,
    solve_config_name: str,
    *,
    course_id: str | None = None,
    assignment_id: str | None = None,
) -> SolveSubmissionSelection:
    """Submission artifacts of verified solve trials under a named solve config.

    Each verified trial with a non-empty submission artifact is one
    gradable item. In-scope trials that yield no gradable submission —
    failed solves and empty submission artifacts — are returned as
    ``skipped`` so the caller can report them; grading nonexistent work
    would be worse than skipping, but the skip must never be silent.
    ``--course``/``--assignment`` narrowing excludes trials from scope
    entirely: a trial the user did not ask about is neither selected
    nor reported.
    """
    submissions = []
    skipped = []
    for job_dir in job_dirs(solving_root):
        record = read_run_record(job_dir)
        if record is None or record.get("stage") != "solve":
            continue
        config = record.get("config")
        if not isinstance(config, dict) or config.get("name") != solve_config_name:
            continue
        record_items = items_by_task_dir(record)
        for trial_dir, result in trial_results(job_dir):
            task_name = result.get("task_name")
            item = record_items.get(task_name) if isinstance(task_name, str) else None
            if item is None:
                continue
            item_course_id = item.get("course_id")
            item_assignment_id = item.get("assignment_id")
            if not isinstance(item_course_id, str) or not isinstance(item_assignment_id, str):
                continue
            if course_id is not None and item_course_id != course_id:
                continue
            if assignment_id is not None and item_assignment_id != assignment_id:
                continue
            # Harbor mirrors the artifact's absolute container path under
            # the trial's artifacts/ directory: /app/submission →
            # artifacts/app/submission.
            artifact_dir = trial_dir / "artifacts" / "app" / "submission"
            if not is_verified_trial(result):
                reason = "solve-failed"
            elif not artifact_dir.is_dir() or not any(artifact_dir.rglob("*")):
                reason = "empty-submission"
            else:
                reason = None
            if reason is not None:
                skipped.append(
                    SkippedSolveTrial(
                        course_id=item_course_id,
                        assignment_id=item_assignment_id,
                        solve_job_name=job_dir.name,
                        trial_name=trial_dir.name,
                        reason=reason,
                    )
                )
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
    return SolveSubmissionSelection(submissions=submissions, skipped=skipped)
