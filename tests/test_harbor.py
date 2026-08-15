from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

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


def write_codex_auth(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"tokens": {}}', encoding="utf-8")
    return path


def test_authentication_defaults_to_cached_codex_login_over_api_key(tmp_path: Path) -> None:
    auth_path = write_codex_auth(tmp_path / ".codex" / "auth.json")

    authentication = harbor_mod.resolve_harbor_authentication(
        "codex", {"OPENAI_API_KEY": "api-secret"}, home=tmp_path
    )

    assert authentication is not None
    assert authentication.provenance() == {
        "method": "codex-auth-json",
        "source": "automatic-cache",
    }
    assert authentication.description == "cached Codex login"
    assert authentication.environment_changes == {
        "CODEX_AUTH_JSON_PATH": str(auth_path),
        "CODEX_FORCE_AUTH_JSON": None,
        "OPENAI_API_KEY": None,
    }


def test_authentication_honors_codex_home(tmp_path: Path) -> None:
    codex_home = tmp_path / "separate-codex-home"
    auth_path = write_codex_auth(codex_home / "auth.json")

    authentication = harbor_mod.resolve_harbor_authentication(
        "codex", {"CODEX_HOME": str(codex_home)}, home=tmp_path / "unused"
    )

    assert authentication is not None
    assert authentication.environment_changes["CODEX_AUTH_JSON_PATH"] == str(auth_path)


def test_explicit_auth_path_has_precedence(tmp_path: Path) -> None:
    explicit = write_codex_auth(tmp_path / "explicit.json")
    write_codex_auth(tmp_path / ".codex" / "auth.json")

    authentication = harbor_mod.resolve_harbor_authentication(
        "codex",
        {
            "CODEX_AUTH_JSON_PATH": str(explicit),
            "CODEX_FORCE_AUTH_JSON": "false",
            "OPENAI_API_KEY": "api-secret",
        },
        home=tmp_path,
    )

    assert authentication is not None
    assert authentication.provenance() == {
        "method": "codex-auth-json",
        "source": "CODEX_AUTH_JSON_PATH",
    }
    assert authentication.environment_changes["CODEX_AUTH_JSON_PATH"] == str(explicit)


def test_force_auth_json_selects_default_auth_file(tmp_path: Path) -> None:
    auth_path = write_codex_auth(tmp_path / ".codex" / "auth.json")

    authentication = harbor_mod.resolve_harbor_authentication(
        "codex", {"CODEX_FORCE_AUTH_JSON": "yes"}, home=tmp_path
    )

    assert authentication is not None
    assert authentication.source == "CODEX_FORCE_AUTH_JSON"
    assert authentication.environment_changes["CODEX_AUTH_JSON_PATH"] == str(auth_path)


def test_force_auth_json_false_selects_api_key(tmp_path: Path) -> None:
    write_codex_auth(tmp_path / ".codex" / "auth.json")

    authentication = harbor_mod.resolve_harbor_authentication(
        "codex",
        {"CODEX_FORCE_AUTH_JSON": "0", "OPENAI_API_KEY": "api-secret"},
        home=tmp_path,
    )

    assert authentication is not None
    assert authentication.provenance() == {
        "method": "openai-api-key",
        "source": "CODEX_FORCE_AUTH_JSON",
    }
    assert authentication.environment_changes["OPENAI_API_KEY"] == "api-secret"


def test_api_key_is_fallback_when_cached_login_is_absent(tmp_path: Path) -> None:
    authentication = harbor_mod.resolve_harbor_authentication(
        "codex", {"OPENAI_API_KEY": "api-secret"}, home=tmp_path
    )

    assert authentication is not None
    assert authentication.provenance() == {
        "method": "openai-api-key",
        "source": "OPENAI_API_KEY",
    }


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "no file-based Codex login"),
        ({"OPENAI_API_KEY": ""}, "OPENAI_API_KEY is missing or empty"),
        ({"CODEX_AUTH_JSON_PATH": ""}, "CODEX_AUTH_JSON_PATH is set but empty"),
        ({"CODEX_FORCE_AUTH_JSON": "sometimes"}, "invalid CODEX_FORCE_AUTH_JSON"),
        (
            {"CODEX_FORCE_AUTH_JSON": "false", "OPENAI_API_KEY": ""},
            "selected API-key authentication",
        ),
    ],
)
def test_authentication_rejects_missing_or_invalid_configuration(
    tmp_path: Path, environment: dict[str, str], message: str
) -> None:
    with pytest.raises(harbor_mod.HarborAuthenticationError, match=message):
        harbor_mod.resolve_harbor_authentication("codex", environment, home=tmp_path)


