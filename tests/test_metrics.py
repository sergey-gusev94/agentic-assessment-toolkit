from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from agentic_assessment_toolkit import metrics
from agentic_assessment_toolkit.hashing import sha256_file
from agentic_assessment_toolkit.results import (
    _CRITERIA_DTYPES,
    _TRIALS_DTYPES,
    OUTCOME_CATEGORIES,
)

# The exact column contract of each metrics table (stage-3 spec);
# metrics.py, the report CSVs, and these tests must agree.
SOLVE_SUMMARY_COLUMNS = [
    "config_name",
    "config_identity",
    "course_id",
    "assignment_id",
    "n_trials",
    "n_completed",
    "n_contract_failed",
    "n_agent_error",
    "n_infra_error",
    "n_timeout",
    "n_cancelled",
    "n_unknown",
    "n_verified",
    "contract_pass_rate",
    "n_late_exception",
]

ASSIGNMENT_COLUMNS = [
    "solver_config_name",
    "solver_config_identity",
    "grading_config_name",
    "grading_config_identity",
    "course_id",
    "assignment_id",
    "rubric_sha256",
    "n_solve_trials",
    "n_gradings",
    "mean_base_pct",
    "sd_base_pct",
    "mean_score_pct",
]

COURSE_COLUMNS = [
    "solver_config_name",
    "solver_config_identity",
    "grading_config_name",
    "grading_config_identity",
    "course_id",
    "n_assignments",
    "n_assignments_mixed_rubric",
    "n_assignments_total",
    "macro_mean_base_pct",
    "macro_mean_score_pct",
    "ci_low",
    "ci_high",
]

JUDGE_COLUMNS = [
    "config_name",
    "config_identity",
    "n_trials",
    "n_valid_gradings",
    "n_items_repeated",
    "mean_within_item_sd",
    "max_within_item_range",
    "criterion_exact_agreement_rate",
    "criterion_mean_abs_diff",
    "rubric_fidelity_rate",
    "n_rubric_unresolved",
    "sums_consistent_rate",
    "late_exception_rate",
    "grading_load_error_rate",
    "rate_completed",
    "rate_contract_failed",
    "rate_agent_error",
    "rate_infra_error",
    "rate_timeout",
    "rate_cancelled",
    "rate_unknown",
]

STUDENT_COLUMNS = [
    "config_name",
    "config_identity",
    "course_id",
    "assignment_id",
    "rubric_sha256",
    "student_id",
    "n_valid_gradings",
    "mean_score_pct",
    "sd_score_pct",
    "mean_base_pct",
    "sd_base_pct",
    "n_failed_gradings",
    "n_sums_inconsistent",
    "n_late_exception",
    "n_load_error",
]

CLASS_COLUMNS = [
    "config_name",
    "config_identity",
    "course_id",
    "assignment_id",
    "rubric_sha256",
    "n_students",
    "mean",
    "median",
    "sd",
    "q25",
    "q75",
]

CHECK_COLUMNS = [
    "config_name",
    "config_identity",
    "course_id",
    "assignment_id",
    "rubric_sha256",
    "student_id",
    "role",
    "n_valid_gradings",
    "mean_base_pct",
    "min_base_pct",
    "max_base_pct",
    "mean_score_pct",
]

FAILURE_COLUMNS = ["stage", "config_name", "config_identity", "outcome", "n_trials", "share"]

UNGRADED_COLUMNS = [
    "config_name",
    "config_identity",
    "course_id",
    "assignment_id",
    "job_name",
    "trial_name",
    "outcome",
]


def trials_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """A trials table with the loader's columns and dtypes, defaults filled."""
    filled: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        base: dict[str, object] = dict.fromkeys(_TRIALS_DTYPES)
        base.update(
            {
                "job_name": "J",
                "trial_name": f"t{index}",
                "item_id": f"item{index}",
                "item_identity": f"identity{index}",
                "course_id": "C1",
                "assignment_id": "HW1",
                "late_exception": False,
                "grading_load_error": False,
            }
        )
        base.update(row)
        filled.append(base)
    return pd.DataFrame(filled, columns=list(_TRIALS_DTYPES)).astype(_TRIALS_DTYPES)


def criteria_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(_CRITERIA_DTYPES)).astype(_CRITERIA_DTYPES)


