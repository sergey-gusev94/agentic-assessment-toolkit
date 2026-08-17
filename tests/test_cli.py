from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_assessment_toolkit import base_images as base_images_mod
from agentic_assessment_toolkit import cli
from agentic_assessment_toolkit import harbor as harbor_mod
from agentic_assessment_toolkit.config import CLAUDE_CODE_AGENT, CODEX_AGENT, environment_path
from agentic_assessment_toolkit.data_root import RUBRIC_ARCHIVE_DIRNAME
from agentic_assessment_toolkit.report import REPORT_FILENAMES
from tests.conftest import COURSE_ID, build_data_root
from tests.test_config import CLAUDE_SOLVE_TOML, GRADE_TOML, SOLVE_TOML, write_config
from tests.test_data_root import make_fake_toolkit_repo
from tests.test_harbor import GRADED_REWARDS, write_trial

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_harbor_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repository tests never invoke Harbor (AGENTS.md)."""

    authentication = harbor_mod.HarborAuthentication(
        method="codex-auth-json",
        source="automatic-cache",
        description="cached Codex login",
        environment_changes={"CODEX_AUTH_JSON_PATH": "/test/auth.json"},
    )

    def refuse(
        command: list[str], _authentication: harbor_mod.HarborAuthentication | None = None
    ) -> int:
        raise AssertionError(f"harbor invoked during tests: {command}")

    # cli calls harbor_mod.invoke_harbor via the shared module object,
    # so one patch covers both import sites. cli_harbor_version would
    # subprocess the harbor binary, so it is stubbed too.
    monkeypatch.setattr(harbor_mod, "invoke_harbor", refuse)
    monkeypatch.setattr(harbor_mod, "cli_harbor_version", lambda: "0.20.0-test")
    monkeypatch.setattr(
        harbor_mod,
        "resolve_harbor_authentication",
        lambda _agent: authentication,
    )


@pytest.fixture(autouse=True)
def base_image_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], str]]:
    """Repository tests never run docker: base-image preparation is
    recorded, not performed (its mechanics live in test_base_images.py).

    The agent is recorded with the flavors because it is what selects
    which template of each flavor is built — a launch that dropped it
    would build the wrong image and no assertion would notice.
    """
    calls: list[tuple[list[str], str]] = []

    def record(flavors: list[str], agent: str) -> None:
        calls.append((sorted(set(flavors)), agent))

    monkeypatch.setattr(base_images_mod, "ensure_base_images", record)
    return calls


@pytest.fixture
def solve_config(tmp_path: Path) -> Path:
    return write_config(tmp_path, SOLVE_TOML, "codex-high")


@pytest.fixture
def grade_config(tmp_path: Path) -> Path:
    return write_config(tmp_path, GRADE_TOML, "codex-grader-sol-high")


def run_cli(*args: str) -> int:
    return cli.main(list(args))


def solve_args(data_root: Path, config: Path, *extra: str) -> list[str]:
    return ["solve", "--data-root", str(data_root), "--config", str(config), *extra]


def grade_args(data_root: Path, config: Path, *extra: str) -> list[str]:
    return ["grade", "--data-root", str(data_root), "--config", str(config), *extra]


def job_dirs(root: Path, kind: str) -> list[Path]:
    base = root / kind
    return sorted(base.iterdir()) if base.is_dir() else []


def test_solve_dry_run_lists_items_without_writing(
    data_root: Path, solve_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert f"{COURSE_ID}/HW1" in out
    assert f"{COURSE_ID}/HW2" in out
    assert "would run 2 of 2 item(s)" in out
    assert job_dirs(data_root, "solving") == []


def test_offline_run_modes_do_not_resolve_authentication(
    data_root: Path,
    solve_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_agent: str) -> harbor_mod.HarborAuthentication:
        raise AssertionError("offline mode resolved authentication")

    monkeypatch.setattr(harbor_mod, "resolve_harbor_authentication", refuse)
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--dry-run",
            )
        )
        == 0
    )
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )


def test_missing_authentication_fails_before_job_or_task_creation(
    data_root: Path,
    solve_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_agent: str) -> harbor_mod.HarborAuthentication:
        raise harbor_mod.HarborAuthenticationError("login unavailable")

    monkeypatch.setattr(harbor_mod, "resolve_harbor_authentication", fail)

    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
            )
        )
        == 2
    )
    assert "error: login unavailable" in capsys.readouterr().err
    assert job_dirs(data_root, "solving") == []
    tasks_root = data_root / "tasks"
    assert not tasks_root.exists() or list(tasks_root.iterdir()) == []


def test_solve_materialize_only_writes_job_dir(
    data_root: Path, solve_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    jobs = job_dirs(data_root, "solving")
    assert len(jobs) == 1
    job_dir = jobs[0]
    assert "__codex-high__" in job_dir.name

    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    assert record["stage"] == "solve"
    assert record["executed"] is False
    assert record["authentication"] is None
    assert record["max_concurrent_trials"] == 8
    assert len(record["items"]) == 1
    assert record["items"][0]["item_id"] == f"{COURSE_ID}/HW1"
    # Lineage fields are always serialized; solve items leave all four null.
    assert record["items"][0]["submission_source"] is None
    assert record["items"][0]["student_id"] is None
    assert record["items"][0]["solve_job_name"] is None
    assert record["items"][0]["solve_trial_name"] is None

    job_config = json.loads((job_dir / "harbor-job.json").read_text(encoding="utf-8"))
    task_path = Path(job_config["tasks"][0]["path"])
    assert task_path.is_dir()
    assert (task_path / "task.toml").is_file()
    # Tasks live outside the Harbor job directory (resume safety).
    assert task_path.parent == data_root / "tasks" / job_dir.name
    # Flat layout: Harbor's job directory is the AAT job directory.
    assert job_config["jobs_dir"] == str(data_root / "solving")
    assert job_config["job_name"] == job_dir.name
    assert job_config["n_concurrent_trials"] == 8
    assert record["command"] == ["harbor", "run", "-c", str(job_dir / "harbor-job.json"), "--yes"]
    out = capsys.readouterr().out
    assert "materialize-only" in out
    # The base image is named for a later manual run, never built here.
    assert "base image aat-env-scientific-python:" in out


def test_solve_mounts_explicit_gurobi_license(
    data_root: Path, solve_config: Path, tmp_path: Path
) -> None:
    course_toml = data_root / "courses" / COURSE_ID / "course.toml"
    course_toml.write_text(
        course_toml.read_text(encoding="utf-8").replace(
            'environment = "scientific-python"', 'environment = "optimization"'
        ),
        encoding="utf-8",
    )
    license_file = tmp_path / "gurobi.lic"
    license_file.write_text("credential\n", encoding="utf-8")

    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--gurobi-license-file",
                str(license_file),
                "--materialize-only",
            )
        )
        == 0
    )
    job_config = json.loads(
        (job_dirs(data_root, "solving")[0] / "harbor-job.json").read_text(encoding="utf-8")
    )
    assert job_config["environment"]["mounts"] == [
        {
            "bind": {"create_host_path": False},
            "read_only": True,
            "source": str(license_file.resolve()),
            "target": "/opt/gurobi/gurobi.lic",
            "type": "bind",
        }
    ]


def test_solve_rejects_gurobi_license_for_non_optimization_task(
    data_root: Path, solve_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    license_file = tmp_path / "gurobi.lic"
    license_file.write_text("credential\n", encoding="utf-8")
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--gurobi-license-file",
                str(license_file),
                "--materialize-only",
            )
        )
        == 2
    )
    assert "resolves 'scientific-python'" in capsys.readouterr().err
    assert job_dirs(data_root, "solving") == []


def test_solve_uses_gurobi_license_environment_variable(
    data_root: Path,
    solve_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    course_toml = data_root / "courses" / COURSE_ID / "course.toml"
    course_toml.write_text(
        course_toml.read_text(encoding="utf-8").replace(
            'environment = "scientific-python"', 'environment = "optimization"'
        ),
        encoding="utf-8",
    )
    license_file = tmp_path / "gurobi.lic"
    license_file.write_text("credential\n", encoding="utf-8")
    monkeypatch.setenv(cli.GUROBI_LICENSE_ENV_VAR, str(license_file))

    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--dry-run",
            )
        )
        == 0
    )
    assert f"Gurobi license: read-only mount from {license_file}" in capsys.readouterr().out
    assert job_dirs(data_root, "solving") == []


def test_solve_rejects_missing_gurobi_license_file(
    data_root: Path, solve_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.lic"
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--gurobi-license-file",
                str(missing),
                "--dry-run",
            )
        )
        == 2
    )
    assert str(missing) in capsys.readouterr().err
    assert job_dirs(data_root, "solving") == []


def test_grade_max_concurrent_trials_is_forwarded_and_recorded(
    data_root: Path, grade_config: Path
) -> None:
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--course",
                COURSE_ID,
                "--max-concurrent-trials",
                "3",
                "--materialize-only",
            )
        )
        == 0
    )
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    job_config = json.loads((job_dir / "harbor-job.json").read_text(encoding="utf-8"))
    assert record["max_concurrent_trials"] == 3
    assert job_config["n_concurrent_trials"] == 3


def test_solve_doneness_skips_done_items(
    data_root: Path, solve_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    job_dir = job_dirs(data_root, "solving")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    task_dir_name = record["items"][0]["task_dir_name"]
    write_trial(job_dir, f"{task_dir_name[:32]}__abc1234", task_name=task_dir_name)

    capsys.readouterr()
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    assert "nothing to do" in capsys.readouterr().out
    assert len(job_dirs(data_root, "solving")) == 1

    # --force launches another job whose trials accumulate alongside.
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
                "--force",
            )
        )
        == 0
    )
    assert len(job_dirs(data_root, "solving")) == 2


def test_solve_selection_errors(data_root: Path, solve_config: Path) -> None:
    assert cli.main(solve_args(data_root, solve_config)) == 2
    assert cli.main(solve_args(data_root, solve_config, "--assignment", "HW1")) == 2
    assert cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--all")) == 2
    assert cli.main(solve_args(data_root, solve_config, "--course", "NOPE")) == 2
    assert (
        cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--assignment", "HW9"))
        == 2
    )


def test_stage_mismatch_is_an_error(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(solve_args(data_root, grade_config, "--course", COURSE_ID)) == 2
    assert "stage" in capsys.readouterr().err


def test_grade_student_submissions_materialize_only(data_root: Path, grade_config: Path) -> None:
    assert (
        cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only"))
        == 0
    )
    jobs = job_dirs(data_root, "grading")
    assert len(jobs) == 1
    record = json.loads((jobs[0] / "aat-run.json").read_text(encoding="utf-8"))
    assert record["stage"] == "grade"
    assert record["items"][0]["item_id"] == f"{COURSE_ID}/stu1/HW1"
    assert record["items"][0]["course_id"] == COURSE_ID
    assert record["items"][0]["assignment_id"] == "HW1"
    assert record["items"][0]["submission_source"] == "student"
    assert record["items"][0]["student_id"] == "stu1"
    assert record["items"][0]["solve_job_name"] is None
    assert record["items"][0]["solve_trial_name"] is None
    assert "rubric" in record["items"][0]["input_hashes"]
    task_dir = data_root / "tasks" / jobs[0].name / record["items"][0]["task_dir_name"]
    assert (task_dir / "environment" / "submission" / "answer.md").is_file()
    assert (task_dir / "environment" / "rubric.md").is_file()


def test_grade_submissions_path_selection(data_root: Path, grade_config: Path) -> None:
    path = data_root / "submissions" / COURSE_ID / "stu1"
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--submissions", str(path), "--materialize-only")
        )
        == 0
    )
    assert len(job_dirs(data_root, "grading")) == 1


def test_grade_submissions_path_outside_tree_is_an_error(
    data_root: Path, grade_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            grade_args(
                data_root, grade_config, "--submissions", str(tmp_path), "--materialize-only"
            )
        )
        == 2
    )
    assert "inside" in capsys.readouterr().err


def test_grade_source_selection_is_exclusive(data_root: Path, grade_config: Path) -> None:
    assert cli.main(grade_args(data_root, grade_config)) == 2
    assert (
        cli.main(grade_args(data_root, grade_config, "--from-solve", "x", "--submissions", "y"))
        == 2
    )


def test_grade_from_solve(data_root: Path, solve_config: Path, grade_config: Path) -> None:
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    solve_job = job_dirs(data_root, "solving")[0]
    record = json.loads((solve_job / "aat-run.json").read_text(encoding="utf-8"))
    task_dir_name = record["items"][0]["task_dir_name"]
    write_trial(
        solve_job,
        f"{task_dir_name[:32]}__abc1234",
        task_name=task_dir_name,
        submission_files={"answer.md": "slope 2"},
    )

    assert (
        cli.main(
            grade_args(data_root, grade_config, "--from-solve", "codex-high", "--materialize-only")
        )
        == 0
    )
    grading_jobs = job_dirs(data_root, "grading")
    assert len(grading_jobs) == 1
    grade_record = json.loads((grading_jobs[0] / "aat-run.json").read_text(encoding="utf-8"))
    assert len(grade_record["items"]) == 1
    item = grade_record["items"][0]
    assert item["item_id"].startswith(solve_job.name)
    # Lineage is recorded explicitly; the item_id carries no course/assignment.
    assert item["course_id"] == COURSE_ID
    assert item["assignment_id"] == "HW1"
    assert item["submission_source"] == "solve-trial"
    assert item["student_id"] is None
    assert item["solve_job_name"] == solve_job.name
    assert item["solve_trial_name"] == f"{task_dir_name[:32]}__abc1234"
    task_dir = data_root / "tasks" / grading_jobs[0].name / item["task_dir_name"]
    assert (task_dir / "environment" / "submission" / "answer.md").is_file()

    # Once a grading trial completes with a valid grading result, the
    # same solve trial is done and skipped.
    write_trial(
        grading_jobs[0],
        f"{item['task_dir_name'][:32]}__graded1",
        task_name=item["task_dir_name"],
        rewards=GRADED_REWARDS,
    )
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--from-solve", "codex-high", "--materialize-only")
        )
        == 0
    )
    assert len(job_dirs(data_root, "grading")) == 1


def test_grade_from_solve_reports_skipped_trials(
    data_root: Path,
    solve_config: Path,
    grade_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--materialize-only"))
        == 0
    )
    solve_job = job_dirs(data_root, "solving")[0]
    record = json.loads((solve_job / "aat-run.json").read_text(encoding="utf-8"))
    tasks = {item["assignment_id"]: item["task_dir_name"] for item in record["items"]}
    # HW1: one gradable trial plus one failed solve; HW2: only a
    # verified trial whose submission artifact is empty.
    write_trial(solve_job, "hw1__ok11111", task_name=tasks["HW1"], submission_files={"a.md": "x"})
    write_trial(solve_job, "hw1__bad2222", task_name=tasks["HW1"], verified=False)
    write_trial(solve_job, "hw2__nosub33", task_name=tasks["HW2"], submission_files=None)
    capsys.readouterr()

    assert (
        cli.main(
            grade_args(data_root, grade_config, "--from-solve", "codex-high", "--materialize-only")
        )
        == 0
    )
    out = capsys.readouterr().out
    # Every skip is a line; only HW2, with no gradable submission at
    # all, gets the warning — with --force, since its trial verified.
    assert f"skipping solve trial {solve_job.name}/hw1__bad2222" in out
    assert "solve failed before producing a submission" in out
    assert f"skipping solve trial {solve_job.name}/hw2__nosub33" in out
    assert "submission artifact is missing or empty" in out
    assert f"warning: {COURSE_ID}/HW1" not in out
    assert (
        f"warning: {COURSE_ID}/HW2 has no gradable submission under solve config 'codex-high'; "
        f"rerun: aat solve --config codex-high --course {COURSE_ID} --assignment HW2 --force"
    ) in out


def test_run_summary_names_failures_and_exits_nonzero(
    data_root: Path,
    solve_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_invoke(
        _command: list[str], _authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        job_dir = job_dirs(data_root, "solving")[0]
        record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
        tasks = {item["assignment_id"]: item["task_dir_name"] for item in record["items"]}
        write_trial(job_dir, "hw1__ok11111", task_name=tasks["HW1"])
        write_trial(job_dir, "hw2__bad2222", task_name=tasks["HW2"], verified=False)
        return 0

    monkeypatch.setattr(harbor_mod, "invoke_harbor", fake_invoke)
    exit_code = cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID))
    # Harbor exited zero — the job finished — but a requested item
    # failed, so the command fails loudly.
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "run summary: 2 item(s) requested, 1 verified, 1 failed" in out
    assert f"failed: {COURSE_ID}/HW2" in out
    assert f"rerun: aat solve --config {solve_config} --course {COURSE_ID} --assignment HW2" in out


def test_run_summary_reports_incomplete_repeats(
    data_root: Path,
    solve_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_invoke(
        _command: list[str], _authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        job_dir = job_dirs(data_root, "solving")[0]
        record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
        tasks = {item["assignment_id"]: item["task_dir_name"] for item in record["items"]}
        write_trial(job_dir, "hw1__ok11111", task_name=tasks["HW1"])
        write_trial(job_dir, "hw1__ok22222", task_name=tasks["HW1"])
        write_trial(job_dir, "hw2__ok11111", task_name=tasks["HW2"])
        write_trial(job_dir, "hw2__bad2222", task_name=tasks["HW2"], verified=False)
        return 0

    monkeypatch.setattr(harbor_mod, "invoke_harbor", fake_invoke)
    exit_code = cli.main(
        solve_args(data_root, solve_config, "--course", COURSE_ID, "--repeats", "2")
    )
    # HW2 verified — it is done — but it lost one of its two requested
    # trials, so the run must not exit clean; a plain rerun of the same
    # command launches exactly the missing trial (target semantics).
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "run summary: 2 item(s) requested, 2 verified, 0 failed" in out
    assert f"incomplete: {COURSE_ID}/HW2 ({COURSE_ID}/HW2): 1 of 2 valid trial(s)" in out
    assert f"incomplete: {COURSE_ID}/HW1" not in out
    assert "re-running the same command launches exactly the missing trials" in out


def test_launch_prepares_base_images_before_harbor(
    data_root: Path, solve_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[object] = []
    monkeypatch.setattr(
        base_images_mod,
        "ensure_base_images",
        lambda flavors, agent: events.append((sorted(set(flavors)), agent)),
    )

    def fake_invoke(
        _command: list[str], _authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        events.append("harbor")
        return 0

    monkeypatch.setattr(harbor_mod, "invoke_harbor", fake_invoke)
    cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID))
    # One preparation call covers every selected flavor and names the
    # config's agent, and it happens first: a trial cannot build FROM a
    # base image that does not exist.
    assert events == [(["data-science", "scientific-python"], CODEX_AGENT), "harbor"]


def test_configuration_change_rerun_is_explained(
    data_root: Path,
    tmp_path: Path,
    solve_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--materialize-only"))
        == 0
    )
    solve_job = job_dirs(data_root, "solving")[0]
    record = json.loads((solve_job / "aat-run.json").read_text(encoding="utf-8"))
    for index, item in enumerate(record["items"]):
        write_trial(solve_job, f"trial__ok{index}", task_name=item["task_dir_name"])

    # Same config again: everything is done, and nothing needs the note.
    capsys.readouterr()
    assert cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--dry-run")) == 0
    assert "note:" not in capsys.readouterr().out

    # A different config is a different identity: the items run again,
    # and the plan says why.
    changed = write_config(
        tmp_path,
        SOLVE_TOML.replace('reasoning_effort = "high"', 'reasoning_effort = "low"'),
        "codex-low",
    )
    assert cli.main(solve_args(data_root, changed, "--course", COURSE_ID, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "note: 2 of 2 item(s) have prior results under a different configuration" in out


def test_grade_from_solve_without_trials_is_noop(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--from-solve", "codex-high", "--materialize-only")
        )
        == 0
    )
    assert "nothing to do" in capsys.readouterr().out
    assert job_dirs(data_root, "grading") == []


def test_config_name_resolution(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    assert (
        run_cli(
            "solve",
            "--data-root",
            str(data_root),
            "--config",
            "codex-high",
            "--course",
            COURSE_ID,
            "--dry-run",
        )
        == 0
    )
    assert "would run" in capsys.readouterr().out


def test_missing_default_data_root_is_an_error(
    solve_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AAT_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert run_cli("solve", "--config", str(solve_config), "--all") == 2
    assert f"default data root {tmp_path / 'aat-data'} does not exist" in capsys.readouterr().err


def test_doneness_is_per_item_not_per_identity_solve(
    data_root: Path, solve_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two assignments sharing one environment flavor must not share doneness."""
    sidecar = data_root / "courses" / COURSE_ID / "assignments" / "HW2.toml"
    sidecar.write_text('environment = "scientific-python"\n', encoding="utf-8")

    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    job_dir = job_dirs(data_root, "solving")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    task_dir_name = record["items"][0]["task_dir_name"]
    write_trial(job_dir, f"{task_dir_name[:32]}__abc1234", task_name=task_dir_name)

    capsys.readouterr()
    assert cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert f"[complete] {COURSE_ID}/HW1" in out
    assert f"[pending ] {COURSE_ID}/HW2" in out
    assert "would run 1 of 2 item(s)" in out


