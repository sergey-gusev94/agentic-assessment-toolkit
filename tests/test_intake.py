from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agentic_assessment_toolkit import cli, intake
from tests.conftest import COURSE_ID, FIXTURES_DIR


def make_dump(data_root: Path, course_id: str) -> Path:
    raw_dir = data_root / "raw" / course_id
    raw_dir.mkdir(parents=True)
    (raw_dir / "syllabus.pdf").write_text("syllabus\n", encoding="utf-8")
    return raw_dir


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """A data root with one raw dump and no processed courses."""
    root = tmp_path / "data-root"
    root.mkdir()
    make_dump(root, "C_NEW_F2026")
    return root


def test_render_prompt_substitutes_the_course_id() -> None:
    prompt = intake.render_prompt("PU_X_F2026")
    assert "raw/PU_X_F2026/" in prompt
    assert "courses/PU_X_F2026/" in prompt
    assert "{course_id}" not in prompt


def test_render_prompt_requires_bounded_assignment_audits() -> None:
    prompt = intake.render_prompt("PU_X_F2026")
    normalized = " ".join(prompt.split())
    assert "spawn one read-only subagent per assignment" in normalized
    assert "student-facing assignment bundle" in normalized
    assert "instructor/reference-solution bundle" in normalized
    # The point split names its source, in precedence order, and the
    # rubric covers the whole assignment.
    assert "applied_scheme" in normalized
    assert "professor_rubric" in normalized
    assert "handout" in normalized
    assert "authored" in normalized
    assert "the higher one wins" in normalized
    assert "cover the **whole assignment**" in normalized
    assert "negative-search evidence" in normalized
    assert "total exactly 100 points" in normalized
    assert "spawn one final read-only reviewer subagent" in normalized
    assert "wait for every audit" in normalized


def test_build_command() -> None:
    command = intake.build_command("PROMPT", "gpt-5.6-sol", "high")
    assert command == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "-m",
        "gpt-5.6-sol",
        "-c",
        "model_reasoning_effort=high",
        "PROMPT",
    ]


def test_statuses(data_root: Path) -> None:
    # A raw dump with no course tree is pending.
    [course] = intake.list_raw_courses(data_root)
    assert course.status == "pending"

    # A course tree without a receipt is manual.
    shutil.copytree(FIXTURES_DIR / "course" / COURSE_ID, data_root / "courses" / "C_NEW_F2026")
    [course] = intake.list_raw_courses(data_root)
    assert course.status == "manual"

    # A receipt matching the current dump hash is done...
    command = intake.build_command("p", "m", "high")
    intake.write_record(
        data_root, "C_NEW_F2026", command=command, model="m", reasoning_effort="high", log_path=None
    )
    [course] = intake.list_raw_courses(data_root)
    assert course.status == "done"

    # ...and new raw material makes the course pending again.
    (data_root / "raw" / "C_NEW_F2026" / "HW9.pdf").write_text("new\n", encoding="utf-8")
    [course] = intake.list_raw_courses(data_root)
    assert course.status == "pending"


