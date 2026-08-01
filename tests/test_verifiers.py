"""Exercise the two standalone verifier scripts as real subprocesses.

The scripts run offline against temporary directories via their
AAT_* environment overrides; no Harbor, no Docker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agentic_assessment_toolkit.config import grading_schema_source_path, verifier_path
from agentic_assessment_toolkit.hashing import file_manifest
from tests.test_grading_schema import valid_result


def run_solve_verifier(tmp_path: Path, submission: Path, inputs: Path) -> dict[str, Any]:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    shutil.copy(verifier_path("solve"), tests_dir / "solve_verifier.py")
    (tests_dir / "input_manifest.json").write_text(
        json.dumps(file_manifest(inputs)), encoding="utf-8"
    )
    reward_path = tmp_path / "reward.json"
    completed = subprocess.run(
        [sys.executable, str(tests_dir / "solve_verifier.py")],
        env={
            "AAT_SUBMISSION_DIR": str(submission),
            "AAT_REWARD_PATH": str(reward_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    stdout: dict[str, Any] = json.loads(completed.stdout)
    rewards = json.loads(reward_path.read_text(encoding="utf-8"))
    assert rewards == {"reward": stdout["reward"]}
    return stdout


@pytest.fixture
def assignment(tmp_path: Path) -> Path:
    inputs = tmp_path / "assignment"
    inputs.mkdir()
    (inputs / "statement.md").write_text("solve it", encoding="utf-8")
    (inputs / "data.csv").write_text("x\n1\n", encoding="utf-8")
    return inputs


def test_solve_verifier_accepts_changed_copy(tmp_path: Path, assignment: Path) -> None:
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "data.csv").write_text("x\n1\n", encoding="utf-8")  # unchanged copy
    (submission / "answer.md").write_text("the slope is 2", encoding="utf-8")  # new
    result = run_solve_verifier(tmp_path, submission, assignment)
    assert result["reward"] == 1.0
    assert result["details"]["failures"] == []


def test_solve_verifier_rejects_missing_submission(tmp_path: Path, assignment: Path) -> None:
    result = run_solve_verifier(tmp_path, tmp_path / "nope", assignment)
    assert result["reward"] == 0.0


def test_solve_verifier_rejects_empty_submission(tmp_path: Path, assignment: Path) -> None:
    (tmp_path / "submission").mkdir()
    result = run_solve_verifier(tmp_path, tmp_path / "submission", assignment)
    assert result["reward"] == 0.0
    assert any("no files" in f for f in result["details"]["failures"])


def test_solve_verifier_rejects_unchanged_inputs_only(tmp_path: Path, assignment: Path) -> None:
    submission = tmp_path / "submission"
    shutil.copytree(assignment, submission)
    result = run_solve_verifier(tmp_path, submission, assignment)
    assert result["reward"] == 0.0
    assert any("unchanged copy" in f for f in result["details"]["failures"])


def test_solve_verifier_rejects_bookkeeping(tmp_path: Path, assignment: Path) -> None:
    submission = tmp_path / "submission"
    (submission / "__pycache__").mkdir(parents=True)
    (submission / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
    (submission / "answer.md").write_text("done", encoding="utf-8")
    (submission / "notes.md~").write_text("backup", encoding="utf-8")
    result = run_solve_verifier(tmp_path, submission, assignment)
    assert result["reward"] == 0.0
    failures = result["details"]["failures"]
    assert any("bookkeeping" in f for f in failures)


def run_grading_verifier(tmp_path: Path, output_dir: Path) -> dict[str, Any]:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    shutil.copy(verifier_path("grade"), tests_dir / "grading_verifier.py")
    shutil.copy(grading_schema_source_path(), tests_dir / "grading_schema.py")
    reward_path = tmp_path / "reward.json"
    completed = subprocess.run(
        [sys.executable, str(tests_dir / "grading_verifier.py")],
        env={
            "AAT_GRADING_OUTPUT_DIR": str(output_dir),
            "AAT_REWARD_PATH": str(reward_path),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    stdout: dict[str, Any] = json.loads(completed.stdout)
    rewards = json.loads(reward_path.read_text(encoding="utf-8"))
    assert rewards["reward"] == stdout["reward"]
    stdout["rewards_file"] = rewards
    return stdout


def make_grading_output(tmp_path: Path, result: dict[str, object] | None) -> Path:
    output_dir = tmp_path / "grading_output"
    output_dir.mkdir(exist_ok=True)
    if result is not None:
        (output_dir / "grading_result.json").write_text(json.dumps(result), encoding="utf-8")
    (output_dir / "justification.md").write_text("# Justification\nDetails.", encoding="utf-8")
    return output_dir


def test_grading_verifier_surfaces_derived_scores(tmp_path: Path) -> None:
    output_dir = make_grading_output(tmp_path, valid_result())
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 85.0  # (8 base + 0.5 bonus) / 10 base max
    assert result["details"]["contract_valid"] is True
    assert result["details"]["score_pct"] == 85.0
    assert result["details"]["base_pct"] == 80.0
    assert result["details"]["sums_consistent"] is True
    assert result["rewards_file"] == {"reward": 85.0, "base_pct": 80.0}


def test_grading_verifier_tolerates_scratch_files(tmp_path: Path) -> None:
    output_dir = make_grading_output(tmp_path, valid_result())
    (output_dir / "notes.txt").write_text("scratch", encoding="utf-8")
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 85.0


def test_grading_verifier_flags_inconsistent_sums(tmp_path: Path) -> None:
    """An authored-sum mismatch is recorded, never a contract failure."""
    flagged = valid_result()
    flagged["base_points"] = 9
    output_dir = make_grading_output(tmp_path, flagged)
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 85.0
    assert result["details"]["contract_valid"] is True
    assert result["details"]["sums_consistent"] is False
    assert result["details"]["sums"]["authored"]["base_points"] == 9
    assert result["details"]["sums"]["computed"]["base_points"] == 8.0
    assert result["rewards_file"] == {"reward": 85.0, "base_pct": 80.0}


def test_grading_verifier_emits_strict_json_for_nan_sums(tmp_path: Path) -> None:
    """A NaN authored sum must not leak into the details as invalid JSON."""
    flagged = valid_result()
    flagged["base_points"] = float("nan")
    output_dir = make_grading_output(tmp_path, flagged)
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 85.0
    assert result["details"]["sums_consistent"] is False
    assert result["details"]["sums"]["authored"]["base_points"] == "nan"


def test_grading_verifier_rejects_structural_violation(tmp_path: Path) -> None:
    bad = valid_result()
    bad["criteria"][0]["evidence"] = ""
    output_dir = make_grading_output(tmp_path, bad)
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 0.0
    assert result["details"]["contract_valid"] is False
    assert result["details"]["sums_consistent"] is None
    assert result["rewards_file"] == {"reward": 0.0}


def test_grading_verifier_rejects_missing_result(tmp_path: Path) -> None:
    output_dir = make_grading_output(tmp_path, None)
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 0.0
    assert any("cannot read" in e for e in result["details"]["errors"])


def test_grading_verifier_rejects_missing_justification(tmp_path: Path) -> None:
    output_dir = make_grading_output(tmp_path, valid_result())
    (output_dir / "justification.md").unlink()
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 0.0
    assert any("justification.md" in e for e in result["details"]["errors"])