def graded(base: float, score: float, **over: object) -> dict[str, object]:
    """A valid grading row: completed grade trial with reward-file scores."""
    row: dict[str, object] = {
        "stage": "grade",
        "outcome": "completed",
        "config_name": "grader",
        "config_identity": "G",
        "base_pct": base,
        "score_pct": score,
        "sums_consistent": True,
    }
    row.update(over)
    return row


def failed_grading(outcome: str = "contract_failed", **over: object) -> dict[str, object]:
    """A failed measurement: a grade trial without a valid grading result."""
    row: dict[str, object] = {
        "stage": "grade",
        "outcome": outcome,
        "config_name": "grader",
        "config_identity": "G",
    }
    row.update(over)
    return row


def solved(outcome: str, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "stage": "solve",
        "outcome": outcome,
        "config_name": "solver",
        "config_identity": "S",
    }
    row.update(over)
    return row


def derived(base: float, score: float, item: str, **over: object) -> dict[str, object]:
    """A solve-derived valid grading; ``item`` is the pooling identity."""
    row = graded(base, score)
    row.update(
        {
            "item_id": item,
            "item_identity": f"{item}-id",
            "submission_source": "solve-trial",
            "solver_config_name": "solver",
            "solver_config_identity": "S",
        }
    )
    row.update(over)
    return row


def student(base: float, score: float, student_id: str, **over: object) -> dict[str, object]:
    row = graded(base, score)
    row.update(
        {
            "submission_source": "student",
            "student_id": student_id,
            "item_id": f"C1/{student_id}/HW1",
            "item_identity": f"{student_id}-id",
        }
    )
    row.update(over)
    return row


def crit(
    trial_name: str, criterion_id: str, points: float, max_points: float, *, bonus: bool = False
) -> dict[str, object]:
    return {
        "job_name": "J",
        "trial_name": trial_name,
        "item_id": "item",
        "item_identity": "identity",
        "config_name": "grader",
        "config_identity": "G",
        "criterion_id": criterion_id,
        "title": f"criterion {criterion_id}",
        "points": points,
        "max_points": max_points,
        "bonus": bonus,
    }


def row_where(frame: pd.DataFrame, column: str, value: object) -> pd.Series[Any]:
    matching = frame[frame[column] == value]
    assert len(matching) == 1
    return matching.iloc[0]


def test_module_constants_match_the_contract() -> None:
    assert metrics.DEFAULT_SEED == 42
    assert metrics.BOOTSTRAP_RESAMPLES == 10_000
    assert metrics.BOOTSTRAP_CONFIDENCE == 0.95
    assert metrics.MIN_BOOTSTRAP_CLUSTERS == 5
    # One source for the taxonomy: metrics shares results.py's tuple.
    assert metrics.OUTCOME_CATEGORIES is OUTCOME_CATEGORIES


def test_solve_summary_counts_and_pass_rate() -> None:
    trials = trials_frame(
        [
            solved("completed"),
            solved("completed", late_exception=True),
            solved("contract_failed"),
            solved("timeout"),
            solved("timeout", assignment_id="HW2"),
            graded(80.0, 80.0),  # grading rows never enter the solve summary
        ]
    )
    table = metrics.solve_summary(trials)
    assert list(table.columns) == SOLVE_SUMMARY_COLUMNS
    assert len(table) == 2

    hw1 = row_where(table, "assignment_id", "HW1")
    assert hw1["config_name"] == "solver"
    assert hw1["config_identity"] == "S"
    assert hw1["course_id"] == "C1"
    assert hw1["n_trials"] == 4
    assert hw1["n_completed"] == 2
    assert hw1["n_contract_failed"] == 1
    assert hw1["n_timeout"] == 1
    assert hw1["n_agent_error"] == 0
    assert hw1["n_infra_error"] == 0
    assert hw1["n_cancelled"] == 0
    assert hw1["n_unknown"] == 0
    assert hw1["n_verified"] == 3  # completed + contract_failed
    assert float(hw1["contract_pass_rate"]) == pytest.approx(2 / 3)
    assert hw1["n_late_exception"] == 1

    hw2 = row_where(table, "assignment_id", "HW2")
    assert hw2["n_trials"] == 1
    assert hw2["n_verified"] == 0
    assert pd.isna(hw2["contract_pass_rate"])  # no verified trials: NA, never 0


