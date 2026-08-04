"""Statistics over the tidy tables: benchmark ladder, judge quality, students.

Implements the statistics contract of docs/design.md ("Results,
statistics, and reporting"): pure, deterministic functions over the
trials and criteria tables returned by ``results.load_results``, using
numpy and pandas only. Each function returns one table whose column
names are the CSV contract of the report, rows sorted by the key
columns.

Definitions used throughout (docs/design.md, denominator policy):

- A **valid grading** is a trials row with stage ``grade``, outcome
  ``completed``, and a non-NA ``base_pct``. Score statistics cover
  valid gradings only, so a failed measurement never enters a score
  mean. Load-error rows carry scores from the reward file and count as
  valid gradings for score statistics; they are excluded only from
  criterion-level and sums statistics and surfaced via their flag rate.
- Trials pool by (``item_id``, ``item_identity``) across jobs — the
  doneness key — so pooling never merges trials whose rubric or
  environment differed.
- Pseudo-students (``student_id`` starting with ``_``) are the grader
  checks' known submissions: excluded from student and benchmark
  aggregates, reported only by ``grader_checks`` — except in
  ``judge_quality``, which includes them because judge quality is about
  the judge, not the students.

Group keys can be NA (for example an unknown solver config), so every
groupby uses ``dropna=False``. A ratio with a zero denominator is NA,
never infinity or a zero default.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data_root import find_rubric, rubric_versions
from .hashing import sha256_file

# One source for the outcome taxonomy: results.py owns the tuple, and
# metrics re-exports it as a module constant.
from .results import OUTCOME_CATEGORIES as OUTCOME_CATEGORIES
from .rubric import RubricError, parse_rubric_file

DEFAULT_SEED = 42
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_CONFIDENCE = 0.95
# Below this many assignments no interval is emitted (the report prints
# the too-few-clusters note instead).
MIN_BOOTSTRAP_CLUSTERS = 5

# One absolute tolerance for comparing authored points: per-criterion
# exact agreement and rubric-fidelity max-points matching.
_POINTS_TOLERANCE = 1e-9

# The key columns of each table. Benchmark-ladder tables are keyed by
# the (solver config, grading config) pair; judge quality by the
# grading config alone.
_CONFIG_KEYS = ["config_name", "config_identity"]
_SOLVE_SUMMARY_KEYS = [*_CONFIG_KEYS, "course_id", "assignment_id"]
_LADDER_CONFIG_KEYS = [
    "solver_config_name",
    "solver_config_identity",
    "grading_config_name",
    "grading_config_identity",
]
# Every grading table keys on the rubric version as well as the config:
# a rubric name may advance to new bytes, so two generations of the same
# assignment can share a config identity, and averaging across them
# would compare grades to a different point split (docs/design.md,
# "Results, statistics, and reporting").
_ASSIGNMENT_KEYS = [*_LADDER_CONFIG_KEYS, "course_id", "assignment_id", "rubric_sha256"]
_COURSE_KEYS = [*_LADDER_CONFIG_KEYS, "course_id"]
_STUDENT_KEYS = [*_CONFIG_KEYS, "course_id", "assignment_id", "student_id", "rubric_sha256"]
_CLASS_KEYS = [*_CONFIG_KEYS, "course_id", "assignment_id", "rubric_sha256"]
_FAILURE_GROUP_KEYS = ["stage", *_CONFIG_KEYS]
_FAILURE_KEYS = [*_FAILURE_GROUP_KEYS, "outcome"]
_POOLING_KEY = ["item_id", "item_identity"]

# Column order and dtypes of each table. Counts are always computed, so
# they stay plain int64; means, deviations, and rates use Float64 so a
# value with no data is NA, never a zero-like default.
_SOLVE_SUMMARY_DTYPES: dict[str, str] = {
    **dict.fromkeys(_SOLVE_SUMMARY_KEYS, "string"),
    "n_trials": "int64",
    **{f"n_{category}": "int64" for category in OUTCOME_CATEGORIES},
    "n_verified": "int64",
    "contract_pass_rate": "Float64",
    "n_late_exception": "int64",
}

_ASSIGNMENT_DTYPES: dict[str, str] = {
    **dict.fromkeys(_ASSIGNMENT_KEYS, "string"),
    "n_solve_trials": "int64",
    "n_gradings": "int64",
    "mean_base_pct": "Float64",
    "sd_base_pct": "Float64",
    "mean_score_pct": "Float64",
}

_COURSE_DTYPES: dict[str, str] = {
    **dict.fromkeys(_COURSE_KEYS, "string"),
    "n_assignments": "int64",
    "n_assignments_mixed_rubric": "int64",
    "n_assignments_total": "int64",
    "macro_mean_base_pct": "Float64",
    "macro_mean_score_pct": "Float64",
    "ci_low": "Float64",
    "ci_high": "Float64",
}

_JUDGE_DTYPES: dict[str, str] = {
    **dict.fromkeys(_CONFIG_KEYS, "string"),
    "n_trials": "int64",
    "n_valid_gradings": "int64",
    "n_items_repeated": "int64",
    "mean_within_item_sd": "Float64",
    "max_within_item_range": "Float64",
    "criterion_exact_agreement_rate": "Float64",
    "criterion_mean_abs_diff": "Float64",
    "rubric_fidelity_rate": "Float64",
    "n_rubric_unresolved": "int64",
    "sums_consistent_rate": "Float64",
    "late_exception_rate": "Float64",
    "grading_load_error_rate": "Float64",
    **{f"rate_{category}": "Float64" for category in OUTCOME_CATEGORIES},
}

_STUDENT_DTYPES: dict[str, str] = {
    **dict.fromkeys(_STUDENT_KEYS, "string"),
    "n_valid_gradings": "int64",
    "mean_score_pct": "Float64",
    "sd_score_pct": "Float64",
    "mean_base_pct": "Float64",
    "sd_base_pct": "Float64",
    "n_failed_gradings": "int64",
    "n_sums_inconsistent": "int64",
    "n_late_exception": "int64",
    "n_load_error": "int64",
}

_CLASS_DTYPES: dict[str, str] = {
    **dict.fromkeys(_CLASS_KEYS, "string"),
    "n_students": "int64",
    "mean": "Float64",
    "median": "Float64",
    "sd": "Float64",
    "q25": "Float64",
    "q75": "Float64",
}

_CHECK_DTYPES: dict[str, str] = {
    **dict.fromkeys(_STUDENT_KEYS, "string"),
    "role": "string",
    "n_valid_gradings": "int64",
    "mean_base_pct": "Float64",
    "min_base_pct": "Float64",
    "max_base_pct": "Float64",
    "mean_score_pct": "Float64",
}

_FAILURE_DTYPES: dict[str, str] = {
    **dict.fromkeys(_FAILURE_KEYS, "string"),
    "n_trials": "int64",
    "share": "Float64",
}

_UNGRADED_KEYS = [*_CONFIG_KEYS, "course_id", "assignment_id", "job_name", "trial_name"]

_UNGRADED_DTYPES: dict[str, str] = {
    **dict.fromkeys(_UNGRADED_KEYS, "string"),
    "outcome": "string",
}


def solve_summary(trials: pd.DataFrame) -> pd.DataFrame:
    """Solve outcome counts per config, course, and assignment.

    ``contract_pass_rate`` follows the denominator policy: completed
    over verified solve trials (completed + contract_failed), NA when
    none are verified.
    """
    solve = trials[_stage_mask(trials, "solve")]
    rows: list[dict[str, object]] = []
    for key, group in solve.groupby(_SOLVE_SUMMARY_KEYS, dropna=False, sort=True):
        counts = {
            category: int((group["outcome"] == category).sum()) for category in OUTCOME_CATEGORIES
        }
        n_verified = counts["completed"] + counts["contract_failed"]
        row: dict[str, object] = dict(zip(_SOLVE_SUMMARY_KEYS, key, strict=True))
        row["n_trials"] = len(group)
        row.update({f"n_{category}": count for category, count in counts.items()})
        row["n_verified"] = n_verified
        row["contract_pass_rate"] = _ratio(counts["completed"], n_verified)
        row["n_late_exception"] = int(group["late_exception"].sum())
        rows.append(row)
    return _table(rows, _SOLVE_SUMMARY_DTYPES, _SOLVE_SUMMARY_KEYS)


def grades_by_assignment(trials: pd.DataFrame) -> pd.DataFrame:
    """Benchmark ladder steps 1-2: per-assignment means, solve-derived only.

    Grading repeats of one solve trial — one pooling key — average to a
    per-solve-trial score; solve trials average to the per-assignment
    score, so repeat counts never weigh the mean. ``sd_base_pct`` is the
    spread across the per-solve-trial means (ddof=1, NA below two).
    """
    valid = trials[_valid_grading_mask(trials)]
    derived = valid[(valid["submission_source"] == "solve-trial").fillna(False)]
    work = derived.rename(
        columns={"config_name": "grading_config_name", "config_identity": "grading_config_identity"}
    )
    rows: list[dict[str, object]] = []
    for key, group in work.groupby(_ASSIGNMENT_KEYS, dropna=False, sort=True):
        per_trial = group.groupby(_POOLING_KEY, dropna=False, sort=True)
        base_means = per_trial["base_pct"].mean()
        score_means = per_trial["score_pct"].mean()
        row: dict[str, object] = dict(zip(_ASSIGNMENT_KEYS, key, strict=True))
        row.update(
            {
                "n_solve_trials": len(base_means),
                "n_gradings": len(group),
                "mean_base_pct": _mean(base_means),
                "sd_base_pct": _sd(base_means),
                "mean_score_pct": _mean(score_means),
            }
        )
        rows.append(row)
    return _table(rows, _ASSIGNMENT_DTYPES, _ASSIGNMENT_KEYS)


def grades_by_course(trials: pd.DataFrame, *, seed: int = DEFAULT_SEED) -> pd.DataFrame:
    """Ladder step 3: assignments macro-average to the course score.

    Each assignment weighs equally. ``ci_low``/``ci_high`` are a
    percentile bootstrap of the macro mean of the per-assignment
    ``mean_base_pct`` values, NA below ``MIN_BOOTSTRAP_CLUSTERS``
    assignments. One rng is seeded once and the groups are processed in
    sorted key order, so the output is deterministic for a given seed.

    An assignment graded against more than one rubric version under this
    config has no single per-assignment score, so it contributes none:
    it is left out of the macro-mean and counted in
    ``n_assignments_mixed_rubric``. Averaging the versions would compare
    scores measured against different point splits; dropping them
    silently would hide it. Its per-version means stay in
    ``grades_by_assignment``, one row each.
    """
    per_assignment = grades_by_assignment(trials)
    solve = trials[_stage_mask(trials, "solve")]
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for key, group in per_assignment.groupby(_COURSE_KEYS, dropna=False, sort=True):
        row: dict[str, object] = dict(zip(_COURSE_KEYS, key, strict=True))
        versions = group.groupby("assignment_id", dropna=False)["rubric_sha256"].transform("size")
        # dropna=False throughout: an NA assignment id is still one
        # assignment, and dropping it here would remove it from the
        # macro-mean while counting it nowhere.
        mixed_ids = group.loc[versions > 1, "assignment_id"]
        n_mixed = int(mixed_ids.nunique(dropna=False))
        group = group[versions == 1]
        n_assignments = len(group)
        # Coverage denominator: distinct assignments with at least one
        # solve trial under this solver config identity — never below
        # the graded count, since a solve-derived grading proves its
        # solve trial existed even when that trial's own result is
        # missing or unreadable. Without any solve rows the graded
        # count is all that is known.
        matching = solve[
            (solve["config_identity"] == row["solver_config_identity"]).fillna(False)
            & (solve["course_id"] == row["course_id"]).fillna(False)
        ]
        n_total = max(int(matching["assignment_id"].nunique()), n_assignments + n_mixed)
        ci_low: float | None = None
        ci_high: float | None = None
        if n_assignments >= MIN_BOOTSTRAP_CLUSTERS:
            values = group["mean_base_pct"].to_numpy(dtype=float)
            ci_low, ci_high = _bootstrap_interval(rng, values)
        row.update(
            {
                "n_assignments": n_assignments,
                "n_assignments_mixed_rubric": n_mixed,
                "n_assignments_total": n_total,
                "macro_mean_base_pct": _mean(group["mean_base_pct"]),
                "macro_mean_score_pct": _mean(group["mean_score_pct"]),
                "ci_low": ci_low,
                "ci_high": ci_high,
            }
        )
        rows.append(row)
    return _table(rows, _COURSE_DTYPES, _COURSE_KEYS)


def judge_quality(trials: pd.DataFrame, criteria: pd.DataFrame, *, data_root: Path) -> pd.DataFrame:
    """Judge-quality statistics per grading config, pseudo-students included.

    Repeat stability pools valid gradings by item; per-criterion
    agreement compares criterion ids shared by both trials of each
    unordered pair of a repeated item's gradings. Rubric fidelity
    resolves each row's rubric from the course tree in ``data_root`` by
    its recorded ``rubric_sha256`` — across the selectable rubrics and
    the archived versions, so a trial graded before ``default`` advanced
    still resolves — and compares the criterion id set, per-id max
    points, and per-id bonus flags; titles and the points awarded never
    enter fidelity. A rubric version that is on disk nowhere, or that
    does not parse, makes the trial unresolvable: excluded from the
    rate, counted in ``n_rubric_unresolved``.
    """
    points_by_trial, shape_by_trial = _criteria_maps(criteria)
    rubric_cache: dict[Path, tuple[str | None, dict[str, tuple[float, bool]] | None]] = {}
    versions_cache: dict[tuple[str, str], dict[str, Path]] = {}
    grading = trials[_stage_mask(trials, "grade")]
    rows: list[dict[str, object]] = []
    for key, group in grading.groupby(_CONFIG_KEYS, dropna=False, sort=True):
        valid = group[_valid_grading_mask(group)]
        n_trials = len(group)
        n_valid = len(valid)

        within_sds: list[float] = []
        within_ranges: list[float] = []
        agreements: list[bool] = []
        abs_diffs: list[float] = []
        for _, item_group in valid.groupby(_POOLING_KEY, dropna=False, sort=True):
            if len(item_group) < 2:
                continue
            base = item_group["base_pct"]
            within_sds.append(float(base.std(ddof=1)))
            within_ranges.append(float(base.max() - base.min()))
            trial_keys = sorted(zip(item_group["job_name"], item_group["trial_name"], strict=True))
            for first, second in itertools.combinations(trial_keys, 2):
                shared = set(points_by_trial.get(first, {})) & set(points_by_trial.get(second, {}))
                for criterion_id in sorted(shared):
                    diff = abs(
                        points_by_trial[first][criterion_id] - points_by_trial[second][criterion_id]
                    )
                    agreements.append(diff <= _POINTS_TOLERANCE)
                    abs_diffs.append(diff)

        # Fidelity covers criterion-bearing valid gradings: load-error
        # rows have no criteria rows and are surfaced by their own rate.
        n_match = 0
        n_resolvable = 0
        n_unresolved = 0
        for job_name, trial_name, course_id, assignment_id, rubric_name, rubric_sha256 in zip(
            valid["job_name"],
            valid["trial_name"],
            valid["course_id"],
            valid["assignment_id"],
            valid["rubric_name"],
            valid["rubric_sha256"],
            strict=True,
        ):
            trial_shape = shape_by_trial.get((job_name, trial_name))
            if trial_shape is None:
                continue
            rubric_shape = _resolved_rubric_shape(
                rubric_cache,
                versions_cache,
                data_root,
                course_id,
                assignment_id,
                rubric_name,
                rubric_sha256,
            )
            if rubric_shape is None:
                n_unresolved += 1
                continue
            n_resolvable += 1
            if _shapes_match(trial_shape, rubric_shape):
                n_match += 1

        n_sums_checkable = int(valid["sums_consistent"].notna().sum())
        n_sums_consistent = int(valid["sums_consistent"].fillna(False).sum())
        row: dict[str, object] = dict(zip(_CONFIG_KEYS, key, strict=True))
        row.update(
            {
                "n_trials": n_trials,
                "n_valid_gradings": n_valid,
                "n_items_repeated": len(within_sds),
                "mean_within_item_sd": _mean_of(within_sds),
                "max_within_item_range": max(within_ranges) if within_ranges else None,
                "criterion_exact_agreement_rate": _ratio(sum(agreements), len(agreements)),
                "criterion_mean_abs_diff": _mean_of(abs_diffs),
                "rubric_fidelity_rate": _ratio(n_match, n_resolvable),
                "n_rubric_unresolved": n_unresolved,
                "sums_consistent_rate": _ratio(n_sums_consistent, n_sums_checkable),
                "late_exception_rate": _ratio(int(group["late_exception"].sum()), n_trials),
                "grading_load_error_rate": _ratio(int(valid["grading_load_error"].sum()), n_valid),
            }
        )
        for category in OUTCOME_CATEGORIES:
            row[f"rate_{category}"] = _ratio(int((group["outcome"] == category).sum()), n_trials)
        rows.append(row)
    return _table(rows, _JUDGE_DTYPES, _CONFIG_KEYS)


def student_grades(trials: pd.DataFrame) -> pd.DataFrame:
    """Grading-assistant table: real students only, per student and assignment.

    ``sd_score_pct`` is the repeat SD — the per-student uncertainty
    statement (ddof=1, NA below two valid gradings). Score statistics
    cover valid gradings only; the failure and flag counts cover all of
    the student's grading trials.
    """
    students = trials[_student_mask(trials) & ~_pseudo_mask(trials)]
    rows: list[dict[str, object]] = []
    for key, group in students.groupby(_STUDENT_KEYS, dropna=False, sort=True):
        valid = group[_valid_grading_mask(group)]
        row: dict[str, object] = dict(zip(_STUDENT_KEYS, key, strict=True))
        row.update(
            {
                "n_valid_gradings": len(valid),
                "mean_score_pct": _mean(valid["score_pct"]),
                "sd_score_pct": _sd(valid["score_pct"]),
                "mean_base_pct": _mean(valid["base_pct"]),
                "sd_base_pct": _sd(valid["base_pct"]),
                "n_failed_gradings": len(group) - len(valid),
                "n_sums_inconsistent": int(group["sums_consistent"].eq(False).fillna(False).sum()),
                "n_late_exception": int(group["late_exception"].sum()),
                "n_load_error": int(group["grading_load_error"].sum()),
            }
        )
        rows.append(row)
    return _table(rows, _STUDENT_DTYPES, _STUDENT_KEYS)


def class_distribution(student_grades_df: pd.DataFrame) -> pd.DataFrame:
    """Class distribution per config, course, and assignment over per-student means.

    Operates on the ``student_grades`` table: one ``mean_score_pct``
    value per student. A student with no valid grading has no mean and
    does not enter. Report-only — rendered in report.md, never part of
    the benchmark CSVs.
    """
    rows: list[dict[str, object]] = []
    for key, group in student_grades_df.groupby(_CLASS_KEYS, dropna=False, sort=True):
        means = group["mean_score_pct"].dropna()
        row: dict[str, object] = dict(zip(_CLASS_KEYS, key, strict=True))
        row.update(
            {
                "n_students": len(means),
                "mean": _mean(means),
                "median": float(means.median()) if len(means) else None,
                "sd": _sd(means),
                "q25": float(means.quantile(0.25)) if len(means) else None,
                "q75": float(means.quantile(0.75)) if len(means) else None,
            }
        )
        rows.append(row)
    return _table(rows, _CLASS_DTYPES, _CLASS_KEYS)


def grader_checks(trials: pd.DataFrame) -> pd.DataFrame:
    """Grader-check summary: pseudo-students only, raw numbers.

    The advisory thresholds (reference at or above 95, irrelevant at or
    below 5, on ``base_pct``) are stated in the report text, never
    applied as a machine pass or fail here.
    """
    pseudo = trials[_student_mask(trials) & _pseudo_mask(trials)]
    rows: list[dict[str, object]] = []
    for key, group in pseudo.groupby(_STUDENT_KEYS, dropna=False, sort=True):
        valid = group[_valid_grading_mask(group)]
        base = valid["base_pct"]
        row: dict[str, object] = dict(zip(_STUDENT_KEYS, key, strict=True))
        row.update(
            {
                "role": _pseudo_role(str(row["student_id"])),
                "n_valid_gradings": len(valid),
                "mean_base_pct": _mean(base),
                "min_base_pct": float(base.min()) if len(valid) else None,
                "max_base_pct": float(base.max()) if len(valid) else None,
                "mean_score_pct": _mean(valid["score_pct"]),
            }
        )
        rows.append(row)
    return _table(rows, _CHECK_DTYPES, _STUDENT_KEYS)


def failure_accounting(trials: pd.DataFrame) -> pd.DataFrame:
    """Trial counts and shares per stage, config, and outcome category.

    Every category appears for every (stage, config) present — zero
    counts included — so failures are never silently dropped.
    """
    rows: list[dict[str, object]] = []
    for key, group in trials.groupby(_FAILURE_GROUP_KEYS, dropna=False, sort=True):
        for category in OUTCOME_CATEGORIES:
            row: dict[str, object] = dict(zip(_FAILURE_GROUP_KEYS, key, strict=True))
            count = int((group["outcome"] == category).sum())
            row.update({"outcome": category, "n_trials": count, "share": _ratio(count, len(group))})
            rows.append(row)
    return _table(rows, _FAILURE_DTYPES, _FAILURE_KEYS)


def ungraded_solve_trials(trials: pd.DataFrame) -> pd.DataFrame:
    """Solve trials that never became a valid grading, with their outcome.

    The direct answer to "what is missing from grading and why": a
    failed solve never produced a submission, a contract failure
    produced an empty one, and a verified solve may simply not have
    been graded yet. Elsewhere these trials appear only as outcome
    counts; here each one is a named row.
    """
    solve = trials[_stage_mask(trials, "solve")]
    valid = trials[_valid_grading_mask(trials)]
    graded = set(
        zip(valid["solve_job_name"], valid["solve_trial_name"], strict=True),
    )
    rows: list[dict[str, object]] = []
    for _, trial in solve.iterrows():
        if (trial["job_name"], trial["trial_name"]) in graded:
            continue
        rows.append(
            {
                **{key: trial[key] for key in _UNGRADED_KEYS},
                "outcome": trial["outcome"],
            }
        )
    return _table(rows, _UNGRADED_DTYPES, _UNGRADED_KEYS)


def _pseudo_role(student_id: str) -> str:
    """Grader-check role by id prefix (docs/design.md, grader checks)."""
    if student_id.startswith("_reference"):
        return "reference"
    if student_id.startswith("_irrelevant"):
        return "irrelevant"
    return "other"


def _bootstrap_interval(
    rng: np.random.Generator, values: np.ndarray[Any, np.dtype[np.float64]]
) -> tuple[float, float]:
    """Hand-rolled percentile bootstrap of the mean (numpy only).

    Resamples ``values`` with replacement ``BOOTSTRAP_RESAMPLES`` times
    and takes the central ``BOOTSTRAP_CONFIDENCE`` span of the
    resampled means.
    """
    indices = rng.integers(0, len(values), size=(BOOTSTRAP_RESAMPLES, len(values)))
    means = values[indices].mean(axis=1)
    tail = 100.0 * (1.0 - BOOTSTRAP_CONFIDENCE) / 2.0
    low, high = np.percentile(means, [tail, 100.0 - tail])
    return float(low), float(high)


def _criteria_maps(
    criteria: pd.DataFrame,
) -> tuple[
    dict[tuple[Any, Any], dict[str, float]],
    dict[tuple[Any, Any], dict[str, tuple[float, bool]]],
]:
    """Per-trial criterion maps: awarded points, and (max points, bonus)."""
    points_by_trial: dict[tuple[Any, Any], dict[str, float]] = {}
    shape_by_trial: dict[tuple[Any, Any], dict[str, tuple[float, bool]]] = {}
    for job_name, trial_name, criterion_id, points, max_points, bonus in zip(
        criteria["job_name"],
        criteria["trial_name"],
        criteria["criterion_id"],
        criteria["points"],
        criteria["max_points"],
        criteria["bonus"],
        strict=True,
    ):
        trial_key = (job_name, trial_name)
        points_by_trial.setdefault(trial_key, {})[criterion_id] = float(points)
        shape_by_trial.setdefault(trial_key, {})[criterion_id] = (float(max_points), bool(bonus))
    return points_by_trial, shape_by_trial


def _resolved_rubric_shape(
    cache: dict[Path, tuple[str | None, dict[str, tuple[float, bool]] | None]],
    versions_cache: dict[tuple[str, str], dict[str, Path]],
    data_root: Path,
    course_id: Any,
    assignment_id: Any,
    rubric_name: Any,
    rubric_sha256: Any,
) -> dict[str, tuple[float, bool]] | None:
    """The trial's rubric as an id -> (max points, bonus) map, or None.

    Resolution is by hash, not by name: the recorded ``rubric_sha256``
    is what the trial was graded against, while the recorded name is a
    label that may since have advanced to different bytes. The named
    file is tried first because it is the usual answer and costs one
    hash; otherwise every version of that assignment's rubric —
    selectable and archived — is searched for the recorded hash.

    A missing name is not fatal — the hash alone resolves — so None
    means the trial is unresolvable: the course, assignment, or hash is
    missing, no version with that hash is on disk, or the resolved
    version does not parse.
    """
    if any(pd.isna(field) for field in (course_id, assignment_id, rubric_sha256)):
        return None
    if not pd.isna(rubric_name):
        # The course-tree layout is owned by data_root; None means no
        # file of that name exists.
        path = find_rubric(data_root, str(course_id), str(assignment_id), str(rubric_name))
        if path is not None:
            if path not in cache:
                cache[path] = _read_rubric_shape(path)
            digest, shape = cache[path]
            if digest == rubric_sha256:
                return shape
    versions = _rubric_versions_cached(
        versions_cache, data_root, str(course_id), str(assignment_id)
    )
    archived = versions.get(str(rubric_sha256))
    if archived is None:
        return None
    if archived not in cache:
        cache[archived] = _read_rubric_shape(archived)
    return cache[archived][1]


def _rubric_versions_cached(
    cache: dict[tuple[str, str], dict[str, Path]],
    data_root: Path,
    course_id: str,
    assignment_id: str,
) -> dict[str, Path]:
    """Per-assignment version index, hashed once rather than per trial."""
    key = (course_id, assignment_id)
    if key not in cache:
        cache[key] = rubric_versions(data_root, course_id, assignment_id)
    return cache[key]


def _read_rubric_shape(path: Path) -> tuple[str | None, dict[str, tuple[float, bool]] | None]:
    try:
        digest = sha256_file(path)
    except OSError:
        return None, None
    try:
        parsed = parse_rubric_file(path)
    except RubricError:
        return digest, None
    return digest, {criterion.id: (criterion.max_points, criterion.bonus) for criterion in parsed}


def _shapes_match(
    trial_shape: dict[str, tuple[float, bool]], rubric_shape: dict[str, tuple[float, bool]]
) -> bool:
    if set(trial_shape) != set(rubric_shape):
        return False
    for criterion_id, (max_points, bonus) in trial_shape.items():
        rubric_max, rubric_bonus = rubric_shape[criterion_id]
        if abs(max_points - rubric_max) > _POINTS_TOLERANCE or bonus != rubric_bonus:
            return False
    return True


def _stage_mask(trials: pd.DataFrame, stage: str) -> pd.Series[bool]:
    return (trials["stage"] == stage).fillna(False)


def _valid_grading_mask(trials: pd.DataFrame) -> pd.Series[bool]:
    """Valid gradings: completed grading rows with a non-NA base_pct."""
    completed = (trials["outcome"] == "completed").fillna(False)
    return _stage_mask(trials, "grade") & completed & trials["base_pct"].notna()


def _student_mask(trials: pd.DataFrame) -> pd.Series[bool]:
    """Grading rows carrying a student id, pseudo-students included."""
    return _stage_mask(trials, "grade") & trials["student_id"].notna()


def _pseudo_mask(trials: pd.DataFrame) -> pd.Series[bool]:
    return trials["student_id"].str.startswith("_").fillna(False)


def _table(rows: list[dict[str, object]], dtypes: dict[str, str], keys: list[str]) -> pd.DataFrame:
    """A DataFrame with the full column set and dtypes, sorted by its keys."""
    frame = pd.DataFrame(rows, columns=list(dtypes)).astype(dtypes)
    return frame.sort_values(keys, kind="stable", ignore_index=True)


def _mean(values: pd.Series[Any]) -> float | None:
    present = values.dropna()
    return float(present.mean()) if len(present) else None


def _sd(values: pd.Series[Any]) -> float | None:
    """Sample standard deviation (ddof=1); None below two values."""
    present = values.dropna()
    return float(present.std(ddof=1)) if len(present) >= 2 else None


def _mean_of(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio(numerator: float, denominator: int) -> float | None:
    """NA on a zero denominator, never infinity or a zero default."""
    return numerator / denominator if denominator else None
