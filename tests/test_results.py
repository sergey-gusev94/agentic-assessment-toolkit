from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from agentic_assessment_toolkit.config import Stage, load_config
from agentic_assessment_toolkit.grading_schema import computed_sums, derive_scores
from agentic_assessment_toolkit.harbor import (
    RUN_RECORD_FILENAME,
    PriorTrialRef,
    RunRecordItem,
    write_run_record,
)
from agentic_assessment_toolkit.results import (
    EXCEPTION_CATEGORIES,
    OUTCOME_CATEGORIES,
    load_results,
)
from tests.test_config import GRADE_TOML, SOLVE_TOML, write_config

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
RESULT_FULL = FIXTURES_DIR / "harbor" / "result_full.json"

SOLVE_JOB = "20260731T000000Z__codex-high__cccccccc"
GRADE_JOB = "20260731T120000Z__codex-grader-sol-high__cccccccc"
RUBRIC_SHA = "ab" * 32

# The exact column contract of the tidy tables (stage-3 spec); results.py,
# report CSVs, and these tests must agree.
TRIALS_COLUMNS = [
    "stage",
    "job_name",
    "trial_name",
    "item_id",
    "item_identity",
    "course_id",
    "assignment_id",
    "config_name",
    "config_identity",
    "agent",
    "model",
    "reasoning_effort",
    "outcome",
    "late_exception",
    "exception_type",
    "reward",
    "score_pct",
    "base_pct",
    "sums_consistent",
    "grading_load_error",
    "n_input_tokens",
    "n_cache_tokens",
    "n_output_tokens",
    "cost_usd",
    "environment_setup_sec",
    "agent_setup_sec",
    "agent_execution_sec",
    "verifier_sec",
    "agent_timeout_sec",
    "started_at",
    "finished_at",
    "submission_source",
    "student_id",
    "rubric_name",
    "rubric_sha256",
    "solve_job_name",
    "solve_trial_name",
    "solver_config_name",
    "solver_config_identity",
    "solver_model",
    "context_config_name",
    "context_config_identity",
    "n_prior_gradings",
    "prior_trials",
    "superseded",
]

CRITERIA_COLUMNS = [
    "job_name",
    "trial_name",
    "item_id",
    "item_identity",
    "config_name",
    "config_identity",
    "criterion_id",
    "title",
    "points",
    "max_points",
    "bonus",
]


def make_job(
    root: Path,
    scratch: Path,
    *,
    stage: Stage,
    job_name: str,
    items: list[RunRecordItem],
    config_identity: str = "c" * 64,
    stage_dir: str | None = None,
) -> Path:
    """A job directory with a run record, under the stage's parent by default."""
    parent = stage_dir if stage_dir is not None else ("solving" if stage == "solve" else "grading")
    toml, name = (
        (SOLVE_TOML, "codex-high") if stage == "solve" else (GRADE_TOML, "codex-grader-sol-high")
    )
    config = load_config(write_config(scratch, toml, name))
    job_dir = root / parent / job_name
    job_dir.mkdir(parents=True)
    write_run_record(
        job_dir,
        stage=stage,
        config=config,
        config_identity=config_identity,
        command=["harbor", "run"],
        executed=True,
        repeats=1,
        max_concurrent_trials=8,
        items=items,
    )
    return job_dir


def solve_item(task: str) -> RunRecordItem:
    return RunRecordItem(
        item_id=f"SYN_C1/{task}",
        task_dir_name=task,
        item_identity="i" * 64,
        course_id="SYN_C1",
        assignment_id=task,
        input_hashes={"assignment": "0" * 64},
    )


def student_item(task: str, student: str = "stu1") -> RunRecordItem:
    return RunRecordItem(
        item_id=f"SYN_C1/{student}/HW1",
        task_dir_name=task,
        item_identity="j" * 64,
        course_id="SYN_C1",
        assignment_id="HW1",
        input_hashes={"rubric": RUBRIC_SHA, "submission": "1" * 64},
        submission_source="student",
        student_id=student,
    )


def solve_trial_item(task: str, solve_job: str, solve_trial: str) -> RunRecordItem:
    return RunRecordItem(
        item_id=f"{solve_job}/{solve_trial}",
        task_dir_name=task,
        item_identity="k" * 64,
        course_id="SYN_C1",
        assignment_id="HW1",
        input_hashes={"rubric": RUBRIC_SHA},
        submission_source="solve-trial",
        solve_job_name=solve_job,
        solve_trial_name=solve_trial,
    )


def trial_result(
    task: str,
    *,
    rewards: dict[str, float] | None,
    exception_type: str | None = None,
    **extra: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "task_name": task,
        "exception_info": (
            {"exception_type": exception_type} if exception_type is not None else None
        ),
        "verifier_result": {"rewards": rewards} if rewards is not None else None,
    }
    result.update(extra)
    return result


