"""Results loading: Harbor job output into tidy trials and criteria tables.

The read side of docs/design.md ("Results, statistics, and reporting").
One loader walks ``solving/`` and ``grading/`` in the data root, joins
each job's run record with Harbor's per-trial ``result.json`` and, for
grading trials, re-validates the stored ``grading_result.json`` artifact,
and returns two tidy pandas tables. Everything is read-only and can be
regenerated at any time.

Missing values load as pandas NA, never as zero: zero never means
unknown. Reading Harbor's ``result.json`` as plain JSON is the one
deliberate coupling to Harbor's on-disk output format, pinned by the
``tests/fixtures/harbor/result_full.json`` fixture. The agent timeout
is read from each trial's materialized ``task.toml`` under the data
root's ``tasks/`` tree — toolkit-written, so an old job keeps the
timeout it actually ran under.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import grading_schema
from .harbor import (
    is_verified_trial,
    items_by_task_dir,
    job_dirs,
    read_run_record,
    trial_results,
)

# Every trial maps to exactly one category (docs/design.md, "Outcome
# taxonomy"). A verified trial — non-empty verifier rewards — is
# categorized by its verification outcome; an unverified trial by its
# recorded exception type via EXCEPTION_CATEGORIES.
OUTCOME_CATEGORIES = (
    "completed",
    "contract_failed",
    "agent_error",
    "infra_error",
    "timeout",
    "cancelled",
    "unknown",
)

# Harbor records only an exception's flat leaf class name
# (``ExceptionInfo.exception_type``), so the grouping into outcome
# families lives here, versioned with the toolkit. The names are
# enumerated from the installed harbor 0.20 source; unlisted names load
# as "unknown". Harbor 0.20 drives Docker through its CLI in a
# subprocess and never imports docker-py, so docker-py exception classes
# cannot appear and are not listed.
EXCEPTION_CATEGORIES: dict[str, str] = {
    # Timeouts: harbor/trial/errors.py plus the builtin.
    "AgentSetupTimeoutError": "timeout",
    "AgentTimeoutError": "timeout",
    "EnvironmentStartTimeoutError": "timeout",
    "TimeoutError": "timeout",
    "VerifierTimeoutError": "timeout",
    # Cancellation.
    "CancelledError": "cancelled",
    "KeyboardInterrupt": "cancelled",
    # Agent errors: provider and API failures, authentication, refusals,
    # context and output limits (harbor/agents/installed/base.py,
    # harbor/agents/installed/vibe.py, harbor/auth/errors.py,
    # harbor/llms/base.py).
    "AgentAuthenticationError": "agent_error",
    "AgentSafetyRefusalError": "agent_error",
    "ApiConnectionClosedError": "agent_error",
    "ApiConnectionError": "agent_error",
    "ApiError": "agent_error",
    "ApiInternalServerError": "agent_error",
    "ApiKeyRejectedError": "agent_error",
    "ApiOverloadedError": "agent_error",
    "ApiProviderResourceNotFoundError": "agent_error",
    "ApiRateLimitError": "agent_error",
    "ApiResponseStalledError": "agent_error",
    "ApiUsageLimitError": "agent_error",
    "AuthenticationError": "agent_error",
    "ContextLengthExceededError": "agent_error",
    "ContextWindowExceededError": "agent_error",
    "ModelNotFoundError": "agent_error",
    "NetworkConnectionError": "agent_error",
    "NonZeroAgentExitCodeError": "agent_error",
    "NotAuthenticatedError": "agent_error",
    "OAuthCallbackError": "agent_error",
    "OutputLengthExceededError": "agent_error",
    "OutputTokenExceededError": "agent_error",
    "UnknownApiError": "agent_error",
    # Infrastructure errors: environment build, healthcheck, sandbox,
    # and verifier infrastructure (harbor/environments/,
    # harbor/verifier/verifier.py, harbor/agents/computer_1/runtime.py).
    "AddTestsDirError": "infra_error",
    "DownloadVerifierDirError": "infra_error",
    "HealthcheckError": "infra_error",
    "MemoryLimitExceededError": "infra_error",
    "RewardFileEmptyError": "infra_error",
    "RewardFileNotFoundError": "infra_error",
    "RuntimeRequestError": "infra_error",
    "SandboxBuildFailedError": "infra_error",
    "ServiceOperationsUnsupportedError": "infra_error",
    "VerifierOutputParseError": "infra_error",
}

# Column order and nullable dtypes of the trials table. Columns that can
# be missing use the nullable pandas dtypes (Int64/Float64/boolean/
# string) so absence is NA, never a zero-like default; late_exception
# and grading_load_error are always computed, so they stay plain bool.
_TRIALS_DTYPES: dict[str, str] = {
    "stage": "string",
    "job_name": "string",
    "trial_name": "string",
    "item_id": "string",
    "item_identity": "string",
    "course_id": "string",
    "assignment_id": "string",
    "config_name": "string",
    "config_identity": "string",
    "agent": "string",
    "model": "string",
    "reasoning_effort": "string",
    "outcome": "string",
    "late_exception": "bool",
    "exception_type": "string",
    "reward": "Float64",
    "score_pct": "Float64",
    "base_pct": "Float64",
    "sums_consistent": "boolean",
    "grading_load_error": "bool",
    "n_input_tokens": "Int64",
    "n_cache_tokens": "Int64",
    "n_output_tokens": "Int64",
    "cost_usd": "Float64",
    "environment_setup_sec": "Float64",
    "agent_setup_sec": "Float64",
    "agent_execution_sec": "Float64",
    "verifier_sec": "Float64",
    "agent_timeout_sec": "Float64",
    "started_at": "string",
    "finished_at": "string",
    "submission_source": "string",
    "student_id": "string",
    "rubric_name": "string",
    "rubric_sha256": "string",
    "solve_job_name": "string",
    "solve_trial_name": "string",
    "solver_config_name": "string",
    "solver_config_identity": "string",
    "solver_model": "string",
    # Final-judge lineage (grading rows of judge configs only): the
    # initial config whose gradings the task presented, how many, and
    # exactly which trials ("job/trial; ..."; the run record holds the
    # structured list).
    "context_config_name": "string",
    "context_config_identity": "string",
    "n_prior_gradings": "Int64",
    "prior_trials": "string",
    # True for a valid final judgment that a later judgment of the same
    # submission, made over a strict superset of its prior gradings,
    # has replaced (the top-up-then-re-judge flow of docs/design.md,
    # decision 16). Always computed, so plain bool.
    "superseded": "bool",
}

_CRITERIA_DTYPES: dict[str, str] = {
    "job_name": "string",
    "trial_name": "string",
    "item_id": "string",
    "item_identity": "string",
    "config_name": "string",
    "config_identity": "string",
    "criterion_id": "string",
    "title": "string",
    "points": "Float64",
    "max_points": "Float64",
    "bonus": "bool",
}

# The four TimingInfo pairs in Harbor's TrialResult, and the column each
# one's duration lands in.
_TIMING_COLUMNS = (
    ("environment_setup", "environment_setup_sec"),
    ("agent_setup", "agent_setup_sec"),
    ("agent_execution", "agent_execution_sec"),
    ("verifier", "verifier_sec"),
)


@dataclass(frozen=True)
class ResultTables:
    """The two tidy tables: one row per trial, one row per criterion."""

    trials: pd.DataFrame
    criteria: pd.DataFrame


def load_results(data_root: Path) -> ResultTables:
    """Load every job under ``solving/`` and ``grading/`` into tidy tables.

    Fails closed per docs/design.md: a job whose run record is missing
    or whose recorded stage does not match its directory contributes
    nothing; a trial the run record does not claim is skipped.
    """
    trial_rows: list[dict[str, object]] = []
    criteria_rows: list[dict[str, object]] = []
    # solving/ is walked before grading/, so the solver-join map is
    # complete before any grading row consumes it.
    solver_configs: dict[str, tuple[str | None, str | None, str | None]] = {}
    for stage, jobs_root in (("solve", data_root / "solving"), ("grade", data_root / "grading")):
        for job_dir in job_dirs(jobs_root):
            record = read_run_record(job_dir)
            if record is None or record.get("stage") != stage:
                continue
            if stage == "solve":
                solver_configs[job_dir.name] = _solver_config(record)
            _load_job(stage, data_root, job_dir, record, solver_configs, trial_rows, criteria_rows)
    _mark_superseded(trial_rows)
    trials = _frame(trial_rows, _TRIALS_DTYPES).sort_values(
        ["stage", "job_name", "trial_name"], kind="stable", ignore_index=True
    )
    # Criteria keep their authored order within a trial, so only the
    # trial keys are sorted (stable sort).
    criteria = _frame(criteria_rows, _CRITERIA_DTYPES).sort_values(
        ["job_name", "trial_name"], kind="stable", ignore_index=True
    )
    return ResultTables(trials=trials, criteria=criteria)


def _load_job(
    stage: str,
    data_root: Path,
    job_dir: Path,
    record: dict[str, object],
    solver_configs: dict[str, tuple[str | None, str | None, str | None]],
    trial_rows: list[dict[str, object]],
    criteria_rows: list[dict[str, object]],
) -> None:
    config_value = record.get("config")
    config = config_value if isinstance(config_value, dict) else {}
    config_identity = _opt_str(record.get("config_identity"))
    items = items_by_task_dir(record)
    # Tasks are materialized beside the job (docs/data-conventions.md):
    # <data_root>/tasks/<job_name>/<task_dir_name>/task.toml.
    tasks_dir = data_root / "tasks" / job_dir.name
    timeout_cache: dict[str, float | None] = {}
    for trial_dir, result in trial_results(job_dir):
        task_name = result.get("task_name")
        if not isinstance(task_name, str):
            continue
        item = items.get(task_name)
        if item is None:
            continue
        row, criteria = _trial_row(
            stage=stage,
            job_name=job_dir.name,
            trial_dir=trial_dir,
            result=result,
            item=item,
            config=config,
            config_identity=config_identity,
            solver_configs=solver_configs,
            task_timeout_sec=_task_agent_timeout(tasks_dir, task_name, timeout_cache),
        )
        trial_rows.append(row)
        criteria_rows.extend(criteria)


def _trial_row(
    *,
    stage: str,
    job_name: str,
    trial_dir: Path,
    result: dict[str, object],
    item: dict[str, object],
    config: dict[str, object],
    config_identity: str | None,
    solver_configs: dict[str, tuple[str | None, str | None, str | None]],
    task_timeout_sec: float | None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    config_name = _opt_str(config.get("name"))
    rewards = _rewards(result)
    verified = is_verified_trial(result)

    exception_info = result.get("exception_info")
    exception_type = (
        _opt_str(exception_info.get("exception_type")) if isinstance(exception_info, dict) else None
    )
    if verified:
        # A verified trial is a measurement: categorized by its
        # verification outcome even when an exception was also recorded
        # — the exception becomes the late-exception flag, never the
        # category.
        if stage == "grade":
            outcome = "completed" if "base_pct" in rewards else "contract_failed"
        else:
            outcome = "contract_failed" if rewards.get("reward") == 0 else "completed"
        late_exception = isinstance(exception_info, dict)
    else:
        outcome = (
            EXCEPTION_CATEGORIES.get(exception_type, "unknown")
            if exception_type is not None
            else "unknown"
        )
        late_exception = False

    # score_pct/base_pct are set only for grading rows with a valid
    # grading result (the base_pct reward key): a contract-violation
    # reward of 0.0 must never look like a score.
    score_pct: float | None = None
    base_pct: float | None = None
    sums_consistent: bool | None = None
    grading_load_error = False
    criteria: list[dict[str, object]] = []
    if stage == "grade" and "base_pct" in rewards:
        score_pct = _opt_number(rewards.get("reward"))
        base_pct = _opt_number(rewards.get("base_pct"))
        # The sums flag is recomputed from the stored artifact, never
        # read from verifier details. A missing, unreadable, or invalid
        # artifact keeps the reward-file percentages but flags the row
        # and contributes no criteria rows.
        artifact = (
            trial_dir / "artifacts" / "app" / "grading_output" / grading_schema.RESULT_FILENAME
        )
        data, errors = grading_schema.load_grading_result(artifact)
        if data is None or errors:
            grading_load_error = True
        else:
            sums_consistent = grading_schema.sums_report(data)["consistent"] is True
            criteria = _criteria_rows(
                data,
                job_name=job_name,
                trial_name=trial_dir.name,
                item=item,
                config_name=config_name,
                config_identity=config_identity,
            )

    n_input, n_cache, n_output, cost = _token_cost_totals(result)

    # Lineage, rubric, and the solver join exist only on grading rows.
    submission_source = student_id = rubric_name = rubric_sha256 = None
    solve_job_name = solve_trial_name = None
    solver_config_name = solver_config_identity = solver_model = None
    context_config_name = context_config_identity = prior_trials = None
    n_prior_gradings: int | None = None
    if stage == "grade":
        submission_source = _opt_str(item.get("submission_source"))
        student_id = _opt_str(item.get("student_id"))
        solve_job_name = _opt_str(item.get("solve_job_name"))
        solve_trial_name = _opt_str(item.get("solve_trial_name"))
        rubric_name = _opt_str(config.get("rubric"))
        input_hashes = item.get("input_hashes")
        if isinstance(input_hashes, dict):
            rubric_sha256 = _opt_str(input_hashes.get("rubric"))
        if solve_job_name is not None:
            solver_config_name, solver_config_identity, solver_model = solver_configs.get(
                solve_job_name, (None, None, None)
            )
        context_config_name = _opt_str(item.get("context_config_name"))
        context_config_identity = _opt_str(item.get("context_config_identity"))
        prior_refs = _prior_trial_refs(item.get("prior_trials"))
        if prior_refs is not None:
            n_prior_gradings = len(prior_refs)
            prior_trials = "; ".join(f"{job}/{trial}" for job, trial in prior_refs)

    row: dict[str, object] = {
        "stage": stage,
        "job_name": job_name,
        "trial_name": trial_dir.name,
        "item_id": _opt_str(item.get("item_id")),
        "item_identity": _opt_str(item.get("item_identity")),
        "course_id": _opt_str(item.get("course_id")),
        "assignment_id": _opt_str(item.get("assignment_id")),
        "config_name": config_name,
        "config_identity": config_identity,
        "agent": _opt_str(config.get("agent")),
        "model": _opt_str(config.get("model")),
        "reasoning_effort": _opt_str(config.get("reasoning_effort")),
        "outcome": outcome,
        "late_exception": late_exception,
        "exception_type": exception_type,
        "reward": _opt_number(rewards.get("reward")) if verified else None,
        "score_pct": score_pct,
        "base_pct": base_pct,
        "sums_consistent": sums_consistent,
        "grading_load_error": grading_load_error,
        "n_input_tokens": n_input,
        "n_cache_tokens": n_cache,
        "n_output_tokens": n_output,
        "cost_usd": cost,
        **{column: _duration_sec(result.get(field)) for field, column in _TIMING_COLUMNS},
        "agent_timeout_sec": (
            None
            if task_timeout_sec is None or (multiplier := _timeout_multiplier(result)) is None
            else task_timeout_sec * multiplier
        ),
        "started_at": _opt_str(result.get("started_at")),
        "finished_at": _opt_str(result.get("finished_at")),
        "submission_source": submission_source,
        "student_id": student_id,
        "rubric_name": rubric_name,
        "rubric_sha256": rubric_sha256,
        "solve_job_name": solve_job_name,
        "solve_trial_name": solve_trial_name,
        "solver_config_name": solver_config_name,
        "solver_config_identity": solver_config_identity,
        "solver_model": solver_model,
        "context_config_name": context_config_name,
        "context_config_identity": context_config_identity,
        "n_prior_gradings": n_prior_gradings,
        "prior_trials": prior_trials,
        "superseded": False,  # computed over all rows by _mark_superseded
    }
    return row, criteria


def _mark_superseded(trial_rows: list[dict[str, object]]) -> None:
    """Mark final judgments replaced by a re-judge over more prior gradings.

    A top-up of the initial gradings makes the re-judge a new item
    identity, so the earlier judgment survives on disk by design — but
    it was made over strictly less evidence, and pooling it into
    final-grade statistics would silently average a current and an
    outdated judgment. Within (grading config identity, item id, context
    config identity), a valid judgment whose prior-trial set is a strict
    subset of another valid judgment's is marked superseded. Judgments
    with equal or non-comparable prior sets all stay current (repeats of
    one judge item share one set; a genuine conflict stays visible in
    the review queue). The statistics layer excludes superseded rows
    from score aggregates and counts them explicitly.
    """
    groups: dict[tuple[object, object, object], list[tuple[frozenset[str], dict[str, object]]]] = {}
    for row in trial_rows:
        if (
            row["stage"] == "grade"
            and row["context_config_identity"] is not None
            and isinstance(row["prior_trials"], str)
            and row["base_pct"] is not None
        ):
            key = (row["config_identity"], row["item_id"], row["context_config_identity"])
            prior_set = frozenset(str(row["prior_trials"]).split("; "))
            groups.setdefault(key, []).append((prior_set, row))
    for entries in groups.values():
        for prior_set, row in entries:
            row["superseded"] = any(prior_set < other_set for other_set, _ in entries)


def _prior_trial_refs(value: object) -> list[tuple[str, str]] | None:
    """The recorded prior-trial lineage as (job, trial) pairs; None when absent.

    A malformed entry drops the whole list rather than yielding a
    partial one — a truncated lineage would silently understate
    ``n_prior_gradings``.
    """
    if not isinstance(value, list):
        return None
    refs: list[tuple[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            return None
        job_name = entry.get("job_name")
        trial_name = entry.get("trial_name")
        if not isinstance(job_name, str) or not isinstance(trial_name, str):
            return None
        refs.append((job_name, trial_name))
    return refs


def _criteria_rows(
    data: dict[str, object],
    *,
    job_name: str,
    trial_name: str,
    item: dict[str, object],
    config_name: str | None,
    config_identity: str | None,
) -> list[dict[str, object]]:
    """One row per criterion of a load-valid grading result, in authored order."""
    entries = data.get("criteria")
    if not isinstance(entries, list):
        return []
    rows: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rows.append(
            {
                "job_name": job_name,
                "trial_name": trial_name,
                "item_id": _opt_str(item.get("item_id")),
                "item_identity": _opt_str(item.get("item_identity")),
                "config_name": config_name,
                "config_identity": config_identity,
                "criterion_id": _opt_str(entry.get("id")),
                "title": _opt_str(entry.get("title")),
                "points": _opt_number(entry.get("points")),
                "max_points": _opt_number(entry.get("max_points")),
                "bonus": entry.get("bonus", False) is True,
            }
        )
    return rows


def _solver_config(record: dict[str, object]) -> tuple[str | None, str | None, str | None]:
    """A solve record's (config_name, config_identity, model), for the solver join."""
    config_value = record.get("config")
    config = config_value if isinstance(config_value, dict) else {}
    return (
        _opt_str(config.get("name")),
        _opt_str(record.get("config_identity")),
        _opt_str(config.get("model")),
    )


