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
    completed_items,
    completed_solve_submissions,
    harbor_environment,
    harbor_version,
    is_completed_trial,
    job_dir_name,
    read_run_record,
    write_run_record,
)
from tests.test_config import SOLVE_TOML, write_config


def test_harbor_version_is_pinned_range() -> None:
    assert harbor_version().startswith("0.20.")


def test_build_harbor_command() -> None:
    command = build_harbor_command(Path("/x/harbor-job.json"))
    assert command == ["harbor", "run", "-c", "/x/harbor-job.json", "--yes"]


def test_harbor_environment_disables_telemetry() -> None:
    environment = harbor_environment({"PATH": "/bin"})
    assert environment["HARBOR_TELEMETRY"] == "0"
    assert environment["PATH"] == "/bin"


def test_job_dir_name_format() -> None:
    moment = datetime(2026, 7, 31, 12, 30, 5, tzinfo=UTC)
    assert job_dir_name("codex-high", "a" * 64, moment) == "20260731T123005Z__codex-high__aaaaaaaa"


def make_item(task_dir_name: str, item_id: str, identity: str) -> RunRecordItem:
    return RunRecordItem(
        item_id=item_id,
        task_dir_name=task_dir_name,
        item_identity=identity,
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
        items=items,
    )
    return job_dir


def write_trial(
    job_dir: Path,
    trial_name: str,
    *,
    task_name: str,
    completed: bool = True,
    submission_files: dict[str, str] | None = None,
) -> Path:
    trial_dir = job_dir / "harbor" / trial_name
    trial_dir.mkdir(parents=True)
    result: dict[str, object] = {
        "task_name": task_name,
        "exception_info": None if completed else {"exception_type": "AgentTimeoutError"},
        "verifier_result": {"rewards": {"reward": 1.0}} if completed else None,
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
        tmp_path / "runs",
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i" * 64)],
    )
    record: dict[str, Any] | None = read_run_record(job_dir)
    assert record is not None
    assert record["stage"] == "solve"
    assert record["toolkit_version"]
    assert record["harbor_version"].startswith("0.20.")
    assert record["items"][0]["item_id"] == "C1/HW1"


def test_read_run_record_handles_garbage(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    assert read_run_record(job_dir) is None
    (job_dir / "aat-run.json").write_text("{broken", encoding="utf-8")
    assert read_run_record(job_dir) is None


def test_is_completed_trial() -> None:
    assert is_completed_trial(
        {"exception_info": None, "verifier_result": {"rewards": {"reward": 0.0}}}
    )
    # A recorded reward proves verification completed, even if a late
    # exception was also recorded.
    assert is_completed_trial(
        {
            "exception_info": {"exception_type": "X"},
            "verifier_result": {"rewards": {"reward": 1.0}},
        }
    )
    assert not is_completed_trial({"exception_info": {"exception_type": "X"}})
    assert not is_completed_trial({"exception_info": None, "verifier_result": None})
    assert not is_completed_trial({"exception_info": None, "verifier_result": {"rewards": {}}})


def test_completed_items(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    jobs_root = tmp_path / "runs"
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
    write_trial(job_dir, "t1__abc1234", task_name="t1", completed=True)
    write_trial(job_dir, "t2__def5678", task_name="t2", completed=False)
    assert completed_items(jobs_root) == {("C1/HW1", "done-identity")}
    assert completed_items(tmp_path / "absent") == set()


def test_completed_solve_submissions(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    jobs_root = tmp_path / "runs"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[
            make_item("t1", "C1/HW1", "i1"),
            make_item("t2", "C1/HW2", "i2"),
        ],
    )
    write_trial(job_dir, "t1__abc1234", task_name="t1", submission_files={"answer.md": "42"})
    write_trial(job_dir, "t1__zzz9999", task_name="t1", completed=False)
    write_trial(job_dir, "t2__ghi9012", task_name="t2", submission_files=None)  # no artifact

    submissions = completed_solve_submissions(jobs_root, "codex-high")
    assert len(submissions) == 1
    submission = submissions[0]
    assert submission.course_id == "C1"
    assert submission.assignment_id == "HW1"
    assert submission.trial_name == "t1__abc1234"
    assert submission.item_id == f"{job_dir.name}/t1__abc1234"
    assert (submission.directory / "answer.md").read_text(encoding="utf-8") == "42"

    assert completed_solve_submissions(jobs_root, "other-config") == []
    assert completed_solve_submissions(jobs_root, "codex-high", course_id="C2") == []
    assert completed_solve_submissions(jobs_root, "codex-high", assignment_id="HW2") == []


def test_grading_jobs_are_not_solve_sources(tmp_path: Path) -> None:
    grade_toml = SOLVE_TOML.replace('"solve"', '"grade"').replace('"solver"', '"grader"')
    config_path = write_config(tmp_path, grade_toml, "codex-grader-high")
    jobs_root = tmp_path / "runs"
    job_dir = write_job(
        jobs_root,
        "20260731T000000Z__codex-grader-high__cccccccc",
        stage="grade",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i1")],
    )
    write_trial(job_dir, "t1__abc1234", task_name="t1", submission_files={"a.md": "x"})
    assert completed_solve_submissions(jobs_root, "codex-grader-high") == []


def test_run_record_is_deterministic_json(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    job_dir = write_job(
        tmp_path / "runs",
        "j1",
        stage="solve",
        config_path=config_path,
        items=[],
    )
    text = (job_dir / harbor_mod.RUN_RECORD_FILENAME).read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert list(parsed) == sorted(parsed)