def write_trial(
    job_dir: Path,
    trial_name: str,
    result: dict[str, object],
    *,
    artifact_text: str | None = None,
) -> Path:
    trial_dir = job_dir / trial_name
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if artifact_text is not None:
        output_dir = trial_dir / "artifacts" / "app" / "grading_output"
        output_dir.mkdir(parents=True)
        (output_dir / "grading_result.json").write_text(artifact_text, encoding="utf-8")
    return trial_dir


def write_task_toml(
    root: Path,
    job_name: str,
    task: str,
    *,
    timeout_sec: float = 3600.0,
    text: str | None = None,
) -> None:
    """The materialized task.toml a job's task ran under, minimal form."""
    task_dir = root / "tasks" / job_name / task
    task_dir.mkdir(parents=True)
    content = text if text is not None else f"[agent]\ntimeout_sec = {timeout_sec}\n"
    (task_dir / "task.toml").write_text(content, encoding="utf-8")


def criterion(
    cid: str, points: float, max_points: float, *, bonus: bool = False
) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": cid,
        "title": f"criterion {cid}",
        "max_points": max_points,
        "points": points,
        "evidence": f"evidence for {cid}",
    }
    if bonus:
        entry["bonus"] = True
    return entry


def grading_data(criteria: list[dict[str, object]], **authored: float) -> dict[str, object]:
    """A valid grading result; keyword overrides make authored sums inconsistent."""
    data: dict[str, object] = {
        "schema_version": 1,
        "criteria": criteria,
        "overall_comment": "graded",
    }
    sums = computed_sums(data)
    sums.update(authored)
    data.update(sums)
    return data


def graded_rewards(data: dict[str, object]) -> dict[str, float]:
    """What the grading verifier's reward file records for a valid result."""
    scores = derive_scores(data)
    return {"reward": scores["score_pct"], "base_pct": scores["base_pct"]}


def row_for(trials: pd.DataFrame, trial_name: str) -> pd.Series[Any]:
    matching = trials[trials["trial_name"] == trial_name]
    assert len(matching) == 1
    return matching.iloc[0]


def as_bool(value: Any) -> bool:
    assert not pd.isna(value)
    return bool(value)


def test_empty_data_root_yields_full_columns(tmp_path: Path) -> None:
    tables = load_results(tmp_path / "root")
    assert list(tables.trials.columns) == TRIALS_COLUMNS
    assert list(tables.criteria.columns) == CRITERIA_COLUMNS
    assert tables.trials.empty
    assert tables.criteria.empty
    # Nullable dtypes even when empty: missing loads as NA, never 0.
    assert str(tables.trials.dtypes["stage"]) == "string"
    assert str(tables.trials.dtypes["late_exception"]) == "bool"
    assert str(tables.trials.dtypes["reward"]) == "Float64"
    assert str(tables.trials.dtypes["sums_consistent"]) == "boolean"
    assert str(tables.trials.dtypes["n_input_tokens"]) == "Int64"
    assert str(tables.trials.dtypes["environment_setup_sec"]) == "Float64"
    assert str(tables.criteria.dtypes["points"]) == "Float64"
    assert str(tables.criteria.dtypes["bonus"]) == "bool"


FULL_GRADING_RESULT = grading_data(
    [
        criterion("slope", 6.5, 8.0),
        criterion("intercept", 1.5, 2.0),
        criterion("plot", 0.5, 1.0, bonus=True),
    ]
)


