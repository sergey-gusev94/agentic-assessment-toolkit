"""Report rendering: CSV tables, report.md, and provenance under ``analysis/``.

The reporting contract of docs/design.md ("Results, statistics, and
reporting"): ``write_report`` loads the tidy tables with
``results.load_results``, applies the row filters, computes every
statistics table from ``metrics``, and writes one timestamped report
directory holding the two tidy tables, the derived CSV tables,
``report.md``, and ``provenance.json``. Everything is read-only over
the data root and regenerable at any time.

The destination is refused inside the toolkit's own repository tree —
the same rule as the data root — because reports contain student
identifiers and grades (docs/data-conventions.md).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__, metrics
from .data_root import DataRootError, ensure_outside_toolkit
from .harbor import create_unique_dir, utc_stamp
from .results import ResultTables, load_results

# Every file a report directory holds; CSVs are written from the frames
# of the same name (plus the two tidy tables).
REPORT_FILENAMES = (
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

# The benchmark ladder keys of grades_by_course; grades_by_assignment
# shares them, so each course row selects its per-assignment rows.
_LADDER_KEYS = (
    "solver_config_name",
    "solver_config_identity",
    "grading_config_name",
    "grading_config_identity",
    "course_id",
)

_ASSIGNMENT_TABLE_COLUMNS = (
    "assignment_id",
    "rubric",
    "n_solve_trials",
    "n_gradings",
    "mean_score_pct",
    "sd_score_pct",
)

# The near-timeout table drops the config identity, whose full hash
# lives in the CSV.
_NEAR_TIMEOUT_TABLE_COLUMNS = (
    "stage",
    "job_name",
    "config_name",
    "n_trials",
    "n_measured",
    "n_unmeasured",
    "n_near_timeout",
    "max_agent_execution_sec",
    "agent_timeout_sec",
)

# The flagged-repeats table keeps the identifying and score columns and
# drops the config identity, whose full hash lives in the CSV.
_CONSISTENCY_TABLE_COLUMNS = (
    "config_name",
    "course_id",
    "assignment_id",
    "student_id",
    "item_id",
    "rubric",
    "n_gradings",
    "score_pct_values",
    "median_score_pct",
    "range_score_pct",
    "n_deviant_gradings",
    "deviant_trials",
)


def write_report(
    data_root: Path,
    *,
    courses: list[str] | None,
    assignments: list[str] | None,
    config_names: list[str] | None,
    seed: int,
    out_root: Path | None,
    now: datetime | None = None,  # injectable for tests, like harbor.utc_stamp
) -> Path:
    """Write one timestamped report directory; return its path.

    Filters: ``courses`` and ``assignments`` keep matching rows; the
    config filter keeps rows whose ``config_name`` is named or — for
    grading rows — whose ``solver_config_name`` is named, so filtering
    to a solver keeps the gradings of its trials. ``None`` means no
    filter. The destination is ``out_root`` or ``<data_root>/analysis``
    and is refused inside the toolkit repository.
    """
    destination = (out_root if out_root is not None else data_root / "analysis").resolve()
    ensure_outside_toolkit(destination, what="report destination")
    if destination.exists() and not destination.is_dir():
        raise DataRootError(f"report destination {destination} exists and is not a directory")

    trials, criteria = _filtered_tables(load_results(data_root), courses, assignments, config_names)
    tables = {
        "solve_summary": metrics.solve_summary(trials),
        "grades_by_assignment": metrics.grades_by_assignment(trials),
        "grades_by_course": metrics.grades_by_course(trials, seed=seed),
        "students": metrics.student_grades(trials),
        "judge_quality": metrics.judge_quality(trials, criteria, data_root=data_root),
        "repeat_consistency": metrics.repeat_consistency(trials),
        "review_queue": metrics.review_queue(trials),
        "grader_checks": metrics.grader_checks(trials),
        "failures": metrics.failure_accounting(trials),
        "near_timeouts": metrics.near_timeouts(trials),
        "ungraded_solves": metrics.ungraded_solve_trials(trials),
    }
    created_utc = (now if now is not None else datetime.now(UTC)).isoformat()

    report_dir = create_unique_dir(destination, f"{utc_stamp(now)}__report")
    trials.to_csv(report_dir / "trials.csv", index=False)
    criteria.to_csv(report_dir / "criteria.csv", index=False)
    for name, frame in tables.items():
        frame.to_csv(report_dir / f"{name}.csv", index=False)
    (report_dir / "report.md").write_text(
        _report_markdown(
            created_utc=created_utc,
            data_root=data_root,
            courses=courses,
            assignments=assignments,
            config_names=config_names,
            seed=seed,
            trials=trials,
            criteria=criteria,
            tables=tables,
        ),
        encoding="utf-8",
    )
    provenance = _provenance(
        created_utc=created_utc,
        data_root=data_root,
        courses=courses,
        assignments=assignments,
        config_names=config_names,
        seed=seed,
        trials=trials,
        criteria=criteria,
    )
    (report_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report_dir


def _filtered_tables(
    tables: ResultTables,
    courses: list[str] | None,
    assignments: list[str] | None,
    config_names: list[str] | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trials = tables.trials
    mask = pd.Series(True, index=trials.index)
    if courses is not None:
        mask &= trials["course_id"].isin(courses)
    if assignments is not None:
        mask &= trials["assignment_id"].isin(assignments)
    if config_names is not None:
        mask &= trials["config_name"].isin(config_names) | trials["solver_config_name"].isin(
            config_names
        )
    trials = trials[mask].reset_index(drop=True)
    # Criteria rows belong to trials: keep exactly the surviving trials'.
    kept = set(zip(trials["job_name"], trials["trial_name"], strict=True))
    criteria = tables.criteria
    criteria_mask = pd.Series(
        [
            (job_name, trial_name) in kept
            for job_name, trial_name in zip(
                criteria["job_name"], criteria["trial_name"], strict=True
            )
        ],
        index=criteria.index,
        dtype=bool,
    )
    return trials, criteria[criteria_mask].reset_index(drop=True)


def _provenance(
    *,
    created_utc: str,
    data_root: Path,
    courses: list[str] | None,
    assignments: list[str] | None,
    config_names: list[str] | None,
    seed: int,
    trials: pd.DataFrame,
    criteria: pd.DataFrame,
) -> dict[str, object]:
    configs = {
        (_opt(name), _opt(identity), _opt(stage))
        for name, identity, stage in zip(
            trials["config_name"], trials["config_identity"], trials["stage"], strict=True
        )
    }
    jobs: dict[str, set[str]] = {"solve": set(), "grade": set()}
    for stage, job_name in zip(trials["stage"], trials["job_name"], strict=True):
        if stage in jobs:
            jobs[stage].add(str(job_name))
    return {
        "created_utc": created_utc,
        "toolkit_version": __version__,
        "data_root": str(data_root),
        "filters": {"courses": courses, "assignments": assignments, "configs": config_names},
        "seed": seed,
        "bootstrap": {
            "resamples": metrics.BOOTSTRAP_RESAMPLES,
            "confidence": metrics.BOOTSTRAP_CONFIDENCE,
            "min_clusters": metrics.MIN_BOOTSTRAP_CLUSTERS,
        },
        "flag_thresholds": {
            "repeat_range_pct": metrics.REPEAT_RANGE_FLAG_PCT,
            "repeat_deviation_pct": metrics.REPEAT_DEVIATION_FLAG_PCT,
            "near_timeout_fraction": metrics.NEAR_TIMEOUT_FRACTION,
        },
        "configs": [
            {"name": name, "identity": identity, "stage": stage}
            for name, identity, stage in sorted(
                configs, key=lambda triple: tuple(value or "" for value in triple)
            )
        ],
        "jobs": {stage: sorted(names) for stage, names in jobs.items()},
        "n_trials": len(trials),
        "n_criteria_rows": len(criteria),
    }


def _report_markdown(
    *,
    created_utc: str,
    data_root: Path,
    courses: list[str] | None,
    assignments: list[str] | None,
    config_names: list[str] | None,
    seed: int,
    trials: pd.DataFrame,
    criteria: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
) -> str:
    parts = [
        "# Assessment report",
        "\n".join(
            [
                f"- Created (UTC): {created_utc}",
                f"- Toolkit version: {__version__}",
                f"- Data root: {data_root}",
                (
                    f"- Filters: courses {_filter_phrase(courses)}; "
                    f"assignments {_filter_phrase(assignments)}; "
                    f"configs {_filter_phrase(config_names)}"
                ),
                (
                    f"- Bootstrap: seed {seed}, {metrics.BOOTSTRAP_RESAMPLES} resamples, "
                    f"{round(metrics.BOOTSTRAP_CONFIDENCE * 100)}% confidence, interval only "
                    f"at {metrics.MIN_BOOTSTRAP_CLUSTERS} or more assignments"
                ),
                f"- Loaded: {len(trials)} trial(s), {len(criteria)} criteria row(s)",
            ]
        ),
    ]
    parts.extend(_benchmark_section(tables["grades_by_assignment"], tables["grades_by_course"]))
    parts.extend(_judge_section(tables["judge_quality"]))
    parts.extend(_consistency_section(tables["repeat_consistency"]))
    parts.extend(_review_queue_section(tables["review_queue"]))
    parts.extend(_checks_section(tables["grader_checks"]))
    parts.extend(_assistant_section(metrics.class_distribution(tables["students"])))
    parts.extend(_failures_section(tables["failures"]))
    parts.extend(_near_timeout_section(tables["near_timeouts"]))
    parts.extend(_ungraded_section(tables["ungraded_solves"]))
    return "\n\n".join(parts) + "\n"


def _benchmark_section(by_assignment: pd.DataFrame, by_course: pd.DataFrame) -> list[str]:
    parts = ["## Benchmark results"]
    if by_course.empty:
        parts.append("No solve-derived gradings in this report.")
        return parts
    parts.append(
        "Scores follow the fixed ladder: grading repeats of one solve trial "
        "average to a per-solve-trial score, solve trials average to the "
        "per-assignment score, and assignments macro-average — each weighing "
        "equally — to the course score. `score_pct` is the bonus-inclusive "
        "gradebook score and can exceed 100. One "
        "subsection per (solver config, grading config, course)."
    )
    for _, row in by_course.iterrows():
        solver = _config_label(row["solver_config_name"], row["solver_config_identity"])
        grader = _config_label(row["grading_config_name"], row["grading_config_identity"])
        parts.append(f"### {_text(row['course_id'])} — solver {solver}, grader {grader}")
        subset = by_assignment[_ladder_mask(by_assignment, row)]
        parts.append(
            "Per-assignment means (`n_solve_trials` graded solve trials, "
            "`n_gradings` gradings; `sd_score_pct` is the spread across "
            "per-solve-trial means). `rubric` is the first 8 characters of the "
            "sha256 of the rubric version graded against — an assignment whose "
            "rubric was revised appears once per version, never averaged across "
            "them:"
        )
        parts.append(_markdown_table(subset, columns=_ASSIGNMENT_TABLE_COLUMNS))
        if row["n_assignments"] == 0:
            # Every graded assignment has more than one rubric version,
            # so there is nothing to average — an empty mean would read
            # as a score of zero.
            macro = (
                "No course macro-mean: every assignment with gradings was graded "
                "against more than one rubric version, so none has a single score."
            )
        else:
            macro = (
                f"Course macro-mean `score_pct` over {row['n_assignments']} "
                f"assignment(s): {_cell(row['macro_mean_score_pct'])}."
            )
            if pd.isna(row["ci_low"]):
                macro += (
                    f" No confidence interval: {row['n_assignments']} assignment(s) with "
                    f"valid gradings is fewer than the {metrics.MIN_BOOTSTRAP_CLUSTERS} "
                    "the bootstrap needs."
                )
            else:
                macro += (
                    f" 95% confidence interval for the macro-mean `score_pct`: "
                    f"{_cell(row['ci_low'])} to {_cell(row['ci_high'])} (percentile "
                    "bootstrap over assignments; seed in provenance.json)."
                )
        parts.append(macro)
        graded = row["n_assignments"] + row["n_assignments_mixed_rubric"]
        parts.append(
            f"Coverage: {graded} of {row['n_assignments_total']} "
            f"assignments — {row['n_assignments_total']} attempted by this solver, "
            f"{graded} with at least one valid grading."
        )
        if row["n_assignments_mixed_rubric"]:
            parts.append(
                f"Of those, {row['n_assignments_mixed_rubric']} were graded against "
                "more than one rubric version under this config. They have no "
                "single per-assignment score, so they are left out of the "
                f"macro-mean, which covers {row['n_assignments']}. Their "
                "per-version means are the rows above."
            )
    return parts


def _judge_section(judge: pd.DataFrame) -> list[str]:
    parts = ["## Judge quality"]
    if judge.empty:
        parts.append("No grading trials in this report.")
        return parts
    parts.append(
        "One row per grading config over its grading trials, pseudo-students "
        "included — judge quality is about the judge. A valid grading is a "
        "completed grading trial with a score (`n_valid_gradings`). Repeat "
        "stability is the within-item SD and range of `score_pct` over items "
        "graded more than once (`n_items_repeated`); per-criterion agreement "
        "compares the criterion ids both gradings of a repeat pair share."
    )
    parts.append(
        "Rubric fidelity is checked over valid gradings whose criteria rows "
        "and rubric are both available: the rubric version is resolved from "
        "the course tree by its recorded hash — across the selectable rubrics "
        "and the archived versions, so a grading made before the rubric was "
        "revised still resolves — and a grading is faithful when its criterion "
        "ids, max points, and bonus flags exactly match that version's. A "
        "grading whose rubric version is on disk nowhere, or does not parse, "
        "is counted in `n_rubric_unresolved` and left out of "
        "`rubric_fidelity_rate`, so the rate is empty when no grading could be "
        "checked; a grading whose stored `grading_result.json` could not be "
        "reloaded has no criteria rows and is counted by "
        "`grading_load_error_rate` instead."
    )
    parts.append(
        "The rate columns differ in denominator: `sums_consistent_rate` is "
        "over valid gradings whose authored sums could be rechecked, "
        "`grading_load_error_rate` over valid gradings, and "
        "`late_exception_rate` and the `rate_<category>` outcome shares over "
        "all of the config's grading trials."
    )
    parts.append(_markdown_table(judge))
    return parts


def _consistency_section(consistency: pd.DataFrame) -> list[str]:
    parts = ["## Cross-run consistency"]
    if consistency.empty:
        parts.append("No item in this report was graded more than once under one config.")
        return parts
    parts.append(
        "One row per repeated item: the same submission graded more than "
        "once under one grading config and rubric version. A group is "
        f"flagged when its `score_pct` range exceeds "
        f"{metrics.REPEAT_RANGE_FLAG_PCT:g} percentage points; a single "
        "grading is flagged when it deviates more than "
        f"{metrics.REPEAT_DEVIATION_FLAG_PCT:g} points from its group "
        "median. Flagged items deserve human review — a wide range means "
        "at least one of the gradings is wrong. The flags exclude "
        "nothing: every grading here still enters every aggregate above. "
        "The full table, unflagged groups included, is "
        "repeat_consistency.csv."
    )
    n_flagged = int((consistency["range_flagged"] | (consistency["n_deviant_gradings"] > 0)).sum())
    parts.append(
        f"{n_flagged} of {len(consistency)} repeated item(s) flagged; "
        f"{int(consistency['n_deviant_gradings'].sum())} grading(s) deviate more than "
        f"{metrics.REPEAT_DEVIATION_FLAG_PCT:g} points from their group median."
    )
    flagged = consistency[consistency["range_flagged"] | (consistency["n_deviant_gradings"] > 0)]
    if flagged.empty:
        parts.append("No repeated item exceeds either threshold.")
    else:
        parts.append(_markdown_table(flagged, columns=_CONSISTENCY_TABLE_COLUMNS))
    return parts


# The review-queue display keeps the identifying and score columns; the
# trial names, paths, and full hashes live in the CSV.
_REVIEW_TABLE_COLUMNS = (
    "config_name",
    "course_id",
    "assignment_id",
    "student_id",
    "rubric",
    "n_initial_gradings",
    "initial_score_pct_values",
    "initial_range_score_pct",
    "n_prior_gradings",
    "prior_score_pct_values",
    "prior_range_score_pct",
    "final_score_pct",
    "final_outside_range",
    "final_distance_outside_range_pct",
    "n_unseen_initial_gradings",
    "n_missing_prior_gradings",
    "review_reasons",
)


def _review_queue_section(queue: pd.DataFrame) -> list[str]:
    parts = ["## Review queue"]
    if queue.empty:
        parts.append("No valid gradings in this report.")
        return parts
    parts.append(
        "The human-review navigation table: one row per graded submission "
        "under one initial grading config (single gradings included), "
        "sorted with final scores outside the judge's prior range first, "
        "farthest outside first, then by complete initial-score range. Rows carry the exact "
        "trial names and per-trial justification paths (relative to the "
        "data root; a final-judge trial's feedback document sits beside "
        "its justification as feedback.md), so a row opens in one step — "
        "`harbor view <data-root>/grading/<job>` browses a named job. "
        "The `initial_*` fields describe every currently loaded initial "
        "grading; the `prior_*` fields describe exactly what the final judge "
        "saw. An outside-range decision is left empty unless all recorded "
        "prior results resolve to one initial group and their count agrees. "
        "`final_distance_outside_range_pct` measures the distance beyond the "
        "nearest prior boundary. `review_reasons` also identifies wide initial "
        "ranges, initial gradings absent from the judge context, missing prior "
        "results, and unresolved lineage. Advisory only: nothing here excludes "
        "any grading from any aggregate. The full table is "
        "review_queue.csv."
    )
    outside = queue["final_outside_range"].fillna(False).astype(bool)
    wide = (
        (queue["initial_range_score_pct"] > metrics.REPEAT_RANGE_FLAG_PCT)
        .fillna(False)
        .astype(bool)
    )
    unseen = queue["n_unseen_initial_gradings"].fillna(0).gt(0)
    missing = queue["n_missing_prior_gradings"].fillna(0).gt(0)
    n_final = int(queue["n_final_gradings"].gt(0).sum())
    parts.append(
        f"{len(queue)} row(s); {n_final} with a final-judge grading, "
        f"{int(outside.sum())} of those outside their complete prior range; "
        f"{int(wide.sum())} row(s) with an initial score range above "
        f"{metrics.REPEAT_RANGE_FLAG_PCT:g} points; {int(unseen.sum())} with "
        f"initial gradings absent from the judge context; {int(missing.sum())} "
        "with recorded prior results unavailable."
    )
    flagged = queue[queue["review_reasons"].notna()]
    if flagged.empty:
        parts.append("No row is flagged; the full queue is in review_queue.csv.")
    else:
        parts.append(_markdown_table(flagged, columns=_REVIEW_TABLE_COLUMNS))
    return parts


def _checks_section(checks: pd.DataFrame) -> list[str]:
    parts = ["## Grader checks"]
    if checks.empty:
        parts.append("No grader-check pseudo-students in this report.")
        return parts
    parts.append(
        "Known submissions graded as pseudo-students, one row per grading "
        "config, course, assignment, pseudo-student, and rubric version. "
        "Advisory thresholds on `score_pct`: a "
        "reference solution should grade at or above 95, an irrelevant "
        "submission at or below 5. The numbers are raw — nothing here is a "
        "machine pass or fail."
    )
    parts.append(_markdown_table(checks))
    return parts


def _assistant_section(class_dist: pd.DataFrame) -> list[str]:
    parts = ["## Grading assistant"]
    if class_dist.empty:
        parts.append("No student submissions in this report.")
        return parts
    parts.append(
        "Class distribution per grading config, course, assignment, and rubric "
        "version, over "
        "per-student mean `score_pct` — one value per student; students with "
        "no valid grading are not counted. Per-student grades are in "
        "students.csv."
    )
    parts.append(_markdown_table(class_dist))
    return parts


def _failures_section(failures: pd.DataFrame) -> list[str]:
    parts = ["## Failure accounting"]
    if failures.empty:
        parts.append("No trials in this report.")
        return parts
    parts.append(
        "Trial counts per stage, config, and outcome category, with each "
        "category's share of that config's trials. Zero counts are listed so "
        "failures are never silently dropped."
    )
    parts.append(_markdown_table(failures))
    return parts


def _near_timeout_section(near: pd.DataFrame) -> list[str]:
    parts = ["## Near-timeout trials"]
    if near.empty:
        parts.append("No trials in this report.")
        return parts
    parts.append(
        "Agent-execution durations against each trial's agent timeout, "
        "one row per job. The timeout is read from the job's materialized "
        "task.toml (times Harbor's timeout multiplier when set), so an "
        "old job keeps the timeout it ran under. A trial above "
        f"{round(metrics.NEAR_TIMEOUT_FRACTION * 100)}% of its timeout counts in "
        "`n_near_timeout` — rising counts here show duration creep before "
        "it becomes timeout failures. A trial whose duration or timeout "
        "is unavailable is counted in `n_unmeasured`, never flagged."
    )
    parts.append(
        f"{int(near['n_near_timeout'].sum())} of {int(near['n_measured'].sum())} measured "
        f"trial(s) near timeout; {int(near['n_unmeasured'].sum())} trial(s) not measurable."
    )
    parts.append(_markdown_table(near, columns=_NEAR_TIMEOUT_TABLE_COLUMNS))
    return parts


def _ungraded_section(ungraded: pd.DataFrame) -> list[str]:
    parts = ["## Ungraded solve trials"]
    if ungraded.empty:
        parts.append("Every solve trial in this report has at least one valid grading.")
        return parts
    parts.append(
        "Solve trials with no valid grading, one named row each — the "
        "direct answer to what is missing from grading and why. A "
        "`completed` trial has a gradable submission that simply has not "
        "been graded (yet, or under the report's filters); any other "
        "outcome never produced one, so grading correctly skipped it."
    )
    parts.append(_markdown_table(ungraded))
    return parts


def _ladder_mask(frame: pd.DataFrame, row: pd.Series[Any]) -> pd.Series[bool]:
    """Rows of ``frame`` matching the course row's ladder keys (NA matches NA)."""
    mask = pd.Series(True, index=frame.index)
    for column in _LADDER_KEYS:
        value = row[column]
        if pd.isna(value):
            mask &= frame[column].isna()
        else:
            mask &= (frame[column] == value).fillna(False)
    return mask