def test_authentication_rejects_invalid_cached_file(tmp_path: Path) -> None:
    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text("not json", encoding="utf-8")

    with pytest.raises(harbor_mod.HarborAuthenticationError, match="invalid Codex auth file"):
        harbor_mod.resolve_harbor_authentication("codex", {}, home=tmp_path)


def test_other_agents_keep_harbor_authentication_behavior(tmp_path: Path) -> None:
    assert harbor_mod.resolve_harbor_authentication("claude-code", {}, home=tmp_path) is None


def test_harbor_environment_applies_authentication_changes() -> None:
    environment = harbor_subprocess_env(
        {"OPENAI_API_KEY": "old", "PATH": "/bin"},
        {"OPENAI_API_KEY": None, "CODEX_AUTH_JSON_PATH": "/auth.json"},
    )
    assert "OPENAI_API_KEY" not in environment
    assert environment["CODEX_AUTH_JSON_PATH"] == "/auth.json"
    assert environment["HARBOR_TELEMETRY"] == "0"


def test_invoke_harbor_passes_resolved_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str], *, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, environment=env, check=check)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("OPENAI_API_KEY", "competing-key")
    monkeypatch.setattr(subprocess, "run", fake_run)
    authentication = harbor_mod.HarborAuthentication(
        method="codex-auth-json",
        source="automatic-cache",
        description="cached Codex login",
        environment_changes={
            "CODEX_AUTH_JSON_PATH": "/auth.json",
            "OPENAI_API_KEY": None,
        },
    )

    assert harbor_mod.invoke_harbor(["harbor", "run"], authentication) == 0
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["CODEX_AUTH_JSON_PATH"] == "/auth.json"
    assert "OPENAI_API_KEY" not in environment
    assert environment["HARBOR_TELEMETRY"] == "0"


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
    authentication: harbor_mod.HarborAuthentication | None = None,
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
        authentication=authentication,
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
    assert record["schema_version"] == 3
    assert record["authentication"] is None
    assert record["max_concurrent_trials"] == 8
    # Version 3: what this job ran, the target it served, and the sample.
    assert record["repeats"] == 1
    assert record["repeats_target"] == 1
    assert record["sample"] is None
    assert record["toolkit_version"]
    assert record["harbor_version"].startswith("0.20.")
    assert record["items"][0]["item_id"] == "C1/HW1"
    assert record["items"][0]["course_id"] == "C1"
    assert record["items"][0]["assignment_id"] == "HW1"
    # The lineage fields are always serialized: a solve item carries
    # them all as null (KeyError here would mean a key went missing).
    assert record["items"][0]["submission_source"] is None
    assert record["items"][0]["student_id"] is None
    assert record["items"][0]["solve_job_name"] is None
    assert record["items"][0]["solve_trial_name"] is None
    assert record["items"][0]["context_config_name"] is None
    assert record["items"][0]["context_config_identity"] is None
    assert record["items"][0]["prior_trials"] is None


def test_run_record_authentication_excludes_credentials_and_paths(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    authentication = harbor_mod.HarborAuthentication(
        method="codex-auth-json",
        source="automatic-cache",
        description="cached Codex login",
        environment_changes={
            "CODEX_AUTH_JSON_PATH": "/secret/place/auth.json",
            "OPENAI_API_KEY": "api-secret",
        },
    )
    job_dir = write_job(
        tmp_path / "solving",
        "job",
        stage="solve",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i" * 64)],
        authentication=authentication,
    )

    record_text = (job_dir / "aat-run.json").read_text(encoding="utf-8")
    record = json.loads(record_text)
    assert record["authentication"] == {
        "method": "codex-auth-json",
        "source": "automatic-cache",
    }
    assert "api-secret" not in record_text
    assert "/secret/place" not in record_text


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


def test_done_trial_totals_pool_across_jobs(tmp_path: Path) -> None:
    """Target-count --repeats subtracts from these pooled counts."""
    config_path = write_config(tmp_path, SOLVE_TOML, "codex-high")
    jobs_root = tmp_path / "solving"
    first = write_job(
        jobs_root,
        "20260731T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i1"), make_item("t2", "C1/HW2", "i2")],
    )
    write_trial(first, "t1__abc1234", task_name="t1")
    write_trial(first, "t1__def5678", task_name="t1")
    write_trial(first, "t2__abc1234", task_name="t2", verified=False)
    second = write_job(
        jobs_root,
        "20260801T000000Z__codex-high__cccccccc",
        stage="solve",
        config_path=config_path,
        items=[make_item("t1", "C1/HW1", "i1")],
    )
    write_trial(second, "t1__aaa1234", task_name="t1")
    totals = harbor_mod.done_trial_totals(jobs_root, "solve")
    # HW1 pools 2 + 1 across the two jobs; HW2's only trial failed.
    assert totals == {("C1/HW1", "i1"): 3}