def test_full_shape_fixture_pins_every_consumed_field(tmp_path: Path) -> None:
    """The one deliberate coupling to Harbor's on-disk TrialResult format."""
    root = tmp_path / "root"
    job_dir = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[student_item("SYN_C1__stu1__HW1")],
    )
    trial_dir = job_dir / "SYN_C1__stu1__HW1__full1aa"
    trial_dir.mkdir()
    write_task_toml(root, GRADE_JOB, "SYN_C1__stu1__HW1", timeout_sec=3600.0)
    shutil.copyfile(RESULT_FULL, trial_dir / "result.json")
    output_dir = trial_dir / "artifacts" / "app" / "grading_output"
    output_dir.mkdir(parents=True)
    (output_dir / "grading_result.json").write_text(
        json.dumps(FULL_GRADING_RESULT), encoding="utf-8"
    )

    tables = load_results(root)
    assert list(tables.trials.columns) == TRIALS_COLUMNS
    assert len(tables.trials) == 1
    row = tables.trials.iloc[0]
    assert row["stage"] == "grade"
    assert row["job_name"] == GRADE_JOB
    assert row["trial_name"] == "SYN_C1__stu1__HW1__full1aa"
    assert row["item_id"] == "SYN_C1/stu1/HW1"
    assert row["item_identity"] == "j" * 64
    assert row["course_id"] == "SYN_C1"
    assert row["assignment_id"] == "HW1"
    assert row["config_name"] == "codex-grader-sol-high"
    assert row["config_identity"] == "c" * 64
    assert row["agent"] == "codex"
    assert row["model"] == "openai/gpt-5.6-sol"
    assert pd.isna(row["reasoning_effort"])
    assert row["outcome"] == "completed"
    assert as_bool(row["late_exception"]) is False
    assert pd.isna(row["exception_type"])
    assert row["reward"] == 85.0
    assert row["score_pct"] == 85.0
    assert row["base_pct"] == 80.0
    assert as_bool(row["sums_consistent"]) is True
    assert as_bool(row["grading_load_error"]) is False
    assert row["n_input_tokens"] == 12345
    assert row["n_cache_tokens"] == 2345
    assert row["n_output_tokens"] == 678
    assert float(row["cost_usd"]) == pytest.approx(0.1234)
    assert row["environment_setup_sec"] == 30.5
    assert row["agent_setup_sec"] == 5.0
    assert row["agent_execution_sec"] == 600.0
    assert row["verifier_sec"] == 15.0
    # 3600 from the task.toml times the fixture's timeout_multiplier 1.0.
    assert row["agent_timeout_sec"] == 3600.0
    assert row["started_at"] == "2026-07-31T10:00:00"
    assert row["finished_at"] == "2026-07-31T10:10:50.500000"
    assert row["submission_source"] == "student"
    assert row["student_id"] == "stu1"
    assert row["rubric_name"] == "default"
    assert row["rubric_sha256"] == RUBRIC_SHA
    assert pd.isna(row["solve_job_name"])
    assert pd.isna(row["solve_trial_name"])
    assert pd.isna(row["solver_config_name"])
    assert pd.isna(row["solver_config_identity"])
    assert pd.isna(row["solver_model"])

    criteria = tables.criteria
    assert list(criteria.columns) == CRITERIA_COLUMNS
    assert list(criteria["criterion_id"]) == ["slope", "intercept", "plot"]
    assert list(criteria["points"]) == [6.5, 1.5, 0.5]
    assert list(criteria["max_points"]) == [8.0, 2.0, 1.0]
    assert list(criteria["bonus"]) == [False, False, True]
    first = criteria.iloc[0]
    assert first["job_name"] == GRADE_JOB
    assert first["trial_name"] == "SYN_C1__stu1__HW1__full1aa"
    assert first["item_id"] == "SYN_C1/stu1/HW1"
    assert first["item_identity"] == "j" * 64
    assert first["config_name"] == "codex-grader-sol-high"
    assert first["config_identity"] == "c" * 64
    assert first["title"] == "criterion slope"


def test_outcome_taxonomy_over_solve_trials(tmp_path: Path) -> None:
    root = tmp_path / "root"
    tasks = ["ok", "contract", "agent", "infra", "tmo", "cancel", "weird", "silent"]
    job_dir = make_job(
        root,
        tmp_path,
        stage="solve",
        job_name=SOLVE_JOB,
        items=[solve_item(task) for task in tasks],
    )
    write_trial(job_dir, "ok__a", trial_result("ok", rewards={"reward": 1.0}))
    write_trial(job_dir, "contract__a", trial_result("contract", rewards={"reward": 0.0}))
    write_trial(
        job_dir, "agent__a", trial_result("agent", rewards=None, exception_type="ApiRateLimitError")
    )
    write_trial(
        job_dir,
        "infra__a",
        trial_result("infra", rewards=None, exception_type="SandboxBuildFailedError"),
    )
    write_trial(
        job_dir, "tmo__a", trial_result("tmo", rewards=None, exception_type="AgentTimeoutError")
    )
    write_trial(
        job_dir, "cancel__a", trial_result("cancel", rewards=None, exception_type="CancelledError")
    )
    write_trial(
        job_dir, "weird__a", trial_result("weird", rewards=None, exception_type="SomethingOddError")
    )
    write_trial(job_dir, "silent__a", trial_result("silent", rewards=None))
    # A trial the run record does not claim is skipped.
    write_trial(job_dir, "orphan__a", trial_result("orphan", rewards={"reward": 1.0}))

    trials = load_results(root).trials
    assert len(trials) == len(tasks)
    assert set(trials["outcome"]) <= set(OUTCOME_CATEGORIES)

    ok = row_for(trials, "ok__a")
    assert ok["outcome"] == "completed"
    assert ok["reward"] == 1.0
    contract = row_for(trials, "contract__a")
    assert contract["outcome"] == "contract_failed"
    assert contract["reward"] == 0.0
    assert row_for(trials, "agent__a")["outcome"] == "agent_error"
    assert row_for(trials, "infra__a")["outcome"] == "infra_error"
    assert row_for(trials, "tmo__a")["outcome"] == "timeout"
    assert row_for(trials, "cancel__a")["outcome"] == "cancelled"
    weird = row_for(trials, "weird__a")
    assert weird["outcome"] == "unknown"
    assert weird["exception_type"] == "SomethingOddError"
    silent = row_for(trials, "silent__a")
    assert silent["outcome"] == "unknown"
    assert pd.isna(silent["exception_type"])

    # Unverified trials have no reward; the exception is the category,
    # never a late exception.
    for name in ("agent__a", "infra__a", "tmo__a", "cancel__a", "weird__a", "silent__a"):
        row = row_for(trials, name)
        assert pd.isna(row["reward"])
        assert as_bool(row["late_exception"]) is False
    # Solve rows never carry grading extras or scores.
    for name in ("ok__a", "contract__a"):
        row = row_for(trials, name)
        assert pd.isna(row["score_pct"])
        assert pd.isna(row["base_pct"])
        assert pd.isna(row["sums_consistent"])
        assert pd.isna(row["submission_source"])
        assert pd.isna(row["rubric_name"])
        assert pd.isna(row["solver_config_name"])


