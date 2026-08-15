from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_assessment_toolkit import __version__, metrics
from agentic_assessment_toolkit.data_root import DataRootError
from agentic_assessment_toolkit.harbor import RunRecordItem, utc_stamp
from agentic_assessment_toolkit.report import REPORT_FILENAMES, write_report
from tests.test_data_root import make_fake_toolkit_repo
from tests.test_metrics import (
    ASSIGNMENT_COLUMNS,
    CHECK_COLUMNS,
    CONSISTENCY_COLUMNS,
    COURSE_COLUMNS,
    FAILURE_COLUMNS,
    JUDGE_COLUMNS,
    NEAR_TIMEOUT_COLUMNS,
    REVIEW_QUEUE_COLUMNS,
    SOLVE_SUMMARY_COLUMNS,
    STUDENT_COLUMNS,
    UNGRADED_COLUMNS,
)
from tests.test_results import (
    CRITERIA_COLUMNS,
    GRADE_JOB,
    SOLVE_JOB,
    TRIALS_COLUMNS,
    criterion,
    graded_rewards,
    grading_data,
    make_job,
    solve_item,
    solve_trial_item,
    student_item,
    trial_result,
    write_task_toml,
    write_trial,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

# CSV name -> header contract; the column constants live beside the
# loader and metrics tests so the contract is single-sourced in tests.
CSV_HEADERS = {
    "trials.csv": TRIALS_COLUMNS,
    "criteria.csv": CRITERIA_COLUMNS,
    "solve_summary.csv": SOLVE_SUMMARY_COLUMNS,
    "grades_by_assignment.csv": ASSIGNMENT_COLUMNS,
    "grades_by_course.csv": COURSE_COLUMNS,
    "students.csv": STUDENT_COLUMNS,
    "judge_quality.csv": JUDGE_COLUMNS,
    "repeat_consistency.csv": CONSISTENCY_COLUMNS,
    "review_queue.csv": REVIEW_QUEUE_COLUMNS,
    "grader_checks.csv": CHECK_COLUMNS,
    "failures.csv": FAILURE_COLUMNS,
    "near_timeouts.csv": NEAR_TIMEOUT_COLUMNS,
    "ungraded_solves.csv": UNGRADED_COLUMNS,
}


def build_root(base: Path) -> Path:
    """One verified solve trial plus one grading job over three sources.

    The grading job grades the solve trial (solve-derived), a real
    student, and a `_reference` pseudo-student, so every report section
    has data. Each grading scores base 80, score 100 (bonus included).
    """
    root = base / "root"
    solve_dir = make_job(
        root,
        base,
        stage="solve",
        job_name=SOLVE_JOB,
        items=[solve_item("HW1")],
        config_identity="d" * 64,
    )
    write_trial(solve_dir, "HW1__abc1234", trial_result("HW1", rewards={"reward": 1.0}))
    grade_dir = make_job(
        root,
        base,
        stage="grade",
        job_name=GRADE_JOB,
        items=[
            solve_trial_item("g1", SOLVE_JOB, "HW1__abc1234"),
            student_item("g2"),
            student_item("g3", student="_reference"),
        ],
    )
    data = grading_data([criterion("a", 4.0, 5.0), criterion("b", 1.0, 2.0, bonus=True)])
    rewards = graded_rewards(data)
    for task in ("g1", "g2", "g3"):
        write_trial(
            grade_dir,
            f"{task}__t1",
            trial_result(task, rewards=rewards),
            artifact_text=json.dumps(data),
        )
    return root


def run_report(
    root: Path,
    *,
    courses: list[str] | None = None,
    assignments: list[str] | None = None,
    config_names: list[str] | None = None,
    seed: int = 42,
    out_root: Path | None = None,
) -> Path:
    return write_report(
        root,
        courses=courses,
        assignments=assignments,
        config_names=config_names,
        seed=seed,
        out_root=out_root,
        now=NOW,
    )


def csv_lines(report_dir: Path, name: str) -> list[str]:
    return (report_dir / name).read_text(encoding="utf-8").splitlines()


def test_write_report_writes_exactly_the_contracted_files(tmp_path: Path) -> None:
    report_dir = run_report(build_root(tmp_path))
    assert report_dir == tmp_path / "root" / "analysis" / f"{utc_stamp(NOW)}__report"
    assert REPORT_FILENAMES == (
        "trials.csv",
        "criteria.csv",
        "solve_summary.csv",
        "grades_by_assignment.csv",
        "grades_by_course.csv",
        "students.csv",
        "judge_quality.csv",
        "repeat_consistency.csv",
        "review_queue.csv",
        "grader_checks.csv",
        "failures.csv",
        "near_timeouts.csv",
        "ungraded_solves.csv",
        "report.md",
        "provenance.json",
    )
    assert sorted(entry.name for entry in report_dir.iterdir()) == sorted(REPORT_FILENAMES)
    for name, columns in CSV_HEADERS.items():
        assert csv_lines(report_dir, name)[0] == ",".join(columns), name
    assert len(csv_lines(report_dir, "trials.csv")) == 1 + 4
    assert len(csv_lines(report_dir, "criteria.csv")) == 1 + 6


def test_report_md_sections_and_benchmark_prose(tmp_path: Path) -> None:
    report_dir = run_report(build_root(tmp_path))
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# Assessment report\n")
    assert f"- Data root: {tmp_path / 'root'}" in report
    assert "- Filters: courses all; assignments all; configs all" in report

    assert "## Benchmark results" in report
    assert (
        "### SYN_C1 — solver codex-high (dddddddd), grader codex-grader-sol-high (cccccccc)"
        in report
    )
    # The per-assignment means table is always printed; the lone solve
    # trial scores base 80, score 100, and has no spread. The rubric
    # version it was graded against is named beside the assignment.
    assert "| HW1 | abababab | 1 | 1 | 80.00 |  | 100.00 |" in report
    # One assignment is below the cluster gate: the explicit note
    # replaces the interval, and coverage is stated.
    assert "fewer than the 5 the bootstrap needs" in report
    assert "Coverage: 1 of 1 assignments" in report

    assert "## Judge quality" in report
    assert "## Cross-run consistency" in report
    # No item is graded twice in this root, so the section is prose only.
    assert "No item in this report was graded more than once under one config." in report
    assert "## Review queue" in report
    # Three single gradings, none repeated or judged: rows exist (unlike
    # repeat consistency), but none is flagged.
    assert "3 row(s); 0 with a final-judge grading" in report
    assert "No row is flagged; the full queue is in review_queue.csv." in report
    assert "## Grader checks" in report
    assert "_reference" in report
    assert "at or above 95" in report and "at or below 5" in report
    assert "## Grading assistant" in report
    assert "## Failure accounting" in report
    assert "## Near-timeout trials" in report
    # No trial in this root has a duration or a materialized task.toml.
    assert "0 of 0 measured trial(s) near timeout; 4 trial(s) not measurable." in report


def test_review_queue_section_renders_flagged_rows() -> None:
    """The flagged-table branch must render every display column: a
    column dropped from the metrics frame would otherwise raise only in
    production, on precisely the roots with disagreements."""
    from agentic_assessment_toolkit.report import _REVIEW_TABLE_COLUMNS, _review_queue_section
    from tests.test_metrics import judged, repeated, trials_frame

    queue = metrics.review_queue(
        trials_frame(
            [
                # Wide initial range (60) plus a final outside it.
                repeated(90.0, "x1"),
                repeated(30.0, "x2"),
                judged(95.0, "jx"),
            ]
        )
    )
    section = "\n\n".join(_review_queue_section(queue))
    assert "1 with a final-judge grading, 1 of those outside the initial range" in section
    assert "1 row(s) with an initial score range above 20" in section
    for column in _REVIEW_TABLE_COLUMNS:
        assert f" {column} " in section


def test_empty_data_root_report_is_header_only(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    report_dir = run_report(root)
    for name, columns in CSV_HEADERS.items():
        assert (report_dir / name).read_text(encoding="utf-8") == ",".join(columns) + "\n", name
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    for line in (
        "No solve-derived gradings in this report.",
        "No grading trials in this report.",
        "No item in this report was graded more than once under one config.",
        "No valid gradings in this report.",
        "No grader-check pseudo-students in this report.",
        "No student submissions in this report.",
        "No trials in this report.",
    ):
        assert line in report


def test_provenance_records_the_computation(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    report_dir = run_report(
        root, courses=["SYN_C1"], config_names=["codex-high", "codex-grader-sol-high"], seed=7
    )
    provenance = json.loads((report_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["created_utc"] == NOW.isoformat()
    assert provenance["toolkit_version"] == __version__
    assert provenance["data_root"] == str(root)
    assert provenance["filters"] == {
        "courses": ["SYN_C1"],
        "assignments": None,
        "configs": ["codex-high", "codex-grader-sol-high"],
    }
    assert provenance["seed"] == 7
    assert provenance["bootstrap"] == {
        "resamples": metrics.BOOTSTRAP_RESAMPLES,
        "confidence": metrics.BOOTSTRAP_CONFIDENCE,
        "min_clusters": metrics.MIN_BOOTSTRAP_CLUSTERS,
    }
    assert provenance["flag_thresholds"] == {
        "repeat_range_pct": metrics.REPEAT_RANGE_FLAG_PCT,
        "repeat_deviation_pct": metrics.REPEAT_DEVIATION_FLAG_PCT,
        "near_timeout_fraction": metrics.NEAR_TIMEOUT_FRACTION,
    }
    assert provenance["configs"] == [
        {"name": "codex-grader-sol-high", "identity": "c" * 64, "stage": "grade"},
        {"name": "codex-high", "identity": "d" * 64, "stage": "solve"},
    ]
    assert provenance["jobs"] == {"grade": [GRADE_JOB], "solve": [SOLVE_JOB]}
    assert provenance["n_trials"] == 4
    assert provenance["n_criteria_rows"] == 6


def test_config_filter_keeps_gradings_via_solver_name(tmp_path: Path) -> None:
    report_dir = run_report(build_root(tmp_path), config_names=["codex-high"])
    trials = csv_lines(report_dir, "trials.csv")
    # The solve trial is named directly; the solve-derived grading
    # survives through its solver_config_name; the student and
    # pseudo-student gradings are filtered out.
    assert len(trials) == 1 + 2
    body = "\n".join(trials[1:])
    assert "g1__t1" in body
    assert "g2__t1" not in body and "g3__t1" not in body
    # Criteria rows follow their trials.
    criteria = csv_lines(report_dir, "criteria.csv")
    assert len(criteria) == 1 + 2
    assert all("g1__t1" in line for line in criteria[1:])


def test_course_and_assignment_filters(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    kept = run_report(root, assignments=["HW1"])
    assert len(csv_lines(kept, "trials.csv")) == 1 + 4
    dropped = run_report(root, courses=["OTHER"])
    assert dropped.name == f"{utc_stamp(NOW)}__report-2"  # same-second collision suffix
    assert csv_lines(dropped, "trials.csv") == [",".join(TRIALS_COLUMNS)]
    assert csv_lines(dropped, "criteria.csv") == [",".join(CRITERIA_COLUMNS)]


def test_same_seed_and_now_reproduce_bytes(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    out = tmp_path / "out"
    first = run_report(root, out_root=out)
    second = run_report(root, out_root=out)
    assert first != second
    for name in REPORT_FILENAMES:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_out_root_that_is_a_file_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    destination = tmp_path / "occupied"
    destination.write_text("not a directory", encoding="utf-8")
    with pytest.raises(DataRootError, match="not a directory"):
        run_report(root, out_root=destination)


def test_out_root_inside_toolkit_tree_is_refused(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    clone = make_fake_toolkit_repo(tmp_path)
    destination = clone / "analysis"
    with pytest.raises(DataRootError, match="inside the toolkit repository"):
        run_report(root, out_root=destination)
    assert not destination.exists()


def test_two_rubric_versions_are_reported_separately(tmp_path: Path) -> None:
    """A revised rubric splits the assignment's row and is called out."""
    root = build_root(tmp_path)
    # A second grading of the same solve trial, against a revised rubric.
    revised = make_job(
        root,
        tmp_path,
        stage="grade",
        job_name="20260801T130000Z__codex-grader-sol-high__cccccccc",
        items=[
            RunRecordItem(
                item_id=f"{SOLVE_JOB}/HW1__abc1234",
                task_dir_name="g4",
                item_identity="v2" * 32,
                course_id="SYN_C1",
                assignment_id="HW1",
                input_hashes={"rubric": "f" * 64},
                submission_source="solve-trial",
                solve_job_name=SOLVE_JOB,
                solve_trial_name="HW1__abc1234",
            )
        ],
    )
    data = grading_data([criterion("a", 5.0, 5.0)])
    write_trial(
        revised,
        "g4__t1",
        trial_result("g4", rewards=graded_rewards(data)),
        artifact_text=json.dumps(data),
    )

    report_dir = run_report(root)
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    # One table row per version, never one averaged row.
    assert "| HW1 | abababab |" in report
    assert "| HW1 | ffffffff |" in report
    # The assignment has no single score, so it leaves the macro-mean —
    # and the report says so rather than dropping it quietly. Coverage
    # still counts it as graded, because it was.
    assert "Coverage: 1 of 1 assignments" in report
    assert "Of those, 1 were graded against more than one rubric version" in report
    assert "the macro-mean, which covers 0" in report
    course = [line for line in csv_lines(report_dir, "grades_by_course.csv")[1:] if line]
    assert len(course) == 1
    assert course[0].split(",")[COURSE_COLUMNS.index("n_assignments")] == "0"
    assert course[0].split(",")[COURSE_COLUMNS.index("n_assignments_mixed_rubric")] == "1"


def test_consistency_section_lists_a_flagged_repeat_group(tmp_path: Path) -> None:
    """A wildly disagreeing regrade of one item is flagged, never excluded."""
    root = build_root(tmp_path)
    repeat_job = "20260801T140000Z__codex-grader-sol-high__cccccccc"
    repeat = make_job(
        root, tmp_path, stage="grade", job_name=repeat_job, items=[student_item("g2")]
    )
    # The same student item as build_root's g2 (base 80), regraded at 0.
    data = grading_data([criterion("a", 0.0, 5.0), criterion("b", 0.0, 2.0, bonus=True)])
    write_trial(
        repeat,
        "g2__t2",
        trial_result("g2", rewards=graded_rewards(data)),
        artifact_text=json.dumps(data),
    )

    report_dir = run_report(root)
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "1 of 1 repeated item(s) flagged; 2 grading(s) deviate more than 10 points" in report
    assert "| 0; 80 |" in report  # the score list, sorted ascending
    assert f"{GRADE_JOB}/g2__t1; {repeat_job}/g2__t2" in report
    body = [line for line in csv_lines(report_dir, "repeat_consistency.csv")[1:] if line]
    assert len(body) == 1
    assert "0; 80" in body[0]
    assert "True" in body[0]  # range_flagged
    # Flag only: both gradings still enter the student aggregates.
    students = [line for line in csv_lines(report_dir, "students.csv")[1:] if line]
    stu1 = next(line for line in students if ",stu1," in line)
    assert stu1.split(",")[STUDENT_COLUMNS.index("n_valid_gradings")] == "2"


def test_near_timeout_section_flags_long_trials(tmp_path: Path) -> None:
    root = tmp_path / "root"
    solve_dir = make_job(
        root, tmp_path, stage="solve", job_name=SOLVE_JOB, items=[solve_item("HW1")]
    )
    write_task_toml(root, SOLVE_JOB, "HW1", timeout_sec=3600.0)
    # 2400 s of agent execution against a 3600 s timeout: above 60%.
    write_trial(
        solve_dir,
        "HW1__long",
        trial_result(
            "HW1",
            rewards={"reward": 1.0},
            agent_execution={
                "started_at": "2026-07-31T10:00:00",
                "finished_at": "2026-07-31T10:40:00",
            },
        ),
    )

    report_dir = run_report(root)
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "1 of 1 measured trial(s) near timeout; 0 trial(s) not measurable." in report
    assert f"| solve | {SOLVE_JOB} |" in report
    # The Markdown table drops the 64-char config identity (the
    # consistency-section convention); the CSV keeps the full hash.
    section = report.split("## Near-timeout trials")[1].split("\n## ")[0]
    assert "config_identity" not in section
    assert csv_lines(report_dir, "near_timeouts.csv")[0].split(",") == list(NEAR_TIMEOUT_COLUMNS)
    body = [line for line in csv_lines(report_dir, "near_timeouts.csv")[1:] if line]
    assert len(body) == 1
    fields = body[0].split(",")
    assert fields[NEAR_TIMEOUT_COLUMNS.index("n_near_timeout")] == "1"
    assert fields[NEAR_TIMEOUT_COLUMNS.index("max_agent_execution_sec")] == "2400.0"
    assert fields[NEAR_TIMEOUT_COLUMNS.index("agent_timeout_sec")] == "3600.0"