def test_doneness_is_per_item_not_per_identity_grading(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Grading one student's submission must not mark other students done."""
    import shutil

    shutil.copytree(
        data_root / "submissions" / COURSE_ID / "stu1" / "HW1",
        data_root / "submissions" / COURSE_ID / "stu2" / "HW1",
    )
    assert (
        cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only"))
        == 0
    )
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    assert len(record["items"]) == 2
    stu1_item = next(i for i in record["items"] if "stu1" in i["item_id"])
    write_trial(
        job_dir,
        f"{stu1_item['task_dir_name'][:32]}__graded1",
        task_name=stu1_item["task_dir_name"],
        rewards=GRADED_REWARDS,
    )

    capsys.readouterr()
    assert cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert f"[complete] {COURSE_ID}/stu1/HW1" in out
    assert f"[pending ] {COURSE_ID}/stu2/HW1" in out


def test_missing_named_rubric_is_an_error(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A configured non-default rubric must exist like any other rubric."""
    config = write_config(tmp_path, GRADE_TOML + 'rubric = "strict-v2"\n', "codex-grader-strict")
    assert cli.main(grade_args(data_root, config, "--course", COURSE_ID, "--materialize-only")) == 2
    assert "strict-v2" in capsys.readouterr().err


def test_grade_without_rubric_is_an_error(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An assignment with no rubric fails at plan time with the authoring fix."""
    import shutil

    shutil.copytree(
        data_root / "submissions" / COURSE_ID / "stu1" / "HW1",
        data_root / "submissions" / COURSE_ID / "stu1" / "HW2",
    )
    assert (
        cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only"))
        == 2
    )
    err = capsys.readouterr().err
    assert f"courses/{COURSE_ID}/rubrics/HW2/default.md" in err
    assert "author" in err
    # The plan fails before any job directory is created.
    assert job_dirs(data_root, "grading") == []


def test_grade_unparseable_rubric_fails_at_plan_time(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rubric = data_root / "courses" / COURSE_ID / "rubrics" / "HW1" / "default.md"
    rubric.write_text("# Rubric\n\n- `broken (1 point): no closing backtick.\n", encoding="utf-8")
    assert (
        cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only"))
        == 2
    )
    assert "line 3" in capsys.readouterr().err
    assert job_dirs(data_root, "grading") == []


def test_grading_flavor_is_rejected_for_solve(
    data_root: Path, solve_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sidecar = data_root / "courses" / COURSE_ID / "assignments" / "HW1.toml"
    sidecar.write_text('environment = "grading"\n', encoding="utf-8")
    assert (
        cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--assignment", "HW1"))
        == 2
    )
    assert "reserved for grading" in capsys.readouterr().err


def test_grade_invalid_result_is_regraded(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A contract-violating grading trial leaves the item pending."""
    assert (
        cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only"))
        == 0
    )
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    item = record["items"][0]
    write_trial(
        job_dir,
        f"{item['task_dir_name'][:32]}__graded1",
        task_name=item["task_dir_name"],
        rewards={"reward": 0.0},  # violation: no base_pct, no valid result
    )

    capsys.readouterr()
    assert cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--dry-run")) == 0
    assert f"[pending ] {COURSE_ID}/stu1/HW1" in capsys.readouterr().out


def test_grade_from_solve_narrowed_by_course_is_accepted(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--from-solve",
                "codex-high",
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    assert "nothing to do" in capsys.readouterr().out
    # --from-solve is narrowed only by --course/--assignment.
    assert cli.main(grade_args(data_root, grade_config, "--from-solve", "x", "--all")) == 2


def test_solve_launch_propagates_harbor_exit_code(
    data_root: Path, solve_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoked: list[list[str]] = []

    invoked_authentication: list[harbor_mod.HarborAuthentication | None] = []

    def fake_invoke(
        command: list[str], authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        invoked.append(command)
        invoked_authentication.append(authentication)
        return 7

    monkeypatch.setattr(harbor_mod, "invoke_harbor", fake_invoke)
    assert (
        cli.main(solve_args(data_root, solve_config, "--course", COURSE_ID, "--assignment", "HW1"))
        == 7
    )
    job_dir = job_dirs(data_root, "solving")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    assert record["executed"] is True
    assert record["harbor_version"] == "0.20.0-test"
    assert record["harbor_version_source"] == "cli"
    assert record["authentication"] == {
        "method": "codex-auth-json",
        "source": "automatic-cache",
    }
    assert invoked == [record["command"]]
    assert invoked_authentication[0] is not None


def test_solve_all_and_grade_all(
    data_root: Path, solve_config: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(solve_args(data_root, solve_config, "--all", "--dry-run")) == 0
    out = capsys.readouterr().out
    assert f"{COURSE_ID}/HW1" in out and f"{COURSE_ID}/HW2" in out

    assert cli.main(grade_args(data_root, grade_config, "--all", "--materialize-only")) == 0
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    assert [i["item_id"] for i in record["items"]] == [f"{COURSE_ID}/stu1/HW1"]


def test_report_command_writes_report_and_prints_directory(
    data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli("report", "--data-root", str(data_root)) == 0
    out = capsys.readouterr().out
    assert out.startswith("report directory: ")
    report_dir = Path(out.removeprefix("report directory: ").strip())
    assert report_dir.is_dir()
    assert report_dir.parent == data_root / "analysis"
    for name in REPORT_FILENAMES:
        assert (report_dir / name).is_file(), name
    provenance = json.loads((report_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["seed"] == 42  # metrics.DEFAULT_SEED is the CLI default
    assert provenance["filters"] == {"courses": None, "assignments": None, "configs": None}


def test_report_out_inside_toolkit_repo_is_refused(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A fake toolkit clone under tmp_path: if the refusal ever
    # regresses, the report lands in tmp_path, never in the real
    # repository tree.
    destination = make_fake_toolkit_repo(tmp_path) / "tmp-report-out"
    assert run_cli("report", "--data-root", str(data_root), "--out", str(destination)) == 2
    assert "inside the toolkit repository" in capsys.readouterr().err
    assert not destination.exists()


def test_report_empty_course_value_still_filters(
    data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty --course (typically an unset shell variable) never means "all"."""
    assert run_cli("report", "--data-root", str(data_root), "--course", "") == 0
    out = capsys.readouterr().out
    report_dir = Path(out.removeprefix("report directory: ").strip())
    provenance = json.loads((report_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["filters"]["courses"] == [""]


def test_report_out_elsewhere_is_honored_with_filters(data_root: Path, tmp_path: Path) -> None:
    destination = tmp_path / "elsewhere"
    assert (
        run_cli(
            "report",
            "--data-root",
            str(data_root),
            "--out",
            str(destination),
            "--course",
            COURSE_ID,
            "--assignment",
            "HW1",
            "--config",
            "codex-high",
            "--config",
            "codex-grader-sol-high",
            "--seed",
            "7",
        )
        == 0
    )
    report_dirs = list(destination.iterdir())
    assert len(report_dirs) == 1
    assert report_dirs[0].name.endswith("__report")
    provenance = json.loads((report_dirs[0] / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["seed"] == 7
    assert provenance["filters"] == {
        "courses": [COURSE_ID],
        "assignments": ["HW1"],
        "configs": ["codex-high", "codex-grader-sol-high"],
    }


def add_student(data_root: Path, student_id: str, assignment_id: str = "HW1") -> None:
    import shutil

    shutil.copytree(
        data_root / "submissions" / COURSE_ID / "stu1" / "HW1",
        data_root / "submissions" / COURSE_ID / student_id / assignment_id,
    )


def sample_order(student_ids: list[str]) -> list[str]:
    """The hash-prefix order the CLI must reproduce, computed independently."""
    import hashlib

    return sorted(
        student_ids,
        key=lambda sid: (hashlib.sha256(f"{COURSE_ID}/{sid}".encode()).hexdigest(), sid),
    )


def test_grade_sample_is_a_deterministic_hash_prefix(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for student_id in ("stu2", "stu3", "stu4", "_reference"):
        add_student(data_root, student_id)
    expected = sample_order(["stu1", "stu2", "stu3", "stu4"])[:2]

    assert (
        cli.main(
            grade_args(data_root, grade_config, "--course", COURSE_ID, "--sample", "2", "--dry-run")
        )
        == 0
    )
    out = capsys.readouterr().out
    for student_id in expected:
        assert f"{COURSE_ID}/{student_id}/HW1" in out
    for student_id in {"stu1", "stu2", "stu3", "stu4"} - set(expected):
        assert f"{COURSE_ID}/{student_id}/HW1" not in out
    # Pseudo-students are excluded from the frame and the selection.
    assert f"{COURSE_ID}/_reference/HW1" not in out
    assert f"sample: {COURSE_ID}/HW1: 2 of 4 submitted student(s)" in out
    assert "excluded 1 pseudo-student submission(s)" in out

    # The prefix property: sample 3 is sample 2 plus one more student.
    capsys.readouterr()
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--course", COURSE_ID, "--sample", "3", "--dry-run")
        )
        == 0
    )
    wider = capsys.readouterr().out
    for student_id in expected:
        assert f"{COURSE_ID}/{student_id}/HW1" in wider


def test_grade_sample_recorded_in_run_record(data_root: Path, grade_config: Path) -> None:
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--course",
                COURSE_ID,
                "--sample",
                "1",
                "--materialize-only",
            )
        )
        == 0
    )
    record = json.loads(
        (job_dirs(data_root, "grading")[0] / "aat-run.json").read_text(encoding="utf-8")
    )
    assert record["sample"] == 1
    assert len(record["items"]) == 1


def test_grade_sample_rejected_with_from_solve(data_root: Path, grade_config: Path) -> None:
    assert (
        cli.main(grade_args(data_root, grade_config, "--from-solve", "codex-high", "--sample", "3"))
        == 2
    )


def test_repeats_target_launches_only_the_deficit(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--submissions", submission, "--materialize-only")
        )
        == 0
    )
    first = job_dirs(data_root, "grading")[0]
    record = json.loads((first / "aat-run.json").read_text(encoding="utf-8"))
    write_trial(
        first,
        "graded__t1",
        task_name=record["items"][0]["task_dir_name"],
        rewards=GRADED_REWARDS,
    )

    capsys.readouterr()
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--submissions",
                submission,
                "--repeats",
                "3",
                "--materialize-only",
            )
        )
        == 0
    )
    second = [d for d in job_dirs(data_root, "grading") if d != first]
    assert len(second) == 1
    harbor_job = json.loads((second[0] / "harbor-job.json").read_text(encoding="utf-8"))
    # One valid grading exists, so the target of 3 launches exactly 2.
    assert harbor_job["n_attempts"] == 2
    new_record = json.loads((second[0] / "aat-run.json").read_text(encoding="utf-8"))
    assert new_record["repeats"] == 2
    assert new_record["repeats_target"] == 3
    assert "materialized 1 task(s), 2 trial(s) per item" in capsys.readouterr().out


def test_repeats_target_at_or_above_target_does_nothing(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--submissions", submission, "--materialize-only")
        )
        == 0
    )
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    task_name = record["items"][0]["task_dir_name"]
    write_trial(job_dir, "graded__t1", task_name=task_name, rewards=GRADED_REWARDS)
    write_trial(job_dir, "graded__t2", task_name=task_name, rewards=GRADED_REWARDS)

    capsys.readouterr()
    assert (
        cli.main(grade_args(data_root, grade_config, "--submissions", submission, "--repeats", "2"))
        == 0
    )
    assert "nothing to do: 1 item(s) already at the target of 2 valid trial(s)" in (
        capsys.readouterr().out
    )
    assert len(job_dirs(data_root, "grading")) == 1  # no new job