def _short_rubric(frame: pd.DataFrame) -> pd.DataFrame:
    """Render ``rubric_sha256`` as a short ``rubric`` column, for display only.

    The full hash stays in the CSVs, which are what anyone matching a
    row back to a rubric version uses; a 64-character cell in a Markdown
    table only hides the numbers beside it.
    """
    if "rubric_sha256" not in frame.columns:
        return frame
    shortened = frame.copy()
    shortened["rubric_sha256"] = frame["rubric_sha256"].map(
        lambda value: pd.NA if pd.isna(value) else str(value)[:8]
    )
    return shortened.rename(columns={"rubric_sha256": "rubric"})


def _markdown_table(frame: pd.DataFrame, columns: tuple[str, ...] | None = None) -> str:
    frame = _short_rubric(frame)
    shown = frame if columns is None else frame[list(columns)]
    lines = [
        "| " + " | ".join(str(column) for column in shown.columns) + " |",
        "| " + " | ".join("---" for _ in shown.columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in shown.itertuples(index=False)
    )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    """One table cell: NA renders empty (like the CSVs), floats at 2 decimals."""
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _config_label(name: Any, identity: Any) -> str:
    label = "unknown" if pd.isna(name) else str(name)
    if not pd.isna(identity):
        label += f" ({str(identity)[:8]})"
    return label


def _text(value: Any) -> str:
    return "unknown" if pd.isna(value) else str(value)


def _filter_phrase(values: list[str] | None) -> str:
    return "all" if values is None else ", ".join(values)


def _opt(value: Any) -> str | None:
    return None if pd.isna(value) else str(value)