def _rewards(result: dict[str, object]) -> dict[str, object]:
    verifier_result = result.get("verifier_result")
    if isinstance(verifier_result, dict):
        rewards = verifier_result.get("rewards")
        if isinstance(rewards, dict):
            return rewards
    return {}


def _token_cost_totals(
    result: dict[str, object],
) -> tuple[int | None, int | None, int | None, float | None]:
    """Token counts and cost, keeping Harbor's semantics verbatim.

    Mirrors ``TrialResult.compute_token_cost_totals``: single-step trials
    record one agent context on ``agent_result``; multi-step trials
    record one per step on ``step_results[i].agent_result`` and sum. The
    input count includes cached tokens. A value no context reports stays
    None — zero never means unknown.
    """
    agent_result = result.get("agent_result")
    contexts: list[dict[str, object]] = []
    if isinstance(agent_result, dict):
        contexts = [agent_result]
    else:
        step_results = result.get("step_results")
        if isinstance(step_results, list):
            for step in step_results:
                if isinstance(step, dict):
                    step_context = step.get("agent_result")
                    if isinstance(step_context, dict):
                        contexts.append(step_context)
    n_input: int | None = None
    n_cache: int | None = None
    n_output: int | None = None
    cost: float | None = None
    for context in contexts:
        value = _opt_int(context.get("n_input_tokens"))
        if value is not None:
            n_input = (n_input or 0) + value
        value = _opt_int(context.get("n_cache_tokens"))
        if value is not None:
            n_cache = (n_cache or 0) + value
        value = _opt_int(context.get("n_output_tokens"))
        if value is not None:
            n_output = (n_output or 0) + value
        cost_value = _opt_number(context.get("cost_usd"))
        if cost_value is not None:
            cost = (cost or 0.0) + cost_value
    return n_input, n_cache, n_output, cost