def test_repeats_deficits_group_into_one_job_each(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_student(data_root, "stu2")
    assert (
        cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only"))
        == 0
    )
    first = job_dirs(data_root, "grading")[0]
    record = json.loads((first / "aat-run.json").read_text(encoding="utf-8"))
    stu1_item = next(i for i in record["items"] if "stu1" in i["item_id"])
    write_trial(first, "graded__t1", task_name=stu1_item["task_dir_name"], rewards=GRADED_REWARDS)

    capsys.readouterr()
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--course",
                COURSE_ID,
                "--repeats",
                "2",
                "--dry-run",
            )
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"run  [partial ] {COURSE_ID}/stu1/HW1 (1 of 2 valid trial(s))" in out
    assert f"run  [pending ] {COURSE_ID}/stu2/HW1 (0 of 2 valid trial(s))" in out
    assert "would run 2 of 2 item(s) across 2 job(s) (one per deficit)" in out

    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--course",
                COURSE_ID,
                "--repeats",
                "2",
                "--materialize-only",
            )
        )
        == 0
    )
    new_jobs = [d for d in job_dirs(data_root, "grading") if d != first]
    assert len(new_jobs) == 2
    by_attempts = {}
    for job_dir in new_jobs:
        harbor_job = json.loads((job_dir / "harbor-job.json").read_text(encoding="utf-8"))
        new_record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
        assert new_record["repeats"] == harbor_job["n_attempts"]
        assert new_record["repeats_target"] == 2
        by_attempts[harbor_job["n_attempts"]] = [i["item_id"] for i in new_record["items"]]
    # stu1 needs one more trial, stu2 needs two; each deficit is one job.
    assert by_attempts == {
        1: [f"{COURSE_ID}/stu1/HW1"],
        2: [f"{COURSE_ID}/stu2/HW1"],
    }


