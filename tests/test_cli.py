from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_assessment_toolkit import cli
from agentic_assessment_toolkit import harbor as harbor_mod
from tests.conftest import COURSE_ID
from tests.test_config import GRADE_TOML, SOLVE_TOML, write_config
from tests.test_harbor import GRADED_REWARDS, write_trial

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_harbor_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repository tests never invoke Harbor (AGENTS.md)."""

    def refuse(command: list[str]) -> int:
        raise AssertionError(f"harbor invoked during tests: {command}")

    # cli calls harbor_mod.invoke_harbor via the shared module object,
    # so one patch covers both import sites. cli_harbor_version would
    # subprocess the harbor binary, so it is stubbed too.
    monkeypatch.setattr(harbor_mod, "invoke_harbor", refuse)
    monkeypatch.setattr(harbor_mod, "cli_harbor_version", lambda: "0.20.0-test")


@pytest.fixture
def solve_config(tmp_path: Path) -> Path:
    return write_config(tmp_path, SOLVE_TOML, "codex-high")


@pytest.fixture
def grade_config(tmp_path: Path) -> Path:
    return write_config(tmp_path, GRADE_TOML, "codex-grader-high")


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
    assert record["max_concurrent_trials"] == 8
    assert len(record["items"]) == 1
    assert record["items"][0]["item_id"] == f"{COURSE_ID}/HW1"

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


def test_missing_data_root_is_an_error(
    solve_config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AAT_DATA_DIR", raising=False)
    assert run_cli("solve", "--config", str(solve_config), "--all") == 2
    assert "no data root" in capsys.readouterr().err


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
    assert f"[done   ] {COURSE_ID}/HW1" in out
    assert f"[pending] {COURSE_ID}/HW2" in out
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
    assert f"[done   ] {COURSE_ID}/stu1/HW1" in out
    assert f"[pending] {COURSE_ID}/stu2/HW1" in out


def test_missing_named_rubric_is_an_error(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A configured non-default rubric must exist; only 'default' may be absent."""
    config = write_config(tmp_path, GRADE_TOML + 'rubric = "strict-v2"\n', "codex-grader-strict")
    assert cli.main(grade_args(data_root, config, "--course", COURSE_ID, "--materialize-only")) == 2
    assert "strict-v2" in capsys.readouterr().err


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
    assert f"[pending] {COURSE_ID}/stu1/HW1" in capsys.readouterr().out


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

    def fake_invoke(command: list[str]) -> int:
        invoked.append(command)
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
    assert invoked == [record["command"]]


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