def _task_agent_timeout(
    tasks_dir: Path, task_name: str, cache: dict[str, float | None]
) -> float | None:
    """The ``[agent] timeout_sec`` of the trial's materialized task.toml.

    The task.toml is toolkit-written at materialization time, so it
    records the timeout the trial actually ran under — a job launched
    before the timeout constant changed keeps its own value. None when
    the file is missing, unreadable, malformed, or holds no positive
    number: an unknown timeout is missing, never a default.
    """
    if task_name not in cache:
        cache[task_name] = _read_task_agent_timeout(tasks_dir / task_name / "task.toml")
    return cache[task_name]


def _read_task_agent_timeout(path: Path) -> float | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    agent = data.get("agent")
    if not isinstance(agent, dict):
        return None
    value = _opt_number(agent.get("timeout_sec"))
    return value if value is not None and value > 0 else None


def _timeout_multiplier(result: dict[str, object]) -> float | None:
    """Harbor's per-trial timeout multiplier; 1.0 when absent.

    ``result.json`` records the trial config's ``timeout_multiplier``,
    which scales the task's timeouts at run time — the effective agent
    timeout is the task.toml value times this factor. A recorded value
    that is non-numeric or non-positive is unusable and yields None, so
    the effective timeout loads as missing — the same policy as a
    non-positive task.toml timeout.
    """
    config = result.get("config")
    recorded = config.get("timeout_multiplier") if isinstance(config, dict) else None
    if recorded is None:
        return 1.0
    value = _opt_number(recorded)
    if value is None or value <= 0:
        return None
    return value


def _duration_sec(timing: object) -> float | None:
    """A TimingInfo pair's duration in seconds; None when not computable.

    Harbor's timestamps are not guaranteed timezone-aware: a pair mixing
    naive and aware datetimes cannot be subtracted and loads as missing,
    like an unparseable or absent end.
    """
    if not isinstance(timing, dict):
        return None
    started = _opt_str(timing.get("started_at"))
    finished = _opt_str(timing.get("finished_at"))
    if started is None or finished is None:
        return None
    try:
        delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
    except (ValueError, TypeError):
        return None
    return delta.total_seconds()


def _frame(rows: list[dict[str, object]], dtypes: dict[str, str]) -> pd.DataFrame:
    """A DataFrame with the full column set and dtypes, even when empty."""
    return pd.DataFrame(rows, columns=list(dtypes)).astype(dtypes)


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _opt_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
