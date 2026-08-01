from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentic_assessment_toolkit.grading_schema import (
    computed_sums,
    derive_scores,
    load_grading_result,
    sums_report,
    validate_grading_result,
)


def valid_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "criteria": [
            {
                "id": "p1",
                "title": "Problem 1",
                "max_points": 8,
                "points": 6,
                "evidence": "answer.md reports slope 2 with the fit shown",
            },
            {
                "id": "p2",
                "title": "Problem 2",
                "max_points": 2,
                "points": 2,
                "evidence": "method stated in answer.md",
            },
            {
                "id": "extra",
                "title": "Bonus plot",
                "max_points": 1,
                "points": 0.5,
                "evidence": "plot present but unlabeled",
                "bonus": True,
            },
        ],
        "base_points": 8,
        "base_max": 10,
        "bonus_points": 0.5,
        "bonus_max": 1,
        "overall_comment": "Good work; method could be clearer.",
    }


def test_valid_result_passes() -> None:
    assert validate_grading_result(valid_result()) == []


def test_non_object_is_rejected() -> None:
    assert validate_grading_result([1, 2]) == ["grading result must be a JSON object"]


def test_missing_fields_are_reported() -> None:
    errors = validate_grading_result({})
    assert "missing required field 'criteria'" in errors
    assert "missing required field 'overall_comment'" in errors
    # Authored sums are a self-check, not required fields.
    assert not any("base_points" in e for e in errors)


def test_wrong_schema_version() -> None:
    data = valid_result()
    data["schema_version"] = 2
    assert any("schema_version" in e for e in validate_grading_result(data))


def test_boolean_schema_version_is_rejected() -> None:
    data = valid_result()
    data["schema_version"] = True
    assert any("schema_version" in e for e in validate_grading_result(data))


def test_empty_criteria_rejected() -> None:
    data = valid_result()
    data["criteria"] = []
    assert any("non-empty list" in e for e in validate_grading_result(data))


def test_duplicate_ids_rejected() -> None:
    data = valid_result()
    data["criteria"][1]["id"] = "p1"
    assert any("duplicate criterion id" in e for e in validate_grading_result(data))


def test_points_above_max_rejected() -> None:
    data = valid_result()
    data["criteria"][0]["points"] = 9
    assert any("0 <= points <= max_points" in e for e in validate_grading_result(data))


def test_negative_points_rejected() -> None:
    data = valid_result()
    data["criteria"][0]["points"] = -1
    assert any("0 <= points <= max_points" in e for e in validate_grading_result(data))


def test_zero_max_points_rejected() -> None:
    data = valid_result()
    data["criteria"][0]["max_points"] = 0
    assert any("max_points must be a finite number > 0" in e for e in validate_grading_result(data))


def test_boolean_points_rejected() -> None:
    data = valid_result()
    data["criteria"][0]["points"] = True
    assert any("points must be a finite number" in e for e in validate_grading_result(data))


def test_empty_evidence_rejected() -> None:
    data = valid_result()
    data["criteria"][0]["evidence"] = "   "
    assert any("evidence" in e for e in validate_grading_result(data))


def test_non_boolean_bonus_rejected() -> None:
    data = valid_result()
    data["criteria"][2]["bonus"] = "yes"
    assert any("bonus must be a boolean" in e for e in validate_grading_result(data))


def test_all_bonus_criteria_rejected() -> None:
    data = valid_result()
    for entry in data["criteria"]:
        entry["bonus"] = True
    data.update(base_points=0, base_max=0, bonus_points=8.5, bonus_max=11)
    assert any(
        "at least one criterion must be non-bonus" in e for e in validate_grading_result(data)
    )


def test_sum_mismatch_is_flagged_not_rejected() -> None:
    data = valid_result()
    data["base_points"] = 9
    assert validate_grading_result(data) == []
    report: dict[str, Any] = sums_report(data)
    assert report["consistent"] is False
    assert report["authored"]["base_points"] == 9
    assert report["computed"]["base_points"] == 8.0


def test_missing_sums_are_valid_but_inconsistent() -> None:
    data = valid_result()
    del data["base_points"]
    assert validate_grading_result(data) == []
    report: dict[str, Any] = sums_report(data)
    assert report["consistent"] is False
    assert report["authored"]["base_points"] is None