def test_grades_by_assignment_ladder_arithmetic() -> None:
    """The macro ladder must ignore repeat counts: means of means."""
    trials = trials_frame(
        [
            # Solve trial sj/t1 graded twice (85 after averaging), sj/t2
            # once (70): the assignment mean is (85 + 70) / 2, not the
            # grading mean (80 + 90 + 70) / 3.
            derived(80.0, 90.0, "sj/t1"),
            derived(90.0, 100.0, "sj/t1"),
            derived(70.0, 80.0, "sj/t2"),
            derived(50.0, 60.0, "sj/t3", assignment_id="HW2"),
            student(100.0, 100.0, "stu1"),  # student-sourced: not benchmark data
            failed_grading(
                submission_source="solve-trial",
                solver_config_name="solver",
                solver_config_identity="S",
            ),
            solved("completed"),
        ]
    )
    table = metrics.grades_by_assignment(trials)
    assert list(table.columns) == ASSIGNMENT_COLUMNS
    assert len(table) == 2

    hw1 = row_where(table, "assignment_id", "HW1")
    assert hw1["solver_config_name"] == "solver"
    assert hw1["solver_config_identity"] == "S"
    assert hw1["grading_config_name"] == "grader"
    assert hw1["grading_config_identity"] == "G"
    assert hw1["n_solve_trials"] == 2
    assert hw1["n_gradings"] == 3
    assert float(hw1["mean_base_pct"]) == pytest.approx(77.5)  # mean of 85 and 70
    assert float(hw1["sd_base_pct"]) == pytest.approx(112.5**0.5)  # SD of [85, 70], ddof=1
    assert float(hw1["mean_score_pct"]) == pytest.approx(87.5)  # mean of 95 and 80

    hw2 = row_where(table, "assignment_id", "HW2")
    assert hw2["n_solve_trials"] == 1
    assert hw2["n_gradings"] == 1
    assert float(hw2["mean_base_pct"]) == 50.0
    assert pd.isna(hw2["sd_base_pct"])  # one solve trial: no spread
    assert float(hw2["mean_score_pct"]) == 60.0


def test_grades_by_assignment_never_pools_across_identities() -> None:
    trials = trials_frame(
        [
            derived(80.0, 80.0, "sj/t1"),
            derived(80.0, 80.0, "sj/t1"),
            derived(100.0, 100.0, "sj/t1", item_identity="other-id"),
        ]
    )
    row = metrics.grades_by_assignment(trials).iloc[0]
    # Same item id under two identities is two solve trials: the mean is
    # (80 + 100) / 2, not the pooled (80 + 80 + 100) / 3.
    assert row["n_solve_trials"] == 2
    assert float(row["mean_base_pct"]) == pytest.approx(90.0)


def test_grades_by_assignment_keeps_na_solver_keys() -> None:
    trials = trials_frame(
        [
            derived(80.0, 80.0, "sj/t1"),
            derived(60.0, 60.0, "gone/t1", solver_config_name=None, solver_config_identity=None),
        ]
    )
    table = metrics.grades_by_assignment(trials)
    assert len(table) == 2
    assert table.iloc[0]["solver_config_name"] == "solver"  # NA keys sort last
    unknown = table.iloc[1]
    assert pd.isna(unknown["solver_config_name"])
    assert pd.isna(unknown["solver_config_identity"])
    assert float(unknown["mean_base_pct"]) == 60.0


def ladder_rows() -> list[dict[str, object]]:
    return [
        derived(80.0, 90.0, "sj/t1"),
        derived(90.0, 100.0, "sj/t1"),
        derived(70.0, 80.0, "sj/t2"),
        derived(50.0, 60.0, "sj/t3", assignment_id="HW2"),
    ]


def test_grades_by_course_macro_average_and_coverage() -> None:
    trials = trials_frame(
        [
            *ladder_rows(),
            # Three assignments were attempted by this solver config;
            # any solve trial counts toward coverage, verified or not.
            solved("completed"),
            solved("completed", assignment_id="HW2"),
            solved("contract_failed", assignment_id="HW3"),
        ]
    )
    table = metrics.grades_by_course(trials, seed=42)
    assert list(table.columns) == COURSE_COLUMNS
    assert len(table) == 1
    row = table.iloc[0]
    assert row["solver_config_name"] == "solver"
    assert row["grading_config_name"] == "grader"
    assert row["course_id"] == "C1"
    assert row["n_assignments"] == 2
    assert row["n_assignments_total"] == 3  # "2 of 3 assignments" coverage
    assert float(row["macro_mean_base_pct"]) == pytest.approx(63.75)  # (77.5 + 50) / 2
    assert float(row["macro_mean_score_pct"]) == pytest.approx(73.75)  # (87.5 + 60) / 2
    # Below MIN_BOOTSTRAP_CLUSTERS assignments there is no interval.
    assert pd.isna(row["ci_low"])
    assert pd.isna(row["ci_high"])


