from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic_assessment_toolkit import harbor as harbor_mod
from agentic_assessment_toolkit.config import load_config
from agentic_assessment_toolkit.harbor import (
    RunRecordItem,
    build_harbor_command,
    done_items,
    harbor_subprocess_env,
    harbor_version,
    is_graded_trial,
    is_verified_trial,
    job_dir_name,
    read_run_record,
    verified_solve_submissions,
    write_run_record,
)
from tests.test_config import GRADE_TOML, SOLVE_TOML, write_config

# What the grading verifier emits for a valid grading result; a contract
# violation emits {"reward": 0.0} with no base_pct.
GRADED_REWARDS = {"reward": 85.0, "base_pct": 80.0}


def test_harbor_version_is_pinned_range() -> None:
    assert harbor_version().startswith("0.20.")


def test_build_harbor_command() -> None:
    command = build_harbor_command(Path("/x/harbor-job.json"))
    assert command == ["harbor", "run", "-c", "/x/harbor-job.json", "--yes"]


def test_harbor_environment_disables_telemetry() -> None:
    environment = harbor_subprocess_env({"PATH": "/bin"})
    assert environment["HARBOR_TELEMETRY"] == "0"
    assert environment["PATH"] == "/bin"


def test_job_dir_name_format() -> None:
    moment = datetime(2026, 7, 31, 12, 30, 5, tzinfo=UTC)
    assert job_dir_name("codex-high", "a" * 64, moment) == "20260731T123005Z__codex-high__aaaaaaaa"


def make_item(
    task_dir_name: str,
    item_id: str,
    identity: str,
    course_id: str = "C1",
    assignment_id: str = "HW1",
) -> RunRecordItem:
    return RunRecordItem(
        item_id=item_id,
        task_dir_name=task_dir_name,
        item_identity=identity,
        course_id=course_id,
        assignment_id=assignment_id,
        input_hashes={"assignment": "0" * 64},
    )


def write_job(
    jobs_root: Path,
    job_name: str,
    *,
    stage: str,
    config_path: Path,
    items: list[RunRecordItem],
) -> Path:
    job_dir = jobs_root / job_name
    job_dir.mkdir(parents=True)
    config = load_config(config_path)
    write_run_record(
        job_dir,
        stage=stage,  # type: ignore[arg-type]
        config=config,
        config_identity="c" * 64,
        command=["harbor", "run"],
        executed=True,
        repeats=1,
        max_concurrent_trials=8,
        items=items,
    )
    return job_dir


