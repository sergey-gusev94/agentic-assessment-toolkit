"""Export one assignment's final judgments as Brightspace PDFs and grades."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pandas as pd

from . import __version__, grading_schema, ingest
from .data_root import ensure_outside_toolkit
from .feedback_pdf import FeedbackRenderError, render_pdf
from .harbor import read_run_record, utc_stamp
from .hashing import sha256_bytes, sha256_dir, sha256_file
from .results import load_results
from .rubric import RubricError, parse_rubric_file

_BASE_ADJUSTMENT_FRACTION = 0.05


class ExportError(Exception):
    """An incomplete or ambiguous export, requiring corrected inputs."""


@dataclass(frozen=True)
class GradeTemplate:
    usernames: dict[str, str]
    column: str
    maximum: float


@dataclass(frozen=True)
class FinalGrade:
    points: float
    percentage: float
    rubric_base_adjustment_points: float


def _final_grade(sums: dict[str, float], maximum: float) -> FinalGrade:
    adjusted_base = min(
        sums["base_points"] + _BASE_ADJUSTMENT_FRACTION * sums["base_max"],
        sums["base_max"],
    )
    fraction = (adjusted_base + sums["bonus_points"]) / sums["base_max"]
    return FinalGrade(
        points=fraction * maximum,
        percentage=fraction * 100,
        rubric_base_adjustment_points=adjusted_base - sums["base_points"],
    )


def _username(value: str) -> str:
    return value.strip().lstrip("#").casefold()


def read_grade_template(path: Path) -> GradeTemplate:
    """Read the actual roster export, including its grade-item point scale."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        headers = next(reader, [])
        if len(headers) != len(set(headers)):
            raise ExportError("grade export contains duplicate headers")
        matches = [
            re.fullmatch(r"(.+ Points Grade) <Numeric MaxPoints:([^<>]+)>", h)
            for h in headers
            if " Points Grade" in h
        ]
        if (
            len(matches) != 1
            or matches[0] is None
            or "Username" not in headers
            or "End-of-Line Indicator" not in headers
        ):
            raise ExportError(
                "export the actual Brightspace roster with Username, exactly one "
                "numeric Points Grade column including MaxPoints, and End-of-Line Indicator"
            )
        match = matches[0]
        try:
            maximum = float(match[2])
        except ValueError as error:
            raise ExportError("invalid gradebook maximum") from error
        if not math.isfinite(maximum) or maximum <= 0:
            raise ExportError("gradebook maximum must be finite and positive")
        usernames = {}
        for row in reader:
            if len(row) != len(headers) or row[headers.index("End-of-Line Indicator")] != "#":
                raise ExportError(f"malformed grade-export row {reader.line_num}")
            original = row[headers.index("Username")]
            key = _username(original)
            if not key or key in usernames:
                raise ExportError(f"blank or duplicate username at row {reader.line_num}")
            usernames[key] = original
        if not usernames:
            raise ExportError("grade export contains no students")
        return GradeTemplate(usernames, match[1], maximum)


def _select_trials(
    frame: pd.DataFrame,
    course_id: str,
    assignment_id: str,
    config_name: str,
    context_name: str,
    gradings: int,
    config_identity: str | None,
    context_identity: str | None,
    trials: list[str],
) -> pd.DataFrame:
    frame = frame.loc[
        (frame["stage"] == "grade")
        & (frame["submission_source"] == "student")
        & (frame["course_id"] == course_id)
        & (frame["assignment_id"] == assignment_id)
        & (frame["config_name"] == config_name)
        & (frame["context_config_name"] == context_name)
        & frame["context_config_identity"].notna()
        & frame["config_identity"].notna()
    ].copy()
    selectors = [
        ("config_identity", config_identity, "--config-identity"),
        ("context_config_identity", context_identity, "--context-config-identity"),
    ]
    for column, selected, _ in selectors:
        if selected is not None:
            frame = frame.loc[frame[column] == selected].copy()
    for column, _, flag in selectors:
        identities = frame[column].dropna().unique().tolist()
        if len(identities) > 1:
            raise ExportError(
                f"multiple stored {column} values; select {flag}: " + ", ".join(identities)
            )
    frame = frame.loc[
        (frame["n_prior_gradings"] == gradings)
        & ~frame["superseded"]
        & (frame["outcome"] == "completed")
        & ~frame["grading_load_error"]
    ].copy()
    frame["reference"] = frame["job_name"] + "/" + frame["trial_name"]
    requested = set(trials)
    unknown = requested - set(frame["reference"])
    if unknown:
        raise ExportError(
            "--trial is not an eligible current judgment: " + ", ".join(sorted(unknown))
        )
    # An explicit trial selects that student's judgment only; other students
    # continue to use their single eligible judgment across all matching jobs.
    chosen_students = frame.loc[frame["reference"].isin(requested), "student_id"]
    return frame.loc[
        ~frame["student_id"].isin(chosen_students) | frame["reference"].isin(requested)
    ]