def test_record_contents(data_root: Path) -> None:
    command = intake.build_command(intake.render_prompt("C_NEW_F2026"), "m", "low")
    path = intake.write_record(
        data_root, "C_NEW_F2026", command=command, model="m", reasoning_effort="low", log_path=None
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["course_id"] == "C_NEW_F2026"
    assert record["model"] == "m"
    assert len(record["raw_sha256"]) == 64
    assert len(record["prompt_sha256"]) == 64
    # The rendered prompt is elided from the recorded command.
    assert record["command"][-1] == "<rendered intake prompt>"
    assert "raw/C_NEW_F2026/" not in json.dumps(record)


def test_print_prompt_needs_no_data_root() -> None:
    assert cli.main(["intake", "--course", "C1", "--print-prompt"]) == 0


def test_selection_errors(data_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["intake", "--data-root", str(data_root)]) == 2
    assert "select courses" in capsys.readouterr().err
    assert cli.main(["intake", "--course", "NOPE", "--data-root", str(data_root)]) == 2
    assert "no raw dump" in capsys.readouterr().err


def test_dry_run(data_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["intake", "--all", "--dry-run", "--data-root", str(data_root)]) == 0
    out = capsys.readouterr().out
    assert "run  [pending] C_NEW_F2026" in out
    assert "would run 1 of 1 course(s)" in out


def fake_execute_producing_course(data_root: Path) -> object:
    def fake(_command: list[str], cwd: Path, log_path: Path) -> int:
        assert cwd == data_root
        shutil.copytree(FIXTURES_DIR / "course" / COURSE_ID, data_root / "courses" / "C_NEW_F2026")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("agent output\n", encoding="utf-8")
        return 0

    return fake


def test_successful_run_writes_receipt_and_checks(
    data_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intake, "codex_path", lambda: "/fake/codex")
    monkeypatch.setattr(intake, "execute", fake_execute_producing_course(data_root))
    assert cli.main(["intake", "--all", "--data-root", str(data_root)]) == 0
    out = capsys.readouterr().out
    assert "launching codex" in out
    assert "course C_NEW_F2026" in out  # the checker report follows
    assert "processed 1 of 1 course(s)" in out
    assert intake.record_path(data_root, "C_NEW_F2026").is_file()

    # The receipt makes the next run a no-op.
    assert cli.main(["intake", "--all", "--data-root", str(data_root)]) == 0
    assert "nothing to do: 1 already processed" in capsys.readouterr().out


def test_failed_agent_leaves_the_course_pending(
    data_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intake, "codex_path", lambda: "/fake/codex")
    monkeypatch.setattr(intake, "execute", lambda *_args, **_kwargs: 3)
    assert cli.main(["intake", "--all", "--data-root", str(data_root)]) == 1
    out = capsys.readouterr().out
    assert "codex exited 3; no receipt written" in out
    assert not intake.record_path(data_root, "C_NEW_F2026").is_file()
    [course] = intake.list_raw_courses(data_root)
    assert course.status == "pending"


def test_zero_exit_without_a_course_tree_is_a_failure(
    data_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intake, "codex_path", lambda: "/fake/codex")
    monkeypatch.setattr(intake, "execute", lambda *_args, **_kwargs: 0)
    assert cli.main(["intake", "--all", "--data-root", str(data_root)]) == 1
    assert "produced no courses/C_NEW_F2026" in capsys.readouterr().out
    assert not intake.record_path(data_root, "C_NEW_F2026").is_file()


def test_manual_course_is_skipped_without_force(
    data_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.copytree(FIXTURES_DIR / "course" / COURSE_ID, data_root / "courses" / "C_NEW_F2026")
    monkeypatch.setattr(intake, "execute", lambda *_args, **_kwargs: pytest.fail("must not run"))
    assert cli.main(["intake", "--all", "--data-root", str(data_root)]) == 0
    out = capsys.readouterr().out
    assert "built by hand?" in out
    assert "nothing to do: 1 hand-built (skipped)" in out


def test_force_runs_a_manual_course(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(FIXTURES_DIR / "course" / COURSE_ID, data_root / "courses" / "C_NEW_F2026")
    monkeypatch.setattr(intake, "codex_path", lambda: "/fake/codex")
    monkeypatch.setattr(intake, "execute", lambda *_args, **_kwargs: 0)
    assert cli.main(["intake", "--all", "--force", "--data-root", str(data_root)]) == 0
    assert intake.record_path(data_root, "C_NEW_F2026").is_file()


def test_execute_tees_output_to_the_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log_path = tmp_path / "logs" / "run.log"
    exit_code = intake.execute(
        ["sh", "-c", "echo hello; echo oops >&2; exit 7"], cwd=tmp_path, log_path=log_path
    )
    assert exit_code == 7
    assert capsys.readouterr().out == "hello\noops\n"
    assert log_path.read_text(encoding="utf-8") == "hello\noops\n"


def test_corrupt_receipt_means_pending_not_manual(data_root: Path) -> None:
    (data_root / "courses" / "C_NEW_F2026").mkdir(parents=True)
    intake.record_path(data_root, "C_NEW_F2026").write_text("{truncated", encoding="utf-8")
    [course] = intake.list_raw_courses(data_root)
    assert course.status == "pending"


def test_missing_codex_is_a_reported_error(
    data_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intake, "codex_path", lambda: None)
    assert cli.main(["intake", "--all", "--data-root", str(data_root)]) == 2
    assert "codex CLI not found on PATH" in capsys.readouterr().err


def test_course_selection_hashes_only_that_dump(data_root: Path) -> None:
    make_dump(data_root, "C_OTHER_F2026")
    [course] = intake.list_raw_courses(data_root, only="C_OTHER_F2026")
    assert course.course_id == "C_OTHER_F2026"


def test_log_path_never_reuses_an_existing_file(data_root: Path) -> None:
    from datetime import UTC, datetime

    moment = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
    first = intake.log_path_for(data_root, "C1", now=moment)
    first.parent.mkdir(parents=True)
    first.write_text("earlier failure\n", encoding="utf-8")
    second = intake.log_path_for(data_root, "C1", now=moment)
    assert second != first
    assert second.parent == first.parent