def test_grades_by_course_total_falls_back_without_solve_rows() -> None:
    row = metrics.grades_by_course(trials_frame(ladder_rows()), seed=42).iloc[0]
    assert row["n_assignments"] == 2
    assert row["n_assignments_total"] == 2


def test_grades_by_course_total_never_below_the_graded_count() -> None:
    # Solve rows cover only HW1, but HW1 and HW2 both have solve-derived
    # gradings — each proves its solve trial existed even though that
    # trial's own result is gone, so the denominator never drops below
    # the graded count.
    trials = trials_frame([*ladder_rows(), solved("completed")])
    row = metrics.grades_by_course(trials, seed=42).iloc[0]
    assert row["n_assignments"] == 2
    assert row["n_assignments_total"] == 2


def bootstrap_trials(bases: tuple[float, ...]) -> pd.DataFrame:
    return trials_frame(
        [
            derived(base, base, f"sj/t{index}", assignment_id=f"HW{index}")
            for index, base in enumerate(bases, start=1)
        ]
    )


def test_grades_by_course_bootstrap_is_seeded_and_gated() -> None:
    trials = bootstrap_trials((60.0, 70.0, 80.0, 90.0, 100.0))
    row = metrics.grades_by_course(trials, seed=42).iloc[0]
    assert row["n_assignments"] == 5  # exactly at the cluster gate
    assert float(row["macro_mean_base_pct"]) == pytest.approx(80.0)
    assert 60.0 <= float(row["ci_low"]) < 80.0 < float(row["ci_high"]) <= 100.0
    # Deterministic: the same seed reproduces the interval exactly.
    again = metrics.grades_by_course(trials, seed=42).iloc[0]
    assert float(again["ci_low"]) == float(row["ci_low"])
    assert float(again["ci_high"]) == float(row["ci_high"])

    gated = metrics.grades_by_course(bootstrap_trials((60.0, 70.0, 80.0, 90.0)), seed=42).iloc[0]
    assert gated["n_assignments"] == 4
    assert pd.isna(gated["ci_low"])
    assert pd.isna(gated["ci_high"])


def test_grades_by_course_bootstrap_seed_changes_the_interval() -> None:
    # Enough irregular assignments that the resampled means are not a
    # few discrete atoms: with five evenly spaced values, any seed's
    # 2.5th percentile lands on the same atom and the check would be
    # vacuous.
    trials = bootstrap_trials(
        (52.7, 61.3, 64.9, 68.2, 72.9, 75.1, 78.4, 83.6, 87.5, 90.1, 95.8, 99.7)
    )
    first = metrics.grades_by_course(trials, seed=42).iloc[0]
    other = metrics.grades_by_course(trials, seed=7).iloc[0]
    assert (float(other["ci_low"]), float(other["ci_high"])) != (
        float(first["ci_low"]),
        float(first["ci_high"]),
    )