def write_grading_artifacts(trial_dir: Path) -> None:
    output_dir = trial_dir / "artifacts" / "app" / "grading_output"
    output_dir.mkdir(parents=True)
    (output_dir / "grading_result.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (output_dir / "justification.md").write_text("# J", encoding="utf-8")


def test_prior_gradings_by_key_collects_valid_gradings(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, GRADE_TOML, "codex-grader-high")
    grading_root = tmp_path / "grading"
    first = write_job(
        grading_root,
        "20260731T000000Z__codex-grader-high__cccccccc",
        stage="grade",
        config_path=config_path,
        items=[make_item("g1", "C1/stu1/HW1", "i1"), make_item("g2", "C1/stu2/HW1", "i1")],
    )
    # Two valid gradings of stu1 (one per job), plus one whose artifacts
    # are gone, plus an invalid grading (no base_pct) that never counts.
    write_grading_artifacts(write_trial(first, "g1__t2", task_name="g1", rewards=GRADED_REWARDS))
    write_trial(first, "g1__gone", task_name="g1", rewards=GRADED_REWARDS)
    write_trial(first, "g2__bad", task_name="g2", rewards={"reward": 0.0})
    second = write_job(
        grading_root,
        "20260801T000000Z__codex-grader-high__cccccccc",
        stage="grade",
        config_path=config_path,
        items=[make_item("g1", "C1/stu1/HW1", "i1")],
    )
    write_grading_artifacts(write_trial(second, "g1__t1", task_name="g1", rewards=GRADED_REWARDS))

    pool = harbor_mod.prior_gradings_by_key(grading_root)
    gradings = pool.by_key[("C1/stu1/HW1", "i1")]
    # Sorted by (job, trial) for deterministic round numbering.
    assert [(g.job_name[:8], g.trial_name) for g in gradings] == [
        ("20260731", "g1__t2"),
        ("20260801", "g1__t1"),
    ]
    for grading in gradings:
        assert grading.result_path.is_file()
        assert grading.justification_path.is_file()
    assert pool.missing_artifacts == {("C1/stu1/HW1", "i1"): 1}
    assert ("C1/stu2/HW1", "i1") not in pool.by_key


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

    selection = verified_solve_submissions(jobs_root, "codex-high")
    assert len(selection.submissions) == 1
    submission = selection.submissions[0]
    assert submission.course_id == "C1"
    assert submission.assignment_id == "HW1"
    assert submission.trial_name == "t1__abc1234"
    assert submission.item_id == f"{job_dir.name}/t1__abc1234"
    assert (submission.directory / "answer.md").read_text(encoding="utf-8") == "42"

    # The two non-gradable trials are reported, not silently dropped.
    assert {(skip.trial_name, skip.reason) for skip in selection.skipped} == {
        ("t1__zzz9999", "solve-failed"),
        ("t2__ghi9012", "empty-submission"),
    }
    empty = next(skip for skip in selection.skipped if skip.trial_name == "t2__ghi9012")
    assert (empty.course_id, empty.assignment_id) == ("C1", "HW2")
    assert empty.solve_job_name == job_dir.name

    other = verified_solve_submissions(jobs_root, "other-config")
    assert other.submissions == [] and other.skipped == []
    # Narrowing excludes trials from scope entirely: neither selected
    # nor reported as skipped.
    narrowed = verified_solve_submissions(jobs_root, "codex-high", course_id="C2")
    assert narrowed.submissions == [] and narrowed.skipped == []
    hw2 = verified_solve_submissions(jobs_root, "codex-high", assignment_id="HW2")
    assert hw2.submissions == []
    assert [skip.reason for skip in hw2.skipped] == ["empty-submission"]


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
    selection = verified_solve_submissions(jobs_root, "codex-grader-high")
    assert selection.submissions == [] and selection.skipped == []


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