def test_force_adds_repeats_in_a_single_job(data_root: Path, grade_config: Path) -> None:
    add_student(data_root, "stu2")
    assert (
        cli.main(grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only"))
        == 0
    )
    first = job_dirs(data_root, "grading")[0]
    record = json.loads((first / "aat-run.json").read_text(encoding="utf-8"))
    stu1_item = next(i for i in record["items"] if "stu1" in i["item_id"])
    write_trial(first, "graded__t1", task_name=stu1_item["task_dir_name"], rewards=GRADED_REWARDS)

    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--course",
                COURSE_ID,
                "--repeats",
                "2",
                "--force",
                "--materialize-only",
            )
        )
        == 0
    )
    new_jobs = [d for d in job_dirs(data_root, "grading") if d != first]
    assert len(new_jobs) == 1
    harbor_job = json.loads((new_jobs[0] / "harbor-job.json").read_text(encoding="utf-8"))
    new_record = json.loads((new_jobs[0] / "aat-run.json").read_text(encoding="utf-8"))
    # --force adds 2 more to every item in scope, done or not.
    assert harbor_job["n_attempts"] == 2
    assert len(new_record["items"]) == 2


def seed_valid_grading(data_root: Path, grade_config: Path, submission: str) -> Path:
    """Materialize a grading job for one submission and store 1 valid trial."""
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--submissions", submission, "--materialize-only")
        )
        == 0
    )
    job_dir = job_dirs(data_root, "grading")[-1]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    write_trial(
        job_dir,
        "seeded__t1",
        task_name=record["items"][0]["task_dir_name"],
        rewards=GRADED_REWARDS,
    )
    return job_dir