def test_judge_quality_repeats_agreement_and_flag_rates(tmp_path: Path) -> None:
    trials = trials_frame(
        [
            # Item X graded three times; a real and a pseudo-student mix
            # pins that judge quality includes pseudo-students.
            graded(80.0, 80.0, trial_name="g1", item_id="X", item_identity="Xi", student_id="stu1"),
            graded(
                90.0,
                90.0,
                trial_name="g2",
                item_id="X",
                item_identity="Xi",
                sums_consistent=False,
                late_exception=True,
                student_id="_reference",
            ),
            graded(100.0, 100.0, trial_name="g3", item_id="X", item_identity="Xi"),
            # A load-error valid grading: scores kept, sums NA, no criteria rows.
            graded(
                50.0,
                50.0,
                trial_name="y1",
                item_id="Y",
                item_identity="Yi",
                sums_consistent=None,
                grading_load_error=True,
            ),
            failed_grading("timeout", trial_name="f1"),
        ]
    )
    # Criterion c appears only in g2: unshared ids never enter agreement.
    criteria = criteria_frame(
        [
            crit("g1", "a", 5.0, 5.0),
            crit("g1", "b", 3.0, 3.0),
            crit("g2", "a", 5.0, 5.0),
            crit("g2", "b", 2.0, 3.0),
            crit("g2", "c", 1.0, 1.0),
            crit("g3", "a", 4.0, 5.0),
            crit("g3", "b", 3.0, 3.0),
        ]
    )
    table = metrics.judge_quality(trials, criteria, data_root=tmp_path)
    assert list(table.columns) == JUDGE_COLUMNS
    assert len(table) == 1
    row = table.iloc[0]
    assert row["config_name"] == "grader"
    assert row["config_identity"] == "G"
    assert row["n_trials"] == 5
    assert row["n_valid_gradings"] == 4
    assert row["n_items_repeated"] == 1
    assert float(row["mean_within_item_sd"]) == pytest.approx(10.0)  # SD of [80, 90, 100]
    assert float(row["max_within_item_range"]) == pytest.approx(20.0)
    # Pairs (g1,g2), (g1,g3), (g2,g3), shared ids a and b each: six
    # comparisons, diffs 0,1 / 1,0 / 1,1 -> two exact, mean 4/6.
    assert float(row["criterion_exact_agreement_rate"]) == pytest.approx(1 / 3)
    assert float(row["criterion_mean_abs_diff"]) == pytest.approx(2 / 3)
    # No rubric lineage recorded: every criterion-bearing grading is
    # unresolvable, and the rate has no denominator.
    assert pd.isna(row["rubric_fidelity_rate"])
    assert row["n_rubric_unresolved"] == 3
    assert float(row["sums_consistent_rate"]) == pytest.approx(2 / 3)  # y1 is not checkable
    assert float(row["late_exception_rate"]) == pytest.approx(1 / 5)
    assert float(row["grading_load_error_rate"]) == pytest.approx(1 / 4)
    assert float(row["rate_completed"]) == pytest.approx(4 / 5)
    assert float(row["rate_timeout"]) == pytest.approx(1 / 5)
    assert float(row["rate_agent_error"]) == 0.0
    assert float(row["rate_unknown"]) == 0.0


def write_rubric(data_root: Path, assignment: str, text: str) -> str:
    path = data_root / "courses" / "C1" / "rubrics" / assignment / "default.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return sha256_file(path)


def test_judge_quality_rubric_fidelity(tmp_path: Path) -> None:
    sha = write_rubric(
        tmp_path,
        "HW1",
        "# HW1 rubric\n\nProse about the assignment.\n\n"
        "- `a` (5 points): the answer is correct.\n"
        "- `b` (3 points, bonus): extra polish.\n",
    )
    unparseable_sha = write_rubric(tmp_path, "HW2", "- `x` (zero points): malformed.\n")

    def fidelity_row(name: str, rubric_sha256: str, assignment: str = "HW1") -> dict[str, object]:
        return graded(
            75.0,
            75.0,
            trial_name=name,
            item_id=name,
            item_identity=f"{name}-id",
            assignment_id=assignment,
            rubric_name="default",
            rubric_sha256=rubric_sha256,
        )

    trials = trials_frame(
        [
            fidelity_row("m1", sha),
            fidelity_row("m2", sha),
            fidelity_row("m3", sha),
            fidelity_row("m4", sha),
            fidelity_row("m5", "0" * 64),  # hash mismatch: not the graded rubric
            fidelity_row("m6", unparseable_sha, assignment="HW2"),
            fidelity_row("m7", "1" * 64, assignment="HW3"),  # no rubric file at all
        ]
    )
    criteria = criteria_frame(
        [
            # m1 matches the rubric: same ids, max points, bonus flags.
            # Awarded points and titles deliberately differ from the
            # rubric — they never enter fidelity.
            crit("m1", "a", 4.0, 5.0),
            crit("m1", "b", 1.0, 3.0, bonus=True),
            crit("m2", "a", 4.0, 4.0),  # wrong max points
            crit("m2", "b", 1.0, 3.0, bonus=True),
            crit("m3", "a", 4.0, 5.0),
            crit("m3", "b", 1.0, 3.0),  # wrong bonus flag
            crit("m4", "a", 4.0, 5.0),
            crit("m4", "b", 1.0, 3.0, bonus=True),
            crit("m4", "d", 1.0, 1.0),  # extra id
            crit("m5", "a", 4.0, 5.0),
            crit("m5", "b", 1.0, 3.0, bonus=True),
            crit("m6", "a", 4.0, 5.0),
            crit("m6", "b", 1.0, 3.0, bonus=True),
            crit("m7", "a", 4.0, 5.0),
            crit("m7", "b", 1.0, 3.0, bonus=True),
        ]
    )
    row = metrics.judge_quality(trials, criteria, data_root=tmp_path).iloc[0]
    assert row["n_valid_gradings"] == 7
    # m1 faithful; m2-m4 resolvable but unfaithful; m5-m7 unresolvable.
    assert float(row["rubric_fidelity_rate"]) == pytest.approx(1 / 4)
    assert row["n_rubric_unresolved"] == 3
    assert pd.isna(row["criterion_exact_agreement_rate"])  # no repeated items