def test_non_finite_authored_sum_is_flagged_and_json_safe() -> None:
    data = valid_result()
    data["base_points"] = float("nan")
    assert validate_grading_result(data) == []
    report: dict[str, Any] = sums_report(data)
    assert report["consistent"] is False
    # Recorded as its repr so the report stays strict-JSON serializable.
    assert report["authored"]["base_points"] == "nan"
    json.dumps(report, allow_nan=False)  # must not raise


def test_huge_int_authored_sum_does_not_crash() -> None:
    data = valid_result()
    data["base_points"] = 10**400  # float() would raise OverflowError
    assert validate_grading_result(data) == []
    report: dict[str, Any] = sums_report(data)
    assert report["consistent"] is False
    assert report["authored"]["base_points"] == 10**400


def test_consistent_sums_report() -> None:
    report = sums_report(valid_result())
    assert report["consistent"] is True
    assert report["computed"] == {
        "base_points": 8.0,
        "base_max": 10.0,
        "bonus_points": 0.5,
        "bonus_max": 1.0,
    }


def test_computed_sums_from_criteria() -> None:
    assert computed_sums(valid_result()) == {
        "base_points": 8.0,
        "base_max": 10.0,
        "bonus_points": 0.5,
        "bonus_max": 1.0,
    }


def test_derive_scores_counts_bonus_over_required_max() -> None:
    scores = derive_scores(valid_result())
    assert scores == {"score_pct": 85.0, "base_pct": 80.0}


def test_derive_scores_ignores_authored_sums() -> None:
    """The computed sums are authoritative; the self-check never enters scoring."""
    data = valid_result()
    data["base_points"] = 999
    assert derive_scores(data) == {"score_pct": 85.0, "base_pct": 80.0}


def test_derive_scores_can_exceed_100() -> None:
    data = valid_result()
    data["criteria"][0]["points"] = 8
    data["criteria"][2]["points"] = 1
    data.update(base_points=10, bonus_points=1)
    assert validate_grading_result(data) == []
    assert derive_scores(data) == {"score_pct": 110.0, "base_pct": 100.0}


def test_derive_scores_uses_same_scale_for_required_and_bonus_points() -> None:
    data = valid_result()
    data["criteria"][0].update(max_points=70, points=70)
    data["criteria"][1].update(max_points=20, points=20)
    data["criteria"][2].update(max_points=10, points=10)
    data.update(base_points=90, base_max=90, bonus_points=10, bonus_max=10)
    assert validate_grading_result(data) == []
    scores = derive_scores(data)
    assert scores["base_pct"] == 100.0
    assert scores["score_pct"] == pytest.approx(111.11111111111111)


def test_float_accumulation_tolerated() -> None:
    criteria = [
        {
            "id": f"c{i}",
            "title": f"Criterion {i}",
            "max_points": 0.1,
            "points": 0.1,
            "evidence": "shown in the notebook",
        }
        for i in range(10)
    ]
    data = {
        "schema_version": 1,
        "criteria": criteria,
        "base_points": 1.0,
        "base_max": 1.0,
        "bonus_points": 0,
        "bonus_max": 0,
        "overall_comment": "ok",
    }
    assert validate_grading_result(data) == []
    assert sums_report(data)["consistent"] is True


def test_non_string_comment_rejected() -> None:
    data = valid_result()
    data["overall_comment"] = 5
    assert any("overall_comment" in e for e in validate_grading_result(data))


def test_load_missing_file(tmp_path: Path) -> None:
    data, errors = load_grading_result(tmp_path / "grading_result.json")
    assert data is None
    assert any("cannot read" in e for e in errors)


def test_load_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "grading_result.json"
    path.write_text("{not json", encoding="utf-8")
    data, errors = load_grading_result(path)
    assert data is None
    assert any("not valid JSON" in e for e in errors)


def test_load_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "grading_result.json"
    path.write_text(json.dumps(valid_result()), encoding="utf-8")
    data, errors = load_grading_result(path)
    assert errors == []
    assert data is not None and data["base_points"] == 8


def test_non_finite_points_rejected() -> None:
    for bad in (float("inf"), float("-inf"), float("nan")):
        data = valid_result()
        data["criteria"][0]["points"] = bad
        assert any("finite" in e for e in validate_grading_result(data)), bad


def test_non_finite_max_points_rejected() -> None:
    data = valid_result()
    data["criteria"][0]["max_points"] = float("inf")
    assert any("finite" in e for e in validate_grading_result(data))