def test_exception_categories_map_into_the_taxonomy() -> None:
    assert set(EXCEPTION_CATEGORIES.values()) <= set(OUTCOME_CATEGORIES)
    assert "completed" not in EXCEPTION_CATEGORIES.values()
    assert "contract_failed" not in EXCEPTION_CATEGORIES.values()


def test_late_exception_never_changes_the_category(tmp_path: Path) -> None:
    root = tmp_path / "root"
    solve_dir = make_job(
        root, tmp_path, stage="solve", job_name=SOLVE_JOB, items=[solve_item("s1")]
    )
    write_trial(
        solve_dir,
        "s1__a",
        trial_result("s1", rewards={"reward": 1.0}, exception_type="VerifierTimeoutError"),
    )
    grade_dir = make_job(
        root, tmp_path, stage="grade", job_name=GRADE_JOB, items=[student_item("g1")]
    )
    data = grading_data([criterion("a", 1.0, 2.0)])
    write_trial(
        grade_dir,
        "g1__a",
        trial_result("g1", rewards=graded_rewards(data), exception_type="AgentTimeoutError"),
        artifact_text=json.dumps(data),
    )

    trials = load_results(root).trials
    solve_row = row_for(trials, "s1__a")
    assert solve_row["outcome"] == "completed"
    assert as_bool(solve_row["late_exception"]) is True
    assert solve_row["exception_type"] == "VerifierTimeoutError"
    grade_row = row_for(trials, "g1__a")
    assert grade_row["outcome"] == "completed"
    assert as_bool(grade_row["late_exception"]) is True
    assert grade_row["exception_type"] == "AgentTimeoutError"


def test_grading_outcomes_flags_and_criteria(tmp_path: Path) -> None:
    root = tmp_path / "root"
    tasks = ["valid", "inconsistent", "contract", "missing", "broken", "invalid"]
    job_dir = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[student_item(task) for task in tasks],
    )
    # Criteria ids are deliberately not alphabetical: the criteria table
    # keeps the authored order.
    data = grading_data([criterion("zeta", 3.0, 4.0), criterion("alpha", 1.0, 2.0, bonus=True)])
    rewards = graded_rewards(data)
    assert rewards == {"reward": 100.0, "base_pct": 75.0}
    write_trial(
        job_dir, "valid__a", trial_result("valid", rewards=rewards), artifact_text=json.dumps(data)
    )
    inconsistent = grading_data(
        [criterion("zeta", 3.0, 4.0), criterion("alpha", 1.0, 2.0, bonus=True)], base_points=999.0
    )
    write_trial(
        job_dir,
        "inconsistent__a",
        trial_result("inconsistent", rewards=rewards),
        artifact_text=json.dumps(inconsistent),
    )
    write_trial(job_dir, "contract__a", trial_result("contract", rewards={"reward": 0.0}))
    write_trial(job_dir, "missing__a", trial_result("missing", rewards=rewards))
    write_trial(
        job_dir, "broken__a", trial_result("broken", rewards=rewards), artifact_text="{broken"
    )
    invalid = {"schema_version": 1, "criteria": [], "overall_comment": "empty"}
    write_trial(
        job_dir,
        "invalid__a",
        trial_result("invalid", rewards=rewards),
        artifact_text=json.dumps(invalid),
    )

    tables = load_results(root)
    trials = tables.trials

    valid_row = row_for(trials, "valid__a")
    assert valid_row["outcome"] == "completed"
    assert valid_row["score_pct"] == 100.0
    assert valid_row["base_pct"] == 75.0
    assert as_bool(valid_row["sums_consistent"]) is True
    assert as_bool(valid_row["grading_load_error"]) is False
    assert valid_row["submission_source"] == "student"
    assert valid_row["student_id"] == "stu1"
    assert valid_row["rubric_name"] == "default"
    assert valid_row["rubric_sha256"] == RUBRIC_SHA

    inconsistent_row = row_for(trials, "inconsistent__a")
    assert inconsistent_row["outcome"] == "completed"
    assert as_bool(inconsistent_row["sums_consistent"]) is False
    assert as_bool(inconsistent_row["grading_load_error"]) is False

    # A contract violation is a failed measurement: its reward 0.0 must
    # never look like a score.
    contract_row = row_for(trials, "contract__a")
    assert contract_row["outcome"] == "contract_failed"
    assert contract_row["reward"] == 0.0
    assert pd.isna(contract_row["score_pct"])
    assert pd.isna(contract_row["base_pct"])
    assert pd.isna(contract_row["sums_consistent"])
    assert as_bool(contract_row["grading_load_error"]) is False

    # Load errors keep the reward-file percentages, stay completed, and
    # contribute no criteria rows.
    for name in ("missing__a", "broken__a", "invalid__a"):
        row = row_for(trials, name)
        assert row["outcome"] == "completed"
        assert row["score_pct"] == 100.0
        assert row["base_pct"] == 75.0
        assert pd.isna(row["sums_consistent"])
        assert as_bool(row["grading_load_error"]) is True

    criteria = tables.criteria
    assert list(criteria["trial_name"]) == [
        "inconsistent__a",
        "inconsistent__a",
        "valid__a",
        "valid__a",
    ]
    assert list(criteria["criterion_id"]) == ["zeta", "alpha", "zeta", "alpha"]
    assert list(criteria["points"]) == [3.0, 1.0, 3.0, 1.0]
    assert list(criteria["max_points"]) == [4.0, 2.0, 4.0, 2.0]
    assert list(criteria["bonus"]) == [False, True, False, True]


