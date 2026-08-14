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


def run_grading_verifier(
    tmp_path: Path,
    output_dir: Path,
    expected_criteria: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    shutil.copy(verifier_path("grade"), tests_dir / "grading_verifier.py")
    shutil.copy(grading_schema_source_path(), tests_dir / "grading_schema.py")
    if expected_criteria is not None:
        (tests_dir / "expected_criteria.json").write_text(
            json.dumps(expected_criteria), encoding="utf-8"
        )
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


# The expected-criteria list matching valid_result(): what the
# materializer would have written from the rubric that produced it.
def matching_expected_criteria() -> list[dict[str, object]]:
    return [
        {"id": "p1", "max_points": 8, "bonus": False},
        {"id": "p2", "max_points": 2, "bonus": False},
        {"id": "extra", "max_points": 1, "bonus": True},
    ]


def test_grading_verifier_surfaces_derived_scores(tmp_path: Path) -> None:
    output_dir = make_grading_output(tmp_path, valid_result())
    result = run_grading_verifier(tmp_path, output_dir)
    assert result["reward"] == 85.0  # (8 base + 0.5 bonus) / 10 base max
    assert result["details"]["contract_valid"] is True
    assert result["details"]["score_pct"] == 85.0
    assert result["details"]["base_pct"] == 80.0
    assert result["details"]["sums_consistent"] is True
    # No expected-criteria file (a task materialized before the file
    # existed): the rubric cross-check is skipped silently.
    assert result["details"]["expected_criteria_checked"] is False
    assert result["rewards_file"] == {"reward": 85.0, "base_pct": 80.0}


def test_grading_verifier_accepts_criteria_matching_the_rubric(tmp_path: Path) -> None:
    output_dir = make_grading_output(tmp_path, valid_result())
    result = run_grading_verifier(tmp_path, output_dir, matching_expected_criteria())
    assert result["reward"] == 85.0
    assert result["details"]["contract_valid"] is True
    assert result["details"]["expected_criteria_checked"] is True
    assert result["details"]["errors"] == []


def run_rubric_mismatch(tmp_path: Path, graded: dict[str, object] | None = None) -> dict[str, Any]:
    output_dir = make_grading_output(tmp_path, graded if graded is not None else valid_result())
    result = run_grading_verifier(tmp_path, output_dir, matching_expected_criteria())
    assert result["reward"] == 0.0
    assert result["details"]["contract_valid"] is False
    assert result["details"]["expected_criteria_checked"] is True
    assert result["rewards_file"] == {"reward": 0.0}
    return result


def test_grading_verifier_rejects_dropped_criterion(tmp_path: Path) -> None:
    graded = valid_result()
    del graded["criteria"][1]  # drop p2
    result = run_rubric_mismatch(tmp_path, graded)
    assert result["details"]["errors"] == [
        "rubric mismatch: criterion 'p2' from the rubric is missing from the grading result"
    ]


def test_grading_verifier_rejects_renamed_criterion(tmp_path: Path) -> None:
    graded = valid_result()
    graded["criteria"][1]["id"] = "p2-renamed"
    result = run_rubric_mismatch(tmp_path, graded)
    assert result["details"]["errors"] == [
        "rubric mismatch: criterion 'p2' from the rubric is missing from the grading result",
        "rubric mismatch: criterion 'p2-renamed' is not in the rubric",
    ]


def test_grading_verifier_rejects_changed_max_points(tmp_path: Path) -> None:
    graded = valid_result()
    graded["criteria"][0]["max_points"] = 5
    graded["criteria"][0]["points"] = 4
    result = run_rubric_mismatch(tmp_path, graded)
    assert result["details"]["errors"] == [
        "rubric mismatch: criterion 'p1': max_points 5 does not match the rubric's 8"
    ]


def test_grading_verifier_rejects_flipped_bonus_flag(tmp_path: Path) -> None:
    graded = valid_result()
    graded["criteria"][2]["bonus"] = False
    result = run_rubric_mismatch(tmp_path, graded)
    assert result["details"]["errors"] == [
        "rubric mismatch: criterion 'extra': bonus false does not match the rubric's true"
    ]


def test_grading_verifier_short_circuits_rubric_check_on_structural_failure(
    tmp_path: Path,
) -> None:
    """Structural validation fails first: the expected-criteria file exists,
    but the rubric cross-check never runs, so the flag stays false."""
    bad = valid_result()
    bad["criteria"][0]["evidence"] = ""
    output_dir = make_grading_output(tmp_path, bad)
    result = run_grading_verifier(tmp_path, output_dir, matching_expected_criteria())
    assert result["reward"] == 0.0
    assert result["details"]["contract_valid"] is False
    assert result["details"]["expected_criteria_checked"] is False
    assert result["rewards_file"] == {"reward": 0.0}


def test_grading_verifier_reports_malformed_expected_criteria(tmp_path: Path) -> None:
    """A broken expected-criteria file is a contract error with a reward
    file written — never an uncaught crash — and the check did not run."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for content in ("not json", '[{"id": "p1"}]'):  # unparseable; missing keys
        (tests_dir / "expected_criteria.json").write_text(content, encoding="utf-8")
        output_dir = make_grading_output(tmp_path, valid_result())
        result = run_grading_verifier(tmp_path, output_dir)
        assert result["reward"] == 0.0
        assert result["details"]["contract_valid"] is False
        assert result["details"]["expected_criteria_checked"] is False
        assert any("malformed expected_criteria.json" in e for e in result["details"]["errors"])
        assert result["rewards_file"] == {"reward": 0.0}


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