def test_student_grades_per_student_means_and_flags() -> None:
    trials = trials_frame(
        [
            student(80.0, 90.0, "stu1"),
            student(90.0, 100.0, "stu1", sums_consistent=False, late_exception=True),
            student(70.0, 80.0, "stu1", sums_consistent=None, grading_load_error=True),
            failed_grading(submission_source="student", student_id="stu1"),
            student(40.0, 50.0, "stu2"),
            student(96.0, 96.0, "_reference"),  # pseudo-student: grader_checks only
            derived(60.0, 60.0, "sj/t1"),  # solve-derived: no student
        ]
    )
    table = metrics.student_grades(trials)
    assert list(table.columns) == STUDENT_COLUMNS
    assert list(table["student_id"]) == ["stu1", "stu2"]

    stu1 = row_where(table, "student_id", "stu1")
    assert stu1["n_valid_gradings"] == 3
    assert float(stu1["mean_score_pct"]) == pytest.approx(90.0)  # mean of [90, 100, 80]
    assert float(stu1["sd_score_pct"]) == pytest.approx(10.0)
    assert float(stu1["mean_base_pct"]) == pytest.approx(80.0)  # mean of [80, 90, 70]
    assert float(stu1["sd_base_pct"]) == pytest.approx(10.0)
    assert stu1["n_failed_gradings"] == 1
    assert stu1["n_sums_inconsistent"] == 1
    assert stu1["n_late_exception"] == 1
    assert stu1["n_load_error"] == 1

    stu2 = row_where(table, "student_id", "stu2")
    assert stu2["n_valid_gradings"] == 1
    assert float(stu2["mean_score_pct"]) == 50.0
    assert pd.isna(stu2["sd_score_pct"])  # one grading: no repeat SD
    assert float(stu2["mean_base_pct"]) == 40.0
    assert stu2["n_failed_gradings"] == 0


def test_class_distribution_over_per_student_means() -> None:
    trials = trials_frame(
        [
            student(70.0, 70.0, "stu1"),
            student(80.0, 80.0, "stu2"),
            student(90.0, 90.0, "stu3"),
            # stu4 has no valid grading, hence no mean and no entry.
            failed_grading(submission_source="student", student_id="stu4"),
        ]
    )
    table = metrics.class_distribution(metrics.student_grades(trials))
    assert list(table.columns) == CLASS_COLUMNS
    assert len(table) == 1
    row = table.iloc[0]
    assert row["config_name"] == "grader"
    assert row["course_id"] == "C1"
    assert row["assignment_id"] == "HW1"
    assert row["n_students"] == 3
    assert float(row["mean"]) == pytest.approx(80.0)
    assert float(row["median"]) == pytest.approx(80.0)
    assert float(row["sd"]) == pytest.approx(10.0)
    assert float(row["q25"]) == pytest.approx(75.0)
    assert float(row["q75"]) == pytest.approx(85.0)