def test_token_and_cost_totals(tmp_path: Path) -> None:
    root = tmp_path / "root"
    tasks = ["single", "none", "steps", "empty"]
    job_dir = make_job(
        root,
        tmp_path,
        stage="solve",
        job_name=SOLVE_JOB,
        items=[solve_item(task) for task in tasks],
    )
    write_trial(
        job_dir,
        "single__a",
        trial_result(
            "single",
            rewards={"reward": 1.0},
            agent_result={
                "n_input_tokens": 100,
                "n_cache_tokens": 40,
                "n_output_tokens": 7,
                "cost_usd": 0.25,
            },
        ),
    )
    write_trial(
        job_dir, "none__a", trial_result("none", rewards={"reward": 1.0}, agent_result=None)
    )
    # Multi-step trials sum per-step agent contexts; a field no step
    # reports stays NA, never 0.
    write_trial(
        job_dir,
        "steps__a",
        trial_result(
            "steps",
            rewards={"reward": 1.0},
            agent_result=None,
            step_results=[
                {
                    "step_name": "one",
                    "agent_result": {"n_input_tokens": 10, "n_output_tokens": 1, "cost_usd": 0.5},
                },
                {"step_name": "two", "agent_result": {"n_input_tokens": 5, "n_output_tokens": 2}},
            ],
        ),
    )
    write_trial(
        job_dir, "empty__a", trial_result("empty", rewards={"reward": 1.0}, agent_result={})
    )

    trials = load_results(root).trials
    assert str(trials.dtypes["n_input_tokens"]) == "Int64"
    assert str(trials.dtypes["cost_usd"]) == "Float64"

    single = row_for(trials, "single__a")
    assert single["n_input_tokens"] == 100
    assert single["n_cache_tokens"] == 40
    assert single["n_output_tokens"] == 7
    assert float(single["cost_usd"]) == pytest.approx(0.25)

    steps = row_for(trials, "steps__a")
    assert steps["n_input_tokens"] == 15
    assert pd.isna(steps["n_cache_tokens"])
    assert steps["n_output_tokens"] == 3
    assert float(steps["cost_usd"]) == pytest.approx(0.5)

    for name in ("none__a", "empty__a"):
        row = row_for(trials, name)
        assert pd.isna(row["n_input_tokens"])
        assert pd.isna(row["n_cache_tokens"])
        assert pd.isna(row["n_output_tokens"])
        assert pd.isna(row["cost_usd"])


def test_phase_durations(tmp_path: Path) -> None:
    root = tmp_path / "root"
    tasks = ["good", "open", "mixed", "garbled"]
    job_dir = make_job(
        root,
        tmp_path,
        stage="solve",
        job_name=SOLVE_JOB,
        items=[solve_item(task) for task in tasks],
    )
    write_trial(
        job_dir,
        "good__a",
        trial_result(
            "good",
            rewards={"reward": 1.0},
            environment_setup={
                "started_at": "2026-07-31T10:00:00",
                "finished_at": "2026-07-31T10:00:30.500000",
            },
            agent_execution={
                "started_at": "2026-07-31T10:00:35+00:00",
                "finished_at": "2026-07-31T10:10:35+00:00",
            },
        ),
    )
    write_trial(
        job_dir,
        "open__a",
        trial_result(
            "open",
            rewards={"reward": 1.0},
            agent_setup={"started_at": "2026-07-31T10:00:30", "finished_at": None},
        ),
    )
    # A pair mixing naive and aware datetimes cannot be subtracted.
    write_trial(
        job_dir,
        "mixed__a",
        trial_result(
            "mixed",
            rewards={"reward": 1.0},
            environment_setup={
                "started_at": "2026-07-31T10:00:00",
                "finished_at": "2026-07-31T10:00:30+00:00",
            },
        ),
    )
    write_trial(
        job_dir,
        "garbled__a",
        trial_result(
            "garbled",
            rewards={"reward": 1.0},
            environment_setup={"started_at": "not-a-date", "finished_at": "also-not"},
        ),
    )

    trials = load_results(root).trials
    good = row_for(trials, "good__a")
    assert good["environment_setup_sec"] == 30.5
    assert good["agent_execution_sec"] == 600.0
    assert pd.isna(good["agent_setup_sec"])
    assert pd.isna(good["verifier_sec"])
    assert pd.isna(row_for(trials, "open__a")["agent_setup_sec"])
    assert pd.isna(row_for(trials, "mixed__a")["environment_setup_sec"])
    assert pd.isna(row_for(trials, "garbled__a")["environment_setup_sec"])