def write_trial(
    job_dir: Path,
    trial_name: str,
    *,
    task_name: str,
    verified: bool = True,
    rewards: dict[str, float] | None = None,
    submission_files: dict[str, str] | None = None,
) -> Path:
    # Flat layout: the AAT job directory is the Harbor job directory, so
    # trials are its immediate subdirectories.
    trial_dir = job_dir / trial_name
    trial_dir.mkdir(parents=True)
    result: dict[str, object] = {
        "task_name": task_name,
        "exception_info": None if verified else {"exception_type": "AgentTimeoutError"},
        "verifier_result": (
            {"rewards": {"reward": 1.0} if rewards is None else rewards} if verified else None
        ),
    }
    (trial_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if submission_files is not None:
        artifact_dir = trial_dir / "artifacts" / "app" / "submission"
        artifact_dir.mkdir(parents=True)
        for name, content in submission_files.items():
            (artifact_dir / name).write_text(content, encoding="utf-8")
    return trial_dir


def test_run_record_roundtrip(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    job_dir = write_job(
        tmp_path / "solving",
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i" * 64)],
    )
    record: dict[str, Any] | None = read_run_record(job_dir)
    assert record is not None
    assert record["stage"] == "solve"
    assert record["schema_version"] == 1
    assert record["max_concurrent_trials"] == 8
    assert record["toolkit_version"]
    assert record["harbor_version"].startswith("0.20.")
    assert record["items"][0]["item_id"] == "C1/HW1"
    assert record["items"][0]["course_id"] == "C1"
    assert record["items"][0]["assignment_id"] == "HW1"


def test_read_run_record_handles_garbage(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    assert read_run_record(job_dir) is None
    (job_dir / "aat-run.json").write_text("{broken", encoding="utf-8")
    assert read_run_record(job_dir) is None


def test_is_verified_trial() -> None:
    assert is_verified_trial(
        {"exception_info": None, "verifier_result": {"rewards": {"reward": 0.0}}}
    )
    # A recorded reward proves verification completed, even if a late
    # exception was also recorded.
    assert is_verified_trial(
        {
            "exception_info": {"exception_type": "X"},
            "verifier_result": {"rewards": {"reward": 1.0}},
        }
    )
    assert not is_verified_trial({"exception_info": {"exception_type": "X"}})
    assert not is_verified_trial({"exception_info": None, "verifier_result": None})
    assert not is_verified_trial({"exception_info": None, "verifier_result": {"rewards": {}}})


def test_is_graded_trial() -> None:
    assert is_graded_trial({"exception_info": None, "verifier_result": {"rewards": GRADED_REWARDS}})
    # A contract violation completes the trial but is a failed
    # measurement, never a grade.
    assert not is_graded_trial(
        {"exception_info": None, "verifier_result": {"rewards": {"reward": 0.0}}}
    )
    assert not is_graded_trial({"exception_info": None, "verifier_result": None})
    # A non-dict rewards value completes the trial but is never a grade.
    assert not is_graded_trial({"exception_info": None, "verifier_result": {"rewards": [1.0]}})


def test_done_items(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    jobs_root = tmp_path / "solving"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[
            make_item("t1", "C1/HW1", "done-identity"),
            make_item("t2", "C1/HW2", "errored-identity"),
            make_item("t3", "C1/HW3", "never-ran-identity"),
        ],
    )
    write_trial(job_dir, "t1__abc1234", task_name="t1", verified=True)
    write_trial(job_dir, "t2__def5678", task_name="t2", verified=False)
    assert done_items(jobs_root, "solve") == {("C1/HW1", "done-identity")}
    assert done_items(tmp_path / "absent", "solve") == set()
    # Fails closed: a record whose stage does not match contributes nothing.
    assert done_items(jobs_root, "grade") == set()


def test_job_level_files_and_tasks_dir_are_not_trials(tmp_path: Path) -> None:
    """Flat layout: only subdirectories with a result.json are trials."""
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    jobs_root = tmp_path / "solving"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i1")],
    )
    # Harbor's job-level result.json is a file, never a trial.
    (job_dir / "result.json").write_text(json.dumps({"stats": {}}), encoding="utf-8")
    (job_dir / "config.json").write_text("{}", encoding="utf-8")
    # A stray directory without result.json (e.g. an interrupted trial).
    (job_dir / "t1__interrup").mkdir()
    write_trial(job_dir, "t1__abc1234", task_name="t1")
    assert done_items(jobs_root, "solve") == {("C1/HW1", "i1")}


def test_solve_contract_failure_counts_done(tmp_path: Path) -> None:
    """A 0-reward solve is a countable outcome, not a retryable failure."""
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    jobs_root = tmp_path / "solving"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i1")],
    )
    write_trial(job_dir, "t1__abc1234", task_name="t1", rewards={"reward": 0.0})
    assert done_items(jobs_root, "solve") == {("C1/HW1", "i1")}


def test_grading_doneness_requires_valid_result(tmp_path: Path) -> None:
    """An invalid grading result leaves the item not-done for regrading."""
    config_path = write_config(tmp_path, GRADE_TOML, "codex-grader-high")
    jobs_root = tmp_path / "grading"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-grader-high__cccccccc",
        stage="grade",
        config_path=config_path,
        items=[
            make_item("t1", "C1/stu1/HW1", "i1"),
            make_item("t2", "C1/stu2/HW1", "i2"),
        ],
    )
    write_trial(job_dir, "t1__abc1234", task_name="t1", rewards=GRADED_REWARDS)
    write_trial(job_dir, "t2__def5678", task_name="t2", rewards={"reward": 0.0})
    assert done_items(jobs_root, "grade") == {("C1/stu1/HW1", "i1")}


def test_verified_solve_submissions(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    jobs_root = tmp_path / "solving"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[
            make_item("t1", "C1/HW1", "i1"),
            make_item("t2", "C1/HW2", "i2", assignment_id="HW2"),
        ],
    )
    write_trial(job_dir, "t1__abc1234", task_name="t1", submission_files={"answer.md": "42"})
    write_trial(job_dir, "t1__zzz9999", task_name="t1", verified=False)
    write_trial(job_dir, "t2__ghi9012", task_name="t2", submission_files=None)  # no artifact

    submissions = verified_solve_submissions(jobs_root, "codex-high")
    assert len(submissions) == 1
    submission = submissions[0]
    assert submission.course_id == "C1"
    assert submission.assignment_id == "HW1"
    assert submission.trial_name == "t1__abc1234"
    assert submission.item_id == f"{job_dir.name}/t1__abc1234"
    assert (submission.directory / "answer.md").read_text(encoding="utf-8") == "42"

    assert verified_solve_submissions(jobs_root, "other-config") == []
    assert verified_solve_submissions(jobs_root, "codex-high", course_id="C2") == []
    assert verified_solve_submissions(jobs_root, "codex-high", assignment_id="HW2") == []


def test_grading_jobs_are_not_solve_sources(tmp_path: Path) -> None:
    grade_toml = SOLVE_TOML.replace('"solve"', '"grade"').replace('"solver"', '"grader"')
    config_path = write_config(tmp_path, grade_toml, "codex-grader-high")
    jobs_root = tmp_path / "solving"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-grader-high__cccccccc",
        stage="grade",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i1")],
    )
    write_trial(job_dir, "t1__abc1234", task_name="t1", submission_files={"a.md": "x"})
    assert verified_solve_submissions(jobs_root, "codex-grader-high") == []


def test_run_record_is_deterministic_json(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    job_dir = write_job(
        tmp_path / "solving",
        "j1",
        stage="solve",
        config_path=config_path,
        items=[],
    )
    text = (job_dir / harbor_mod.RUN_RECORD_FILENAME).read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert list(parsed) == sorted(parsed)