def test_grader_checks_roles_and_raw_numbers() -> None:
    trials = trials_frame(
        [
            student(96.0, 100.0, "_reference"),
            student(98.0, 98.0, "_reference"),
            student(3.0, 3.0, "_irrelevant"),
            failed_grading(submission_source="student", student_id="_irrelevant"),
            student(40.0, 40.0, "_probe"),
            student(80.0, 80.0, "stu1"),  # real student: student_grades only
        ]
    )
    table = metrics.grader_checks(trials)
    assert list(table.columns) == CHECK_COLUMNS
    assert list(table["student_id"]) == ["_irrelevant", "_probe", "_reference"]
    assert list(table["role"]) == ["irrelevant", "other", "reference"]

    reference = row_where(table, "student_id", "_reference")
    assert reference["n_valid_gradings"] == 2
    assert float(reference["mean_base_pct"]) == pytest.approx(97.0)
    assert float(reference["min_base_pct"]) == 96.0
    assert float(reference["max_base_pct"]) == 98.0
    assert float(reference["mean_score_pct"]) == pytest.approx(99.0)

    irrelevant = row_where(table, "student_id", "_irrelevant")
    assert irrelevant["n_valid_gradings"] == 1
    assert float(irrelevant["mean_base_pct"]) == 3.0


def test_failure_accounting_includes_zero_count_categories() -> None:
    trials = trials_frame(
        [
            solved("completed"),
            solved("completed"),
            solved("timeout"),
            graded(80.0, 80.0),
        ]
    )
    table = metrics.failure_accounting(trials)
    assert list(table.columns) == FAILURE_COLUMNS
    # Every category for both (stage, config) pairs, zero counts included.
    assert len(table) == 2 * len(OUTCOME_CATEGORIES)
    assert table.iloc[0]["stage"] == "grade"  # sorted by the key columns

    grade_rows = table[table["stage"] == "grade"]
    assert list(grade_rows["outcome"]) == sorted(OUTCOME_CATEGORIES)
    grade_completed = row_where(grade_rows, "outcome", "completed")
    assert grade_completed["n_trials"] == 1
    assert float(grade_completed["share"]) == 1.0
    grade_timeout = row_where(grade_rows, "outcome", "timeout")
    assert grade_timeout["n_trials"] == 0
    assert float(grade_timeout["share"]) == 0.0

    solve_rows = table[table["stage"] == "solve"]
    assert row_where(solve_rows, "outcome", "completed")["n_trials"] == 2
    assert float(row_where(solve_rows, "outcome", "completed")["share"]) == pytest.approx(2 / 3)
    assert float(row_where(solve_rows, "outcome", "timeout")["share"]) == pytest.approx(1 / 3)
    assert float(solve_rows["share"].sum()) == pytest.approx(1.0)


def test_ungraded_solve_trials_names_every_missing_trial() -> None:
    trials = trials_frame(
        [
            solved("completed", trial_name="s0"),
            solved("completed", trial_name="s1", assignment_id="HW2"),
            solved("agent_error", trial_name="s2", assignment_id="HW3"),
            derived(90.0, 90.0, "J/s0", solve_job_name="J", solve_trial_name="s0"),
            # A failed grading is not a valid grading: s1 stays ungraded.
            failed_grading(
                submission_source="solve-trial", solve_job_name="J", solve_trial_name="s1"
            ),
        ]
    )
    table = metrics.ungraded_solve_trials(trials)
    assert list(table.columns) == UNGRADED_COLUMNS
    assert [
        (row["assignment_id"], row["trial_name"], row["outcome"]) for _, row in table.iterrows()
    ] == [
        ("HW2", "s1", "completed"),
        ("HW3", "s2", "agent_error"),
    ]


def test_empty_inputs_yield_full_columns(tmp_path: Path) -> None:
    empty = trials_frame([])
    no_criteria = criteria_frame([])
    expected = {
        "solve_summary": (metrics.solve_summary(empty), SOLVE_SUMMARY_COLUMNS),
        "grades_by_assignment": (metrics.grades_by_assignment(empty), ASSIGNMENT_COLUMNS),
        "grades_by_course": (metrics.grades_by_course(empty, seed=42), COURSE_COLUMNS),
        "judge_quality": (
            metrics.judge_quality(empty, no_criteria, data_root=tmp_path),
            JUDGE_COLUMNS,
        ),
        "student_grades": (metrics.student_grades(empty), STUDENT_COLUMNS),
        "class_distribution": (
            metrics.class_distribution(metrics.student_grades(empty)),
            CLASS_COLUMNS,
        ),
        "grader_checks": (metrics.grader_checks(empty), CHECK_COLUMNS),
        "failure_accounting": (metrics.failure_accounting(empty), FAILURE_COLUMNS),
        "ungraded_solve_trials": (metrics.ungraded_solve_trials(empty), UNGRADED_COLUMNS),
    }
    for name, (table, columns) in expected.items():
        assert list(table.columns) == columns, name
        assert table.empty, name
    # Counts stay int64 and statistics nullable Float64 even when empty.
    assert str(expected["solve_summary"][0].dtypes["n_trials"]) == "int64"
    assert str(expected["solve_summary"][0].dtypes["contract_pass_rate"]) == "Float64"
    assert str(expected["failure_accounting"][0].dtypes["share"]) == "Float64"
    assert str(expected["grader_checks"][0].dtypes["role"]) == "string"