def test_agent_timeout_from_the_materialized_task_toml(tmp_path: Path) -> None:
    """The timeout each trial ran under; anything unrecoverable loads as NA."""
    root = tmp_path / "root"
    tasks = ["plain", "scaled", "absent", "broken", "agentless", "nonpositive", "zeromult"]
    job_dir = make_job(
        root,
        tmp_path,
        stage="solve",
        job_name=SOLVE_JOB,
        items=[solve_item(task) for task in tasks],
    )
    write_task_toml(root, SOLVE_JOB, "plain", timeout_sec=1800.0)
    write_task_toml(root, SOLVE_JOB, "scaled", timeout_sec=1800.0)
    write_task_toml(root, SOLVE_JOB, "broken", text="not = [valid")
    write_task_toml(root, SOLVE_JOB, "agentless", text="[verifier]\ntimeout_sec = 600.0\n")
    write_task_toml(root, SOLVE_JOB, "nonpositive", timeout_sec=0.0)
    write_task_toml(root, SOLVE_JOB, "zeromult", timeout_sec=1800.0)
    write_trial(job_dir, "plain__a", trial_result("plain", rewards={"reward": 1.0}))
    # Harbor's timeout multiplier scales the task's timeouts at run time.
    write_trial(
        job_dir,
        "scaled__a",
        trial_result("scaled", rewards={"reward": 1.0}, config={"timeout_multiplier": 2.0}),
    )
    # A recorded non-positive multiplier is unusable: the effective
    # timeout loads as missing, like a non-positive task.toml timeout.
    write_trial(
        job_dir,
        "zeromult__a",
        trial_result("zeromult", rewards={"reward": 1.0}, config={"timeout_multiplier": 0.0}),
    )
    for task in ("absent", "broken", "agentless", "nonpositive"):
        write_trial(job_dir, f"{task}__a", trial_result(task, rewards={"reward": 1.0}))

    trials = load_results(root).trials
    assert row_for(trials, "plain__a")["agent_timeout_sec"] == 1800.0
    assert row_for(trials, "scaled__a")["agent_timeout_sec"] == 3600.0
    for name in ("absent__a", "broken__a", "agentless__a", "nonpositive__a", "zeromult__a"):
        assert pd.isna(row_for(trials, name)["agent_timeout_sec"]), name


def test_solver_join(tmp_path: Path) -> None:
    root = tmp_path / "root"
    make_job(root, tmp_path, stage="solve", job_name=SOLVE_JOB, items=[], config_identity="d" * 64)
    gone_job = "20990101T000000Z__gone__ffffffff"
    job_dir = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[
            solve_trial_item("s1", SOLVE_JOB, "t1__abc1234"),
            solve_trial_item("s2", gone_job, "t9__zzz9999"),
            student_item("s3"),
        ],
    )
    for task in ("s1", "s2", "s3"):
        write_trial(job_dir, f"{task}__a", trial_result(task, rewards={"reward": 0.0}))

    trials = load_results(root).trials
    joined = row_for(trials, "s1__a")
    assert joined["submission_source"] == "solve-trial"
    assert pd.isna(joined["student_id"])
    assert joined["solve_job_name"] == SOLVE_JOB
    assert joined["solve_trial_name"] == "t1__abc1234"
    assert joined["solver_config_name"] == "codex-high"
    assert joined["solver_config_identity"] == "d" * 64
    assert joined["solver_model"] == "openai/gpt-5.6-sol"

    unknown = row_for(trials, "s2__a")
    assert unknown["solve_job_name"] == gone_job
    assert pd.isna(unknown["solver_config_name"])
    assert pd.isna(unknown["solver_config_identity"])
    assert pd.isna(unknown["solver_model"])

    student = row_for(trials, "s3__a")
    assert pd.isna(student["solve_job_name"])
    assert pd.isna(student["solver_config_name"])


def judge_item(task: str) -> RunRecordItem:
    """A final-judge item: a student grading item with judge lineage."""
    return RunRecordItem(
        item_id="SYN_C1/stu1/HW1",
        task_dir_name=task,
        item_identity="m" * 64,
        course_id="SYN_C1",
        assignment_id="HW1",
        input_hashes={"rubric": RUBRIC_SHA, "prior_gradings": "2" * 64},
        submission_source="student",
        student_id="stu1",
        context_config_name="codex-grader-sol-high",
        context_config_identity="e" * 64,
        prior_trials=(
            PriorTrialRef(job_name=GRADE_JOB, trial_name="g1__t1"),
            PriorTrialRef(job_name=GRADE_JOB, trial_name="g1__t2"),
        ),
    )