def _safe_component(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ExportError(f"invalid path component {value!r}")
    return value


def _load_judgment(
    root: Path, row: dict[str, Any], submission_hash: str
) -> tuple[dict[str, Any], str, dict[str, str]]:
    job = _safe_component(str(row["job_name"]))
    trial = _safe_component(str(row["trial_name"]))
    record = read_run_record(root / "grading" / job)
    if record is None:
        raise ExportError("missing run record")
    items = record.get("items")
    matching = (
        [
            item
            for item in items
            if isinstance(item, dict)
            and item.get("item_id") == row["item_id"]
            and item.get("item_identity") == row["item_identity"]
        ]
        if isinstance(items, list)
        else []
    )
    if len(matching) != 1:
        raise ExportError("judgment does not resolve to exactly one run-record item")
    item = matching[0]
    hashes = item.get("input_hashes", {})
    if not isinstance(hashes, dict) or hashes.get("submission") != submission_hash:
        raise ExportError("submission ZIP does not match the work graded by this judgment")
    task = root / "tasks" / job / _safe_component(str(item["task_dir_name"]))
    rubric = task / "environment" / "rubric.md"
    if not rubric.is_file() or sha256_file(rubric) != hashes.get("rubric"):
        raise ExportError("missing or changed materialized rubric")
    expected = [
        {"id": c.id, "max_points": c.max_points, "bonus": c.bonus}
        for c in parse_rubric_file(rubric)
    ]
    output = root / "grading" / job / trial / "artifacts" / "app" / "grading_output"
    data, errors = grading_schema.load_grading_result(output / grading_schema.RESULT_FILENAME)
    for name in [grading_schema.JUSTIFICATION_FILENAME, grading_schema.FEEDBACK_FILENAME]:
        errors.extend(grading_schema.text_file_errors(output / name))
    if data is not None and not errors:
        errors.extend(grading_schema.expected_criteria_errors(expected, data))
    if errors or data is None:
        raise ExportError("; ".join(errors))
    # Preserve rubric order even if the stored result used a different order.
    criteria = cast(list[dict[str, Any]], data["criteria"])
    by_id = {entry["id"]: entry for entry in criteria}
    data["criteria"] = [by_id[entry["id"]] for entry in expected]
    feedback = (output / grading_schema.FEEDBACK_FILENAME).read_text(encoding="utf-8")
    sources = {
        name: sha256_file(output / name)
        for name in (
            grading_schema.RESULT_FILENAME,
            grading_schema.JUSTIFICATION_FILENAME,
            grading_schema.FEEDBACK_FILENAME,
        )
    }
    sources["submission"] = submission_hash
    sources["rubric"] = sha256_file(rubric)
    sources["run_record"] = sha256_file(root / "grading" / job / "aat-run.json")
    return data, feedback, sources


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _text(value: str) -> str:
    """Escape author-independent labels before composing Markdown."""
    return re.sub(r"([\\`*_{}\[\]#+.!|<>$~-])", r"\\\1", " ".join(value.split()))


def feedback_document(
    course_id: str,
    assignment: str,
    student: ingest.Student,
    data: dict[str, Any],
    feedback: str,
    maximum: float,
    grade: FinalGrade,
) -> str:
    sums = grading_schema.computed_sums(data)
    lines = [
        f"# {_text(assignment)}: grade and feedback",
        "",
        f"**Course:** {_text(course_id)}",
        "",
        f"**Student:** {_text(student.display_name)} ({_text(student.lms_username)})",
        "",
        f"**Final academic grade: {_number(grade.points)} / {_number(maximum)} "
        f"({_number(grade.percentage)}%)**",
        "",
        f"Rubric base points: {_number(sums['base_points'])} / {_number(sums['base_max'])}. "
        f"Bonus points: {_number(sums['bonus_points'])} / {_number(sums['bonus_max'])}.",
        "",
        f"A {_number(_BASE_ADJUSTMENT_FRACTION * 100)} percentage point adjustment adds "
        f"{_number(grade.rubric_base_adjustment_points)} rubric base points, capped at "
        f"the base maximum of {_number(sums['base_max'])}. "
        "Earned bonus points are added after the cap.",
        "",
    ]
    if not math.isclose(sums["base_max"], maximum):
        lines.extend(
            [
                f"The rubric score is scaled to the gradebook maximum of {_number(maximum)} points.",
                "",
            ]
        )
    lines.extend(
        ["## Criterion scores", "", "| Criterion | Points | Maximum |", "| :--- | ---: | ---: |"]
    )
    for entry in data["criteria"]:
        title = f"{entry['id']}: {entry['title']}" + (" (bonus)" if entry.get("bonus") else "")
        lines.append(
            f"| {_text(title)} | {_number(entry['points'])} | {_number(entry['max_points'])} |"
        )
    lines.extend(["", "## Feedback", "", feedback.strip(), ""])
    return "\n".join(lines)


def export_results(
    root: Path,
    *,
    course_id: str,
    assignment_id: str,
    config_name: str,
    context_name: str,
    gradings: int,
    grade_export: Path,
    submission_zips: list[Path],
    out_root: Path | None = None,
    config_identity: str | None = None,
    context_identity: str | None = None,
    trials: list[str] | None = None,
    allow_partial: bool = False,
    zero_missing: bool = False,
    can_exceed: bool = False,
) -> Path:
    """Create a fresh export snapshot; never launch grading or publish results.

    zero_missing is the operator's confirmation that the downloads cover all
    intended file submissions and absent students should receive zero.
    """
    _safe_component(course_id)
    _safe_component(assignment_id)
    if gradings < 1:
        raise ExportError("--gradings must be positive")
    destination = (out_root or root / "analysis" / "exports").resolve()
    ensure_outside_toolkit(destination, what="export destination")
    for name in (
        "raw",
        "raw-submissions",
        "courses",
        "tables",
        "submissions",
        "grading",
        "solving",
        "tasks",
    ):
        protected = (root / name).resolve()
        if destination.is_relative_to(protected) or protected.is_relative_to(destination):
            raise ExportError(
                "export destination overlaps source data; use analysis/exports or a separate directory"
            )
    template = read_grade_template(grade_export)
    submissions = ingest.read_brightspace_submissions(submission_zips)
    by_username = {_username(s.username): s for s in submissions}
    if len(by_username) != len(submissions):
        raise ExportError("multiple Brightspace person IDs share a username")
    unexpected = set(by_username) - set(template.usernames)
    if unexpected:
        raise ExportError("ZIP submitters absent from roster: " + ", ".join(sorted(unexpected)))
    students = ingest.read_students(root, course_id)
    if not students:
        raise ExportError("missing student identity table; run ingest-submissions before export")
    for known_student in students:
        _safe_component(known_student.student_id)
    mapped = {_username(s.lms_username): s for s in students if s.lms_username}
    if (
        len(mapped) != sum(bool(s.lms_username) for s in students)
        or len({s.student_id for s in students}) != len(students)
        or any(s.course_id != course_id for s in students)
    ):
        raise ExportError("conflicting student identity table")
    all_trials = load_results(root).trials
    known_student_ids = set(
        all_trials.loc[
            (all_trials["course_id"] == course_id)
            & (all_trials["assignment_id"] == assignment_id)
            & (all_trials["submission_source"] == "student"),
            "student_id",
        ].dropna()
    )
    frame = _select_trials(
        all_trials,
        course_id,
        assignment_id,
        config_name,
        context_name,
        gradings,
        config_identity,
        context_identity,
        trials or [],
    )
    entries: list[dict[str, Any]] = []
    documents: list[tuple[str, str]] = []
    for username, original in template.usernames.items():
        entry: dict[str, Any] = {"username": original}
        entries.append(entry)
        student = mapped.get(username)
        submission = by_username.get(username)
        if submission is None:
            # An incomplete ZIP cannot erase work already known to the toolkit.
            known = student is not None and (
                (root / "submissions" / course_id / student.student_id / assignment_id).exists()
                or student.student_id in known_student_ids
            )
            if zero_missing and not known:
                entry.update(
                    status="zero_missing", grade="0", reason="confirmed no file submission"
                )
            else:
                entry.update(
                    status="unresolved",
                    reason=(
                        "ZIP omits a known submission"
                        if known
                        else "no file submission in ZIP; confirm complete downloads with --zero-missing"
                    ),
                )
            continue
        try:
            if student is None or student.lms_person_id != submission.person_id:
                raise ExportError("missing or mismatched identity mapping; run ingest-submissions")
            _safe_component(student.student_id)
            normalized = root / "submissions" / course_id / student.student_id / assignment_id
            if not normalized.is_dir() or sha256_dir(normalized) != submission.sha256:
                raise ExportError(
                    "ZIP content differs from the normalized submission; check ingest inputs"
                )
            selected = frame.loc[frame["student_id"] == student.student_id]
            if len(selected) != 1:
                refs = ", ".join(selected["reference"])
                raise ExportError(
                    "no completed current final judgment"
                    if selected.empty
                    else f"multiple final judgments; select --trial JOB/TRIAL: {refs}"
                )
            row = {str(key): value for key, value in selected.iloc[0].to_dict().items()}
            data, feedback, hashes = _load_judgment(root, row, submission.sha256)
            sums = grading_schema.computed_sums(data)
            grade = _final_grade(sums, template.maximum)
            if not math.isfinite(grade.points):
                raise ExportError("grade is not finite")
            if grade.points > template.maximum and not can_exceed:
                raise ExportError(
                    "grade exceeds the gradebook maximum; enable Can Exceed in Brightspace "
                    "and confirm with --can-exceed"
                )
            archive_name = submission.folder + "/feedback.pdf"
            document = feedback_document(
                course_id,
                template.column.removesuffix(" Points Grade"),
                student,
                data,
                feedback,
                template.maximum,
                grade,
            )
            documents.append((archive_name, document))
            entry.update(
                status="graded",
                grade=_number(grade.points),
                student_id=student.student_id,
                trial=row["reference"],
                item_identity=row["item_identity"],
                config_identity=row["config_identity"],
                context_config_identity=row["context_config_identity"],
                prior_trials=row["prior_trials"],
                feedback_file=archive_name,
                hashes=hashes,
                rubric_base_max=sums["base_max"],
                rubric_base_adjustment_points=grade.rubric_base_adjustment_points,
                sums_consistent=grading_schema.sums_report(data)["consistent"],
            )
        except (ExportError, OSError, ValueError, RubricError) as error:
            entry.update(status="unresolved", reason=str(error))
    unresolved = [entry for entry in entries if entry["status"] == "unresolved"]
    if unresolved and not allow_partial:
        raise ExportError(
            "export incomplete; no upload files written:\n"
            + "\n".join(f"  {entry['username']}: {entry['reason']}" for entry in unresolved)
        )
    included = [entry for entry in entries if entry["status"] != "unresolved"]
    if not included:
        raise ExportError("no grades are ready to export")
    manifest = {
        "schema_version": 1,
        "toolkit_version": __version__,
        "course_id": course_id,
        "assignment_id": assignment_id,
        "config_name": config_name,
        "context_config_name": context_name,
        "gradings": gradings,
        "grade_column": template.column,
        "gradebook_maximum": template.maximum,
        "base_adjustment_pct": _BASE_ADJUSTMENT_FRACTION * 100,
        "allow_partial": allow_partial,
        "zero_missing": zero_missing,
        "can_exceed": can_exceed,
        "inputs": [
            {"path": str(p.resolve()), "sha256": sha256_file(p)}
            for p in [
                grade_export,
                *submission_zips,
                root / "tables" / course_id / ingest.STUDENTS_CSV,
            ]
        ],
        "students": entries,
    }
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aat-export-", dir=destination) as temporary:
        staging = Path(temporary) / "result"
        staging.mkdir()
        try:
            with zipfile.ZipFile(staging / "feedback.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                for name, document in documents:
                    pdf = render_pdf(document)
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, pdf)
        except FeedbackRenderError as error:
            raise ExportError(f"no upload files written: {error}") from error
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(["Username", template.column, "End-of-Line Indicator"])
        writer.writerows([entry["username"], entry["grade"], "#"] for entry in included)
        (staging / "grades.csv").write_text(buffer.getvalue(), encoding="utf-8", newline="")
        manifest["outputs"] = {
            name: sha256_bytes((staging / name).read_bytes())
            for name in ("feedback.zip", "grades.csv")
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        final = destination / f"{utc_stamp()}__{course_id}__{assignment_id}__{uuid4().hex[:8]}"
        shutil.move(str(staging), final)
    return final