def test_rubric_fidelity_resolves_a_superseded_version_from_the_archive(tmp_path: Path) -> None:
    """A grading made before `default` advanced still resolves, by hash."""
    old_text = (
        "# HW1 rubric\n\n- `a` (5 points): the answer is correct.\n"
        "- `b` (3 points, bonus): extra polish.\n"
    )
    old_sha = write_rubric(tmp_path, "HW1", old_text)
    rubric_path = tmp_path / "courses" / "C1" / "rubrics" / "HW1" / "default.md"
    archive = rubric_path.parent / "archive"
    archive.mkdir()
    (archive / f"{old_sha[:8]}.md").write_text(old_text, encoding="utf-8")
    # `default` advances: same ids, a corrected max.
    rubric_path.write_text(
        "# HW1 rubric\n\n- `a` (6 points): the answer is correct.\n"
        "- `b` (3 points, bonus): extra polish.\n",
        encoding="utf-8",
    )
    new_sha = sha256_file(rubric_path)
    assert new_sha != old_sha

    def row(name: str, rubric_sha256: str) -> dict[str, object]:
        return graded(
            75.0,
            75.0,
            trial_name=name,
            item_id=name,
            item_identity=f"{name}-id",
            assignment_id="HW1",
            rubric_name="default",
            rubric_sha256=rubric_sha256,
        )

    trials = trials_frame([row("old", old_sha), row("new", new_sha)])
    criteria = criteria_frame(
        [
            crit("old", "a", 4.0, 5.0),  # faithful to the archived version
            crit("old", "b", 1.0, 3.0, bonus=True),
            crit("new", "a", 4.0, 6.0),  # faithful to the current version
            crit("new", "b", 1.0, 3.0, bonus=True),
        ]
    )
    judge = metrics.judge_quality(trials, criteria, data_root=tmp_path).iloc[0]
    assert judge["n_rubric_unresolved"] == 0
    assert float(judge["rubric_fidelity_rate"]) == pytest.approx(1.0)


def test_a_revised_rubric_never_averages_across_versions() -> None:
    """Two versions of one assignment are separate rows, and drop out of the course mean."""
    trials = trials_frame(
        [
            solved("completed", assignment_id="HW1"),
            solved("completed", assignment_id="HW2"),
            # One solve trial, graded under two rubric versions: the
            # rubric bytes are in the per-item identity, so these are
            # separate items, never repeats of one.
            derived(
                60.0,
                60.0,
                "sj/t1",
                item_identity="v1",
                assignment_id="HW1",
                rubric_sha256="a" * 64,
            ),
            derived(
                90.0,
                90.0,
                "sj/t1",
                item_identity="v2",
                assignment_id="HW1",
                rubric_sha256="b" * 64,
            ),
            derived(80.0, 80.0, "sj/t2", assignment_id="HW2", rubric_sha256="a" * 64),
        ]
    )
    by_assignment = metrics.grades_by_assignment(trials)
    hw1 = by_assignment[by_assignment["assignment_id"] == "HW1"]
    assert list(hw1["mean_base_pct"]) == [60.0, 90.0]

    course = metrics.grades_by_course(trials).iloc[0]
    # HW1 has no single score, so only HW2 enters the macro-mean — and
    # the dropped assignment is counted, never silently omitted.
    assert course["n_assignments"] == 1
    assert course["n_assignments_mixed_rubric"] == 1
    assert course["n_assignments_total"] == 2
    assert float(course["macro_mean_base_pct"]) == pytest.approx(80.0)