def test_judge_lineage_loads_into_trials(tmp_path: Path) -> None:
    root = tmp_path / "root"
    job_dir = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[judge_item("jt"), student_item("gt", student="stu2")],
    )
    data = grading_data([criterion("a", 4.0, 5.0)])
    for task in ("jt", "gt"):
        write_trial(
            job_dir,
            f"{task}__t1",
            trial_result(task, rewards=graded_rewards(data)),
            artifact_text=json.dumps(data),
        )
    trials = load_results(root).trials
    judge_row = trials[trials["trial_name"] == "jt__t1"].iloc[0]
    assert judge_row["context_config_name"] == "codex-grader-sol-high"
    assert judge_row["context_config_identity"] == "e" * 64
    assert judge_row["n_prior_gradings"] == 2
    assert judge_row["prior_trials"] == f"{GRADE_JOB}/g1__t1; {GRADE_JOB}/g1__t2"
    # An ordinary grading row carries the lineage columns as missing.
    plain_row = trials[trials["trial_name"] == "gt__t1"].iloc[0]
    assert pd.isna(plain_row["context_config_identity"])
    assert pd.isna(plain_row["n_prior_gradings"])
    assert pd.isna(plain_row["prior_trials"])


def judge_item_over(task: str, identity: str, prior_trial_names: list[str]) -> RunRecordItem:
    return RunRecordItem(
        item_id="SYN_C1/stu1/HW1",
        task_dir_name=task,
        item_identity=identity,
        course_id="SYN_C1",
        assignment_id="HW1",
        input_hashes={"rubric": RUBRIC_SHA},
        submission_source="student",
        student_id="stu1",
        context_config_name="codex-grader-sol-high",
        context_config_identity="e" * 64,
        prior_trials=tuple(
            PriorTrialRef(job_name=GRADE_JOB, trial_name=name) for name in prior_trial_names
        ),
    )


def test_supersession_marks_strict_prior_subsets(tmp_path: Path) -> None:
    """A re-judge over more prior gradings supersedes the earlier judgment;
    non-comparable prior sets all stay current."""
    root = tmp_path / "root"
    job_dir = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[
            judge_item_over("j2", "m" * 64, ["g1", "g2"]),
            judge_item_over("j3", "n" * 64, ["g1", "g2", "g3"]),
            judge_item_over("jx", "o" * 64, ["g4"]),  # disjoint: stays current
        ],
    )
    data = grading_data([criterion("a", 4.0, 5.0)])
    for task in ("j2", "j3", "jx"):
        write_trial(
            job_dir,
            f"{task}__t1",
            trial_result(task, rewards=graded_rewards(data)),
            artifact_text=json.dumps(data),
        )
    trials = load_results(root).trials
    by_trial = {row["trial_name"]: row for _, row in trials.iterrows()}
    assert bool(by_trial["j2__t1"]["superseded"]) is True
    assert bool(by_trial["j3__t1"]["superseded"]) is False
    assert bool(by_trial["jx__t1"]["superseded"]) is False


def test_invalid_judgment_never_supersedes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    job_dir = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[
            judge_item_over("j2", "m" * 64, ["g1", "g2"]),
            judge_item_over("j3", "n" * 64, ["g1", "g2", "g3"]),
        ],
    )
    data = grading_data([criterion("a", 4.0, 5.0)])
    write_trial(
        job_dir,
        "j2__t1",
        trial_result("j2", rewards=graded_rewards(data)),
        artifact_text=json.dumps(data),
    )
    # The wider judgment violated the contract: it is a failed
    # measurement, so the earlier valid judgment stays current.
    write_trial(job_dir, "j3__t1", trial_result("j3", rewards={"reward": 0.0}))
    trials = load_results(root).trials
    by_trial = {row["trial_name"]: row for _, row in trials.iterrows()}
    assert bool(by_trial["j2__t1"]["superseded"]) is False


def test_loaded_judge_lineage_joins_in_the_review_queue(tmp_path: Path) -> None:
    """End to end across the loader and metrics: the loader's
    "job/trial; ..." lineage string must be exactly what review_queue
    splits, or every judge row silently detaches into a standalone row."""
    from agentic_assessment_toolkit import metrics

    root = tmp_path / "root"
    initial_job = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[student_item("g1")],
    )
    data = grading_data([criterion("a", 4.0, 5.0)])
    write_trial(
        initial_job,
        "g1__t1",
        trial_result("g1", rewards=graded_rewards(data)),
        artifact_text=json.dumps(data),
    )
    judge_job = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name="20260731T130000Z__codex-judge__jjjjjjjj",
        items=[judge_item_over("jt", "m" * 64, ["g1__t1"])],
        config_identity="j" * 64,
    )
    write_trial(
        judge_job,
        "jt__t1",
        trial_result("jt", rewards=graded_rewards(data)),
        artifact_text=json.dumps(data),
    )
    queue = metrics.review_queue(load_results(root).trials)
    assert len(queue) == 1
    row = queue.iloc[0]
    assert row["n_gradings"] == 1  # attached, not a standalone judge row
    assert row["n_final_gradings"] == 1