def test_run_summary_counts_pooled_trials(
    data_root: Path,
    grade_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary's totals include valid trials pooled from earlier jobs."""
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    seed_valid_grading(data_root, grade_config, submission)

    def fake_invoke(
        _command: list[str], _authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        job_dir = job_dirs(data_root, "grading")[-1]
        record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
        # The deficit job asked for 2 trials; only one produces a grade.
        write_trial(
            job_dir,
            "new__t1",
            task_name=record["items"][0]["task_dir_name"],
            rewards=GRADED_REWARDS,
        )
        return 0

    monkeypatch.setattr(harbor_mod, "invoke_harbor", fake_invoke)
    capsys.readouterr()
    exit_code = cli.main(
        grade_args(data_root, grade_config, "--submissions", submission, "--repeats", "3")
    )
    assert exit_code == 1
    out = capsys.readouterr().out
    # 1 pooled + 1 new of a target of 3: graded but incomplete — never
    # "failed", and never "1 of 3" (the pooled trial must count).
    assert "run summary: 1 item(s) requested, 1 graded, 0 failed" in out
    assert f"incomplete: {COURSE_ID}/HW1 ({COURSE_ID}/stu1/HW1): 2 of 3 valid trial(s)" in out
    assert "re-running the same command launches exactly the missing trials" in out


def test_force_run_summary_targets_existing_plus_added(
    data_root: Path,
    grade_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    seed_valid_grading(data_root, grade_config, submission)

    def fake_invoke(
        _command: list[str], _authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        job_dir = job_dirs(data_root, "grading")[-1]
        record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
        write_trial(
            job_dir,
            "new__t1",
            task_name=record["items"][0]["task_dir_name"],
            rewards=GRADED_REWARDS,
        )
        return 0

    monkeypatch.setattr(harbor_mod, "invoke_harbor", fake_invoke)
    capsys.readouterr()
    exit_code = cli.main(
        grade_args(
            data_root, grade_config, "--submissions", submission, "--force", "--repeats", "2"
        )
    )
    # --force asked for 2 more on top of 1 existing; only 1 arrived.
    assert exit_code == 1
    out = capsys.readouterr().out
    assert f"incomplete: {COURSE_ID}/HW1 ({COURSE_ID}/stu1/HW1): 2 of 3 valid trial(s)" in out
    assert "--force adds trials rather than ensuring a target" in out


def test_multi_job_launch_aborts_on_nonzero_harbor_exit(
    data_root: Path,
    grade_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A harbor failure stops the remaining deficit jobs, loudly."""
    add_student(data_root, "stu2")
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    seed_valid_grading(data_root, grade_config, submission)

    invocations: list[list[str]] = []

    def failing_invoke(
        command: list[str], _authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        invocations.append(command)
        return 5

    monkeypatch.setattr(harbor_mod, "invoke_harbor", failing_invoke)
    capsys.readouterr()
    exit_code = cli.main(
        grade_args(data_root, grade_config, "--course", COURSE_ID, "--repeats", "2")
    )
    assert exit_code == 5
    assert len(invocations) == 1
    out = capsys.readouterr().out
    assert "harbor exited 5; not launching the remaining 1 job(s)" in out
    # Provenance never lies: the launched job's record says executed,
    # the never-launched job's record says not.
    new_jobs = job_dirs(data_root, "grading")[1:]
    executed = {
        json.loads((job / "aat-run.json").read_text(encoding="utf-8"))["executed"]
        for job in new_jobs
    }
    assert executed == {True, False}


def test_multi_job_launch_runs_every_deficit_group(
    data_root: Path,
    grade_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    add_student(data_root, "stu2")
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    seed_valid_grading(data_root, grade_config, submission)

    invocations: list[list[str]] = []

    def fake_invoke(
        command: list[str], _authentication: harbor_mod.HarborAuthentication | None
    ) -> int:
        invocations.append(command)
        return 0

    monkeypatch.setattr(harbor_mod, "invoke_harbor", fake_invoke)
    capsys.readouterr()
    exit_code = cli.main(
        grade_args(data_root, grade_config, "--course", COURSE_ID, "--repeats", "2")
    )
    # Both jobs launch (largest deficit first); no trials appear, so
    # stu2 is failed and stu1 (1 pooled trial) is incomplete.
    assert len(invocations) == 2
    attempts = [
        json.loads(
            Path(command[3]).read_text(encoding="utf-8")  # harbor run -c <path> --yes
        )["n_attempts"]
        for command in invocations
    ]
    assert attempts == [2, 1]
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "run summary: 2 item(s) requested, 1 graded, 1 failed" in out
    assert f"incomplete: {COURSE_ID}/HW1 ({COURSE_ID}/stu1/HW1)" in out


JUDGE_TOML_CLI = """\
stage = "grade"
agent = "codex"
model = "openai/gpt-5.6-sol"
prompt = "judge"
judge = true
"""


def write_grading_artifacts(trial_dir: Path) -> None:
    output_dir = trial_dir / "artifacts" / "app" / "grading_output"
    output_dir.mkdir(parents=True)
    (output_dir / "grading_result.json").write_text(
        '{"schema_version": 1, "criteria": []}', encoding="utf-8"
    )
    (output_dir / "justification.md").write_text("# Round justification", encoding="utf-8")


def grade_one_initial(data_root: Path, grade_config: Path, trial_name: str) -> Path:
    """One valid initial grading of stu1/HW1 with stored artifacts."""
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    existing = set(job_dirs(data_root, "grading"))
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--submissions",
                submission,
                "--force",
                "--materialize-only",
            )
        )
        == 0
    )
    job_dir = next(d for d in job_dirs(data_root, "grading") if d not in existing)
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    trial_dir = write_trial(
        job_dir,
        trial_name,
        task_name=record["items"][0]["task_dir_name"],
        rewards=GRADED_REWARDS,
    )
    write_grading_artifacts(trial_dir)
    return job_dir


def test_judge_flow(
    data_root: Path,
    grade_config: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.test_config import write_config

    judge_config = write_config(tmp_path, JUDGE_TOML_CLI, "codex-judge")
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    grade_one_initial(data_root, grade_config, "graded__t1")

    # One prior grading is short of --gradings 2: skipped loudly
    # with the exact top-up command, no judge job is created, and the
    # run exits nonzero — it did not deliver what was asked.
    capsys.readouterr()
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "2",
                "--materialize-only",
            )
        )
        == 1
    )
    out = capsys.readouterr().out
    assert (
        f"skipping {COURSE_ID}/stu1/HW1: 1 of 2 required prior grading(s) "
        f"under config 'codex-grader-sol-high'" in out
    )
    assert f"--submissions {submission} --repeats 2" in out
    assert "nothing to do: 0 item(s)" in out
    assert "judge: 1 item(s) skipped with fewer than 2 usable prior grading(s)" in out

    # With three prior gradings and --gradings 2 the judge task
    # materializes over exactly the earliest two — numbered rounds, the
    # feedback declaration, identity and lineage recorded; the third
    # grading is not presented.
    grade_one_initial(data_root, grade_config, "graded__t2")
    grade_one_initial(data_root, grade_config, "graded__t3")
    n_before = len(job_dirs(data_root, "grading"))
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "2",
                "--materialize-only",
            )
        )
        == 0
    )
    judge_jobs = job_dirs(data_root, "grading")[n_before:]
    assert len(judge_jobs) == 1
    record = json.loads((judge_jobs[0] / "aat-run.json").read_text(encoding="utf-8"))
    item = record["items"][0]
    assert record["config"]["judge"] is True
    assert item["context_config_name"] == "codex-grader-sol-high"
    assert [ref["trial_name"] for ref in item["prior_trials"]] == ["graded__t1", "graded__t2"]
    assert "prior_gradings" in item["input_hashes"]
    task_dir = data_root / "tasks" / judge_jobs[0].name / item["task_dir_name"]
    assert (task_dir / "environment" / "prior_gradings" / "01" / "grading_result.json").is_file()
    assert (task_dir / "environment" / "prior_gradings" / "02" / "justification.md").is_file()
    assert not (task_dir / "environment" / "prior_gradings" / "03").exists()
    assert json.loads((task_dir / "tests" / "required_files.json").read_text(encoding="utf-8")) == [
        "feedback.md"
    ]

    # The item is judged over the earliest two gradings, and the
    # selection is stable: a fourth initial grading changes nothing, so
    # the item stays complete. Judging over more evidence means raising
    # --gradings, which changes the item's inputs and re-judges.
    write_trial(
        judge_jobs[0],
        "judged__t1",
        task_name=item["task_dir_name"],
        rewards=GRADED_REWARDS,
    )
    grade_one_initial(data_root, grade_config, "graded__t4")
    capsys.readouterr()
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "2",
                "--dry-run",
            )
        )
        == 0
    )
    assert "[complete]" in capsys.readouterr().out
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "3",
                "--dry-run",
            )
        )
        == 0
    )
    assert "[pending ]" in capsys.readouterr().out


def test_judge_from_solve_flow(
    data_root: Path,
    solve_config: Path,
    grade_config: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The judge composes with solve-derived submissions (any source)."""
    from tests.test_config import write_config

    judge_config = write_config(tmp_path, JUDGE_TOML_CLI, "codex-judge")
    # A verified solve trial with a submission artifact...
    assert (
        cli.main(
            solve_args(
                data_root,
                solve_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    solve_job = job_dirs(data_root, "solving")[0]
    solve_record = json.loads((solve_job / "aat-run.json").read_text(encoding="utf-8"))
    write_trial(
        solve_job,
        "solved__t1",
        task_name=solve_record["items"][0]["task_dir_name"],
        submission_files={"answer.md": "slope = 2"},
    )
    # ...graded once by the initial config, with stored artifacts...
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--from-solve", "codex-high", "--materialize-only")
        )
        == 0
    )
    grading_job = job_dirs(data_root, "grading")[0]
    grading_record = json.loads((grading_job / "aat-run.json").read_text(encoding="utf-8"))
    trial_dir = write_trial(
        grading_job,
        "graded__t1",
        task_name=grading_record["items"][0]["task_dir_name"],
        rewards=GRADED_REWARDS,
    )
    write_grading_artifacts(trial_dir)

    # ...is short of --gradings 2: the top-up command names the
    # solve source, not a student folder.
    capsys.readouterr()
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--from-solve",
                "codex-high",
                "--context-from",
                str(grade_config),
                "--gradings",
                "2",
                "--materialize-only",
            )
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "--from-solve codex-high" in out.split("top up:")[1]

    # And judges cleanly at --gradings 1: the task presents the
    # round, and the item keeps the solve-trial lineage.
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--from-solve",
                "codex-high",
                "--context-from",
                str(grade_config),
                "--gradings",
                "1",
                "--materialize-only",
            )
        )
        == 0
    )
    judge_job = job_dirs(data_root, "grading")[-1]
    record = json.loads((judge_job / "aat-run.json").read_text(encoding="utf-8"))
    item = record["items"][0]
    assert item["item_id"] == f"{solve_job.name}/solved__t1"
    assert item["submission_source"] == "solve-trial"
    assert [ref["trial_name"] for ref in item["prior_trials"]] == ["graded__t1"]
    task_dir = data_root / "tasks" / judge_job.name / item["task_dir_name"]
    assert (task_dir / "environment" / "prior_gradings" / "01" / "grading_result.json").is_file()


def test_top_up_command_uses_force_when_artifacts_are_missing(
    data_root: Path,
    grade_config: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doneness counts artifact-less valid trials, so the plain target
    would launch nothing; the hint must switch to --force."""
    from tests.test_config import write_config

    judge_config = write_config(tmp_path, JUDGE_TOML_CLI, "codex-judge")
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    grade_one_initial(data_root, grade_config, "graded__t1")
    # A second valid grading whose artifacts are gone: valid for
    # doneness, unusable for the judge.
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    write_trial(
        job_dir,
        "graded__gone",
        task_name=record["items"][0]["task_dir_name"],
        rewards=GRADED_REWARDS,
    )

    capsys.readouterr()
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "2",
                "--materialize-only",
            )
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "1 of 2 required prior grading(s) (1 more unusable" in out
    assert f"--submissions {submission} --force --repeats 1" in out


def test_judge_rubric_must_match_context_rubric(
    data_root: Path, grade_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_config import write_config

    judge_config = write_config(
        tmp_path, JUDGE_TOML_CLI + 'rubric = "strict"\n', "codex-judge-strict"
    )
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "1",
            )
        )
        == 2
    )
    assert "must grade against the same rubric" in capsys.readouterr().err


def test_grade_sample_with_course_level_submissions_path(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for student_id in ("stu2", "stu3"):
        add_student(data_root, student_id)
    expected = sample_order(["stu1", "stu2", "stu3"])[:1]
    course_path = str(data_root / "submissions" / COURSE_ID)
    assert (
        cli.main(
            grade_args(
                data_root, grade_config, "--submissions", course_path, "--sample", "1", "--dry-run"
            )
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"{COURSE_ID}/{expected[0]}/HW1" in out
    assert "would run 1 of 1 item(s)" in out

    # Below the course level the frame is one student, not a panel.
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--submissions",
                str(data_root / "submissions" / COURSE_ID / "stu1"),
                "--sample",
                "1",
            )
        )
        == 2
    )
    assert "course-wide frame" in capsys.readouterr().err


def test_judge_flag_validation(
    data_root: Path, grade_config: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_config import write_config

    judge_config = write_config(tmp_path, JUDGE_TOML_CLI, "codex-judge")
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")

    # A judge config needs --context-from and --gradings.
    assert cli.main(grade_args(data_root, judge_config, "--submissions", submission)) == 2
    assert "--context-from" in capsys.readouterr().err
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
            )
        )
        == 2
    )
    assert "--gradings" in capsys.readouterr().err

    # --context-from needs a judge config; --gradings needs --context-from.
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "2",
            )
        )
        == 2
    )
    assert "does not set judge = true" in capsys.readouterr().err
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--submissions", submission, "--gradings", "2")
        )
        == 2
    )
    assert "only valid with --context-from" in capsys.readouterr().err

    # The context must be the initial config, never another judge.
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(judge_config),
                "--gradings",
                "2",
            )
        )
        == 2
    )
    assert "itself a final-judge config" in capsys.readouterr().err


def test_grade_submissions_three_level_path_and_depth_limit(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = data_root / "submissions" / COURSE_ID / "stu1" / "HW1"
    assert (
        cli.main(
            grade_args(data_root, grade_config, "--submissions", str(path), "--materialize-only")
        )
        == 0
    )
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    assert [i["item_id"] for i in record["items"]] == [f"{COURSE_ID}/stu1/HW1"]

    capsys.readouterr()
    too_deep = path / "extra"
    assert (
        cli.main(
            grade_args(
                data_root, grade_config, "--submissions", str(too_deep), "--materialize-only"
            )
        )
        == 2
    )
    assert "three levels" in capsys.readouterr().err


def test_grade_presents_rubric_source_when_present(data_root: Path, grade_config: Path) -> None:
    source = data_root / "courses" / COURSE_ID / "rubrics" / "HW1" / "source"
    source.mkdir()
    (source / "rubric.pdf").write_text("professor rubric\n", encoding="utf-8")
    assert (
        cli.main(
            grade_args(
                data_root,
                grade_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    item = record["items"][0]
    assert "rubric_source" in item["input_hashes"]
    task_dir = data_root / "tasks" / job_dir.name / item["task_dir_name"]
    assert (task_dir / "environment" / "rubric_source" / "rubric.pdf").is_file()

    # The source directory is part of the frozen judge: an identity
    # computed without it must differ.
    plain_root = build_data_root(job_dir.parent.parent / "plain")
    assert (
        cli.main(
            grade_args(
                plain_root, grade_config, "--course", COURSE_ID, "--assignment", "HW1", "--dry-run"
            )
        )
        == 0
    )
    plain_jobs = job_dirs(plain_root, "grading")
    assert plain_jobs == []  # dry run writes nothing; compare via a real record
    assert (
        cli.main(
            grade_args(
                plain_root,
                grade_config,
                "--course",
                COURSE_ID,
                "--assignment",
                "HW1",
                "--materialize-only",
            )
        )
        == 0
    )
    plain_record = json.loads(
        (job_dirs(plain_root, "grading")[0] / "aat-run.json").read_text(encoding="utf-8")
    )
    assert plain_record["items"][0]["item_identity"] != item["item_identity"]


CLAUDE_GRADE_TOML_CLI = """\
stage = "grade"
agent = "claude-code"
model = "anthropic/claude-opus-5"
reasoning_effort = "high"
prompt = "grader"
"""

CLAUDE_JUDGE_TOML_CLI = """\
stage = "grade"
agent = "claude-code"
model = "anthropic/claude-opus-5"
reasoning_effort = "high"
prompt = "judge"
judge = true
"""

CLAUDE_AUTHENTICATION = harbor_mod.HarborAuthentication(
    method="claude-oauth-token",
    source="CLAUDE_CODE_OAUTH_TOKEN",
    description="Claude Code subscription token",
    environment_changes={"CLAUDE_CODE_OAUTH_TOKEN": "oauth-secret"},
)


def test_claude_solve_launch_uses_claude_images_and_records_its_authentication(
    data_root: Path,
    tmp_path: Path,
    base_image_calls: list[tuple[list[str], str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Claude launch builds Claude images and records how it authenticated.

    The agent has to travel from the config all the way to the image
    build and the run record: the same wiring a Codex-only test suite
    cannot see, because Codex is what every default resolves to.
    """
    config = write_config(tmp_path, CLAUDE_SOLVE_TOML, "claude-opus5-high")
    monkeypatch.setattr(
        harbor_mod, "resolve_harbor_authentication", lambda _agent: CLAUDE_AUTHENTICATION
    )
    monkeypatch.setattr(harbor_mod, "invoke_harbor", lambda _command, _authentication: 0)

    # No trials exist behind the stubbed Harbor, so every item is
    # reported failed and the run exits nonzero; the launch itself is
    # what this test is about.
    assert (
        cli.main(solve_args(data_root, config, "--course", COURSE_ID, "--assignment", "HW1")) == 1
    )
    assert base_image_calls == [(["scientific-python"], CLAUDE_CODE_AGENT)]

    job_dir = job_dirs(data_root, "solving")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    assert record["config"]["agent"] == CLAUDE_CODE_AGENT
    assert record["authentication"] == {
        "method": "claude-oauth-token",
        "source": "CLAUDE_CODE_OAUTH_TOKEN",
    }
    task_dir = data_root / "tasks" / job_dir.name / record["items"][0]["task_dir_name"]
    assert (task_dir / "environment" / "base.Dockerfile").read_bytes() == environment_path(
        "scientific-python", CLAUDE_CODE_AGENT
    ).read_bytes()


def test_agent_without_a_shipped_template_is_reported_at_plan_time(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Harbor agent with no image of its own still runs, and says so.

    Its tasks build on the plain `<flavor>.Dockerfile`, where Harbor finds
    no agent on PATH and installs one per trial — a cost worth one line
    rather than a surprise in a build log.
    """
    config = write_config(
        tmp_path,
        CLAUDE_SOLVE_TOML.replace('agent = "claude-code"', 'agent = "gemini-cli"'),
        "gemini-high",
    )
    assert cli.main(solve_args(data_root, config, "--course", COURSE_ID, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "note: no environment template ships for agent 'gemini-cli'" in out
    assert "Harbor installs that CLI in every trial" in out


def test_claude_grade_run_uses_the_claude_grading_image(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path, CLAUDE_GRADE_TOML_CLI, "claude-grader-opus5-high")
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    assert (
        cli.main(grade_args(data_root, config, "--submissions", submission, "--materialize-only"))
        == 0
    )
    # The image a manual harbor run would have to build first is the
    # Claude one, named by its content tag.
    assert (
        base_images_mod.base_image_reference("grading", CLAUDE_CODE_AGENT)
        in capsys.readouterr().out
    )

    job_dir = job_dirs(data_root, "grading")[0]
    record = json.loads((job_dir / "aat-run.json").read_text(encoding="utf-8"))
    task_dir = data_root / "tasks" / job_dir.name / record["items"][0]["task_dir_name"]
    assert (task_dir / "environment" / "base.Dockerfile").read_bytes() == environment_path(
        "grading", CLAUDE_CODE_AGENT
    ).read_bytes()

    # Same submission, same rubric, different agent: a different image
    # and therefore a different item — Codex gradings never pool with
    # Claude ones.
    codex = write_config(tmp_path, GRADE_TOML, "codex-grader-sol-high")
    assert (
        cli.main(grade_args(data_root, codex, "--submissions", submission, "--materialize-only"))
        == 0
    )
    codex_record = json.loads(
        (job_dirs(data_root, "grading")[1] / "aat-run.json").read_text(encoding="utf-8")
    )
    assert codex_record["items"][0]["item_id"] == record["items"][0]["item_id"]
    assert codex_record["items"][0]["item_identity"] != record["items"][0]["item_identity"]


def test_claude_judge_reads_gradings_stored_by_a_codex_grader(
    data_root: Path,
    grade_config: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cross-agent judge finds its context config's stored gradings.

    Every part of the lookup key must be a *context* config input. Using
    the judge's own grading image instead made this exact command match
    zero gradings and print a top-up hint that could never help, since
    the gradings asked for already existed.
    """
    judge_config = write_config(tmp_path, CLAUDE_JUDGE_TOML_CLI, "claude-judge-opus5-high")
    submission = str(data_root / "submissions" / COURSE_ID / "stu1" / "HW1")
    grade_one_initial(data_root, grade_config, "graded__t1")

    capsys.readouterr()
    initial_jobs = set(job_dirs(data_root, "grading"))
    assert (
        cli.main(
            grade_args(
                data_root,
                judge_config,
                "--submissions",
                submission,
                "--context-from",
                str(grade_config),
                "--gradings",
                "1",
                "--materialize-only",
            )
        )
        == 0
    )
    assert "skipping" not in capsys.readouterr().out

    # Job directories are timestamped to the second, so the judge job is
    # found by difference rather than by sort order.
    judge_job = next(d for d in job_dirs(data_root, "grading") if d not in initial_jobs)
    record = json.loads((judge_job / "aat-run.json").read_text(encoding="utf-8"))
    item = record["items"][0]
    assert record["config"]["agent"] == CLAUDE_CODE_AGENT
    assert item["context_config_name"] == "codex-grader-sol-high"
    assert [ref["trial_name"] for ref in item["prior_trials"]] == ["graded__t1"]
    task_dir = data_root / "tasks" / judge_job.name / item["task_dir_name"]
    assert (task_dir / "environment" / "prior_gradings" / "01" / "grading_result.json").is_file()
    # The judge's own tasks still build on the judge agent's image.
    assert (task_dir / "environment" / "base.Dockerfile").read_bytes() == environment_path(
        "grading", CLAUDE_CODE_AGENT
    ).read_bytes()


def test_init_data_creates_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "aat-data"
    assert cli.main(["init-data", "--data-root", str(target)]) == 0
    out = capsys.readouterr().out
    assert f"initialized data root {target.resolve()}" in out
    assert "created courses/" in out
    assert (target / "courses").is_dir()
    assert (target / "README.md").is_file()

    assert cli.main(["init-data", "--data-root", str(target)]) == 0
    assert "already initialized" in capsys.readouterr().out


def test_init_data_refuses_toolkit_clone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_fake_toolkit_repo(tmp_path)
    assert cli.main(["init-data", "--data-root", str(repo / "data")]) == 2
    assert "inside the toolkit repository" in capsys.readouterr().err


def test_grade_refuses_to_orphan_a_superseded_rubric(
    data_root: Path, grade_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Advancing `default` without archiving the old bytes is refused."""
    args = grade_args(data_root, grade_config, "--course", COURSE_ID, "--materialize-only")
    assert cli.main(args) == 0  # one grading job now refers to the current bytes

    rubric = data_root / "courses" / COURSE_ID / "rubrics" / "HW1" / "default.md"
    superseded = rubric.read_bytes()
    rubric.write_text("# HW1\n\n- `a` (10 points): a corrected split.\n", encoding="utf-8")
    assert cli.main(args) == 2
    err = capsys.readouterr().err
    assert "no longer on disk" in err
    assert f"{COURSE_ID}/HW1: rubric 'default' version" in err

    # Archiving the superseded bytes under any name restores resolution.
    archive = rubric.parent / RUBRIC_ARCHIVE_DIRNAME
    archive.mkdir()
    (archive / "old.md").write_bytes(superseded)
    assert cli.main(args) == 0