def test_malformed_prior_trials_lineage_loads_as_missing(tmp_path: Path) -> None:
    """One malformed entry drops the whole lineage, never a truncated one."""
    root = tmp_path / "root"
    job_dir = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[judge_item_over("jt", "m" * 64, ["g1", "g2"])],
    )
    record_path = job_dir / RUN_RECORD_FILENAME
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["items"][0]["prior_trials"][1] = {"job_name": GRADE_JOB}  # no trial_name
    record_path.write_text(json.dumps(record), encoding="utf-8")
    data = grading_data([criterion("a", 4.0, 5.0)])
    write_trial(
        job_dir,
        "jt__t1",
        trial_result("jt", rewards=graded_rewards(data)),
        artifact_text=json.dumps(data),
    )
    row = load_results(root).trials.iloc[0]
    assert pd.isna(row["n_prior_gradings"])
    assert pd.isna(row["prior_trials"])


def test_stage_mismatched_or_recordless_jobs_contribute_nothing(tmp_path: Path) -> None:
    """Fails closed: a missing run record, a record without a stage, or a
    record whose stage does not match its directory contributes nothing."""
    root = tmp_path / "root"
    misfiled_grade = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name=GRADE_JOB,
        items=[student_item("g1")],
        stage_dir="solving",
    )
    write_trial(misfiled_grade, "g1__a", trial_result("g1", rewards={"reward": 0.0}))
    misfiled_solve = make_job(
        root,
        tmp_path,
        stage="solve",
        job_name=SOLVE_JOB,
        items=[solve_item("s1")],
        stage_dir="grading",
    )
    write_trial(misfiled_solve, "s1__a", trial_result("s1", rewards={"reward": 1.0}))
    recordless = root / "solving" / "20260731T010000Z__manual__eeeeeeee"
    recordless.mkdir(parents=True)
    write_trial(recordless, "x1__a", trial_result("x1", rewards={"reward": 1.0}))
    stageless = make_job(
        root,
        tmp_path,
        stage="solve",
        job_name="20260731T020000Z__codex-high__cccccccc",
        items=[solve_item("s2")],
    )
    record_path = stageless / RUN_RECORD_FILENAME
    record = json.loads(record_path.read_text(encoding="utf-8"))
    del record["stage"]
    record_path.write_text(json.dumps(record), encoding="utf-8")
    write_trial(stageless, "s2__a", trial_result("s2", rewards={"reward": 1.0}))

    tables = load_results(root)
    assert tables.trials.empty
    assert tables.criteria.empty


def test_absent_lineage_fields_load_as_missing(tmp_path: Path) -> None:
    """Records written before the lineage fields existed load as NA (decision 6)."""
    root = tmp_path / "root"
    job_dir = make_job(
        root, tmp_path, stage="grade", job_name=GRADE_JOB, items=[student_item("g1")]
    )
    record_path = job_dir / RUN_RECORD_FILENAME
    record = json.loads(record_path.read_text(encoding="utf-8"))
    for key in ("submission_source", "student_id", "solve_job_name", "solve_trial_name"):
        del record["items"][0][key]
    record["items"][0]["input_hashes"] = {}
    record_path.write_text(json.dumps(record), encoding="utf-8")
    write_trial(job_dir, "g1__a", trial_result("g1", rewards={"reward": 0.0}))

    row = load_results(root).trials.iloc[0]
    assert row["item_id"] == "SYN_C1/stu1/HW1"
    assert pd.isna(row["submission_source"])
    assert pd.isna(row["student_id"])
    assert pd.isna(row["solve_job_name"])
    assert pd.isna(row["solve_trial_name"])
    assert pd.isna(row["rubric_sha256"])


def test_trials_sorted_by_stage_job_and_trial(tmp_path: Path) -> None:
    root = tmp_path / "root"
    solve_dir = make_job(
        root,
        tmp_path,
        stage="solve",
        job_name=SOLVE_JOB,
        items=[solve_item("a1"), solve_item("b2")],
    )
    write_trial(solve_dir, "b2__z", trial_result("b2", rewards={"reward": 1.0}))
    write_trial(solve_dir, "a1__z", trial_result("a1", rewards={"reward": 1.0}))
    grade_dir = make_job(
        root, tmp_path, stage="grade", job_name=GRADE_JOB, items=[student_item("g1")]
    )
    write_trial(grade_dir, "g1__z", trial_result("g1", rewards={"reward": 0.0}))

    trials = load_results(root).trials
    assert list(trials["stage"]) == ["grade", "solve", "solve"]
    assert list(trials["trial_name"]) == ["g1__z", "a1__z", "b2__z"]
