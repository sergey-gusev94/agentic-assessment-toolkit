from __future__ import annotations

import csv
import io
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from agentic_assessment_toolkit import cli, feedback_pdf, ingest
from agentic_assessment_toolkit import export_results as exports
from agentic_assessment_toolkit.data_root import DataRootError
from agentic_assessment_toolkit.hashing import sha256_dir, sha256_file
from tests.test_results import criterion, graded_rewards, grading_data, trial_result


@dataclass
class ExportData:
    root: Path
    roster: Path
    archive: Path
    folder: str

    def run(self, **options: Any) -> Path:
        arguments: dict[str, Any] = {
            "course_id": "SYN_C1",
            "assignment_id": "HW1",
            "config_name": "judge",
            "context_name": "grader",
            "gradings": 3,
            "grade_export": self.roster,
            "submission_zips": [self.archive],
        }
        arguments.update(options)
        return exports.export_results(self.root, **arguments)

    def judgment(
        self,
        job: str = "job1",
        *,
        student: str = "S001",
        count: int = 3,
        identity: str = "a" * 64,
        failed: bool = False,
        data: dict[str, object] | None = None,
    ) -> Path:
        rubric = self.root / "tasks" / job / student / "environment" / "rubric.md"
        rubric.parent.mkdir(parents=True, exist_ok=True)
        rubric.write_text("- `a` (10 points): Analysis\n- `b` (2 points, bonus): Extension\n")
        if data is None:
            data = grading_data(
                [criterion("a", 8, 10), criterion("b", 1, 2, bonus=True)], base_points=999
            )
        record = {
            "stage": "grade",
            "config": {"name": "judge", "judge": True, "rubric": "default"},
            "config_identity": identity,
            "items": [
                {
                    "item_id": f"SYN_C1/{student}/HW1",
                    "item_identity": f"{student}-{count}",
                    "task_dir_name": student,
                    "course_id": "SYN_C1",
                    "assignment_id": "HW1",
                    "student_id": student,
                    "submission_source": "student",
                    "input_hashes": {
                        "submission": sha256_dir(
                            self.root / "submissions" / "SYN_C1" / student / "HW1"
                        ),
                        "rubric": sha256_file(rubric),
                    },
                    "context_config_name": "grader",
                    "context_config_identity": "b" * 64,
                    "prior_trials": [
                        {"job_name": "initial", "trial_name": f"t{i}"} for i in range(count)
                    ],
                }
            ],
        }
        job_dir = self.root / "grading" / job
        job_dir.mkdir(parents=True)
        (job_dir / "aat-run.json").write_text(json.dumps(record))
        trial = job_dir / "trial1"
        trial.mkdir()
        result = trial_result(
            student,
            rewards=None if failed else graded_rewards(data),
            exception_type="AgentTimeoutError" if failed else None,
        )
        (trial / "result.json").write_text(json.dumps(result))
        output = trial / "artifacts" / "app" / "grading_output"
        output.mkdir(parents=True)
        if not failed:
            (output / "grading_result.json").write_text(json.dumps(data))
            (output / "justification.md").write_text("STAFF ONLY: earlier graders disagreed.")
            (output / "feedback.md").write_text(
                "You used the correct balance (answer.txt, line 1). "
                "Check the units of $x^2 + \\alpha$ before substituting values."
            )
        return output


@pytest.fixture
def export_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ExportData:
    root = tmp_path / "data"
    raw = root / "raw-submissions" / "SYN_C1"
    raw.mkdir(parents=True)
    roster = raw / "roster.csv"
    roster.write_text(
        "Username,Assignment 1 Points Grade <Numeric MaxPoints:100>,End-of-Line Indicator\n"
        "#alice,,#\n#bob,,#\n"
    )
    archive = raw / "Assignment 1.zip"
    folder = "123-456 - alice Alice Example - Sep 1, 2026 1200 PM"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(folder + "/answer.txt", "A balance equation.")
        z.writestr("index.html", "download index")
    normalized = root / "submissions" / "SYN_C1" / "S001" / "HW1"
    normalized.mkdir(parents=True)
    (normalized / "answer.txt").write_text("A balance equation.")
    table = root / "tables" / "SYN_C1" / "students.csv"
    table.parent.mkdir(parents=True)
    with table.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(ingest.STUDENTS_COLUMNS)
        writer.writerow(["S001", "SYN_C1", "123", "alice", "Alice Example", "brightspace"])
    # Selection tests inspect the exact document passed to the renderer.
    # A separate test exercises real PDF compilation and text extraction.
    monkeypatch.setattr(exports, "render_pdf", lambda text: b"%PDF-test\n" + text.encode())
    return ExportData(root, roster, archive, folder)


def read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="") as handle:
        return list(csv.reader(handle))


def test_export_uses_judge_scores_and_brightspace_folders(export_data: ExportData) -> None:
    export_data.judgment()
    first = export_data.run(zero_missing=True)
    second = export_data.run(zero_missing=True)
    assert first != second
    assert (first / "feedback.zip").read_bytes() == (second / "feedback.zip").read_bytes()
    assert read_csv(first / "grades.csv") == [
        ["Username", "Assignment 1 Points Grade", "End-of-Line Indicator"],
        ["#alice", "90", "#"],
        ["#bob", "0", "#"],
    ]
    with zipfile.ZipFile(first / "feedback.zip") as archive:
        assert archive.namelist() == [export_data.folder + "/feedback.pdf"]
        document = archive.read(archive.namelist()[0]).decode()
        assert "90 / 100 (90%)" in document
        assert "8 / 10" in document
        assert "gradebook maximum of 100" in document
        assert "STAFF ONLY" not in document
        assert "999" not in document
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["students"][0]["sums_consistent"] is False
    assert manifest["students"][1]["status"] == "zero_missing"
    assert set(manifest["outputs"]) == {"feedback.zip", "grades.csv"}
    assert {p.name for p in first.iterdir()} == {"feedback.zip", "grades.csv", "manifest.json"}


def test_absence_needs_confirmation_and_partial_omits_it(export_data: ExportData) -> None:
    export_data.judgment()
    with pytest.raises(exports.ExportError, match="--zero-missing"):
        export_data.run()
    assert not (export_data.root / "analysis").exists()
    path = export_data.run(allow_partial=True)
    assert len(read_csv(path / "grades.csv")) == 2
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["students"][1]["status"] == "unresolved"


def test_failed_judgment_never_becomes_zero(export_data: ExportData) -> None:
    export_data.judgment(failed=True)
    with pytest.raises(exports.ExportError, match="no completed current final judgment"):
        export_data.run(zero_missing=True)
    path = export_data.run(zero_missing=True, allow_partial=True)
    assert read_csv(path / "grades.csv")[1:] == [["#bob", "0", "#"]]
    export_data.judgment("retry")
    path = export_data.run(zero_missing=True)
    assert read_csv(path / "grades.csv")[1] == ["#alice", "90", "#"]


def test_collects_students_from_separate_jobs(export_data: ExportData) -> None:
    bob_folder = "789-456 - bob Bob Example - Sep 1, 2026 1200 PM"
    with zipfile.ZipFile(export_data.archive, "a") as archive:
        archive.writestr(bob_folder + "/answer.txt", "Bob's balance")
    normalized = export_data.root / "submissions/SYN_C1/S002/HW1"
    normalized.mkdir(parents=True)
    (normalized / "answer.txt").write_text("Bob's balance")
    with (export_data.root / "tables/SYN_C1/students.csv").open("a") as handle:
        handle.write("S002,SYN_C1,789,bob,Bob Example,brightspace\n")
    export_data.judgment()
    export_data.judgment(
        "bob-retry",
        student="S002",
        data=grading_data([criterion("a", 5, 10), criterion("b", 0, 2, bonus=True)]),
    )
    path = export_data.run()
    assert read_csv(path / "grades.csv")[1:] == [["#alice", "90", "#"], ["#bob", "50", "#"]]
    with zipfile.ZipFile(path / "feedback.zip") as archive:
        assert set(archive.namelist()) == {
            export_data.folder + "/feedback.pdf",
            bob_folder + "/feedback.pdf",
        }


def test_missing_download_does_not_zero_known_work(export_data: ExportData) -> None:
    table = export_data.root / "tables" / "SYN_C1" / "students.csv"
    with table.open("a") as handle:
        handle.write("S002,SYN_C1,789,bob,Bob Example,brightspace\n")
    export_data.judgment("bob-failed", student="S002", failed=True)
    export_data.judgment()
    # There is no normalized directory for S002, but the failed trial still
    # proves that absence from this ZIP cannot authorize a zero.
    with pytest.raises(exports.ExportError, match="ZIP omits a known submission"):
        export_data.run(zero_missing=True)


def test_multiple_judgments_need_selection(export_data: ExportData) -> None:
    export_data.judgment()
    export_data.judgment("retry")
    with pytest.raises(exports.ExportError, match="multiple final judgments"):
        export_data.run(zero_missing=True)
    path = export_data.run(zero_missing=True, trials=["retry/trial1"])
    assert (
        json.loads((path / "manifest.json").read_text())["students"][0]["trial"] == "retry/trial1"
    )
    with pytest.raises(exports.ExportError, match="not an eligible"):
        export_data.run(zero_missing=True, trials=["missing/trial"])


def test_superseded_judgments_cannot_be_exported(export_data: ExportData) -> None:
    export_data.judgment(count=2)
    export_data.judgment("more-evidence", count=3)
    with pytest.raises(exports.ExportError, match="no completed current"):
        export_data.run(gradings=2, zero_missing=True)
    path = export_data.run(zero_missing=True)
    assert (
        json.loads((path / "manifest.json").read_text())["students"][0]["trial"]
        == "more-evidence/trial1"
    )


def test_config_name_cannot_mix_versions(export_data: ExportData) -> None:
    export_data.judgment()
    export_data.judgment("changed-config", identity="c" * 64)
    with pytest.raises(exports.ExportError, match="--config-identity"):
        export_data.run(zero_missing=True)
    path = export_data.run(zero_missing=True, config_identity="c" * 64)
    assert (
        json.loads((path / "manifest.json").read_text())["students"][0]["trial"]
        == "changed-config/trial1"
    )


@pytest.mark.parametrize("changed", ["normalized", "judged", "feedback", "rubric", "criteria"])
def test_changed_or_missing_inputs_block_export(export_data: ExportData, changed: str) -> None:
    output = export_data.judgment()
    if changed == "normalized":
        (export_data.root / "submissions/SYN_C1/S001/HW1/answer.txt").write_text("Different work")
    elif changed == "judged":
        path = export_data.root / "grading/job1/aat-run.json"
        data = json.loads(path.read_text())
        data["items"][0]["input_hashes"]["submission"] = "different"
        path.write_text(json.dumps(data))
    elif changed == "feedback":
        (output / "feedback.md").unlink()
    elif changed == "rubric":
        (export_data.root / "tasks/job1/S001/environment/rubric.md").write_text("changed")
    else:
        path = output / "grading_result.json"
        data = json.loads(path.read_text())
        data["criteria"][0]["max_points"] = 20
        path.write_text(json.dumps(data))
    with pytest.raises(exports.ExportError, match="export incomplete"):
        export_data.run(zero_missing=True)
    assert not (export_data.root / "analysis").exists()


def test_bonus_above_gradebook_max_needs_confirmation(export_data: ExportData) -> None:
    export_data.judgment(
        data=grading_data([criterion("a", 10, 10), criterion("b", 2, 2, bonus=True)])
    )
    with pytest.raises(exports.ExportError, match="--can-exceed"):
        export_data.run(zero_missing=True)
    path = export_data.run(zero_missing=True, can_exceed=True)
    assert read_csv(path / "grades.csv")[1] == ["#alice", "120", "#"]


def test_render_failure_leaves_no_upload_snapshot(
    export_data: ExportData, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_data.judgment()

    def fail(_text: str) -> bytes:
        raise feedback_pdf.FeedbackRenderError("invalid equation")

    monkeypatch.setattr(exports, "render_pdf", fail)
    with pytest.raises(exports.ExportError, match="no upload files written"):
        export_data.run(zero_missing=True)
    assert list((export_data.root / "analysis/exports").iterdir()) == []


def test_export_refuses_repository_and_source_destinations(export_data: ExportData) -> None:
    with pytest.raises(DataRootError):
        export_data.run(out_root=Path(__file__).resolve().parents[1] / "exports")
    with pytest.raises(exports.ExportError, match="overlaps source"):
        export_data.run(out_root=export_data.root / "grading" / "exports")


def test_cli_produces_both_outputs(
    export_data: ExportData, capsys: pytest.CaptureFixture[str]
) -> None:
    export_data.judgment()
    result = cli.main(
        [
            "export-results",
            "--data-root",
            str(export_data.root),
            "--course",
            "SYN_C1",
            "--assignment",
            "HW1",
            "--config",
            "judge",
            "--context-from",
            "grader",
            "--gradings",
            "3",
            "--grade-export",
            str(export_data.roster),
            "--submissions-zip",
            str(export_data.archive),
            "--zero-missing",
        ]
    )
    assert result == 0
    output = capsys.readouterr().out
    assert "graded: 1" in output and "zero_missing: 1" in output
    assert "feedback.zip" in output and "grades.csv" in output


@pytest.mark.parametrize(
    "contents",
    [
        "Username,Assignment 1 Points Grade,End-of-Line Indicator\n#alice,,#\n",
        "Username,Assignment 1 Points Grade <Numeric MaxPoints:nan>,End-of-Line Indicator\n#alice,,#\n",
        "Username,Assignment 1 Points Grade <Numeric MaxPoints:100>,End-of-Line Indicator\n#alice,,#\nALICE,,#\n",
        "Username,Assignment 1 Points Grade <Numeric MaxPoints:100>,End-of-Line Indicator\n#alice,,\n",
    ],
)
def test_invalid_grade_templates_are_rejected(export_data: ExportData, contents: str) -> None:
    export_data.roster.write_text(contents)
    with pytest.raises(exports.ExportError):
        export_data.run()


def test_reuploads_use_ingest_merge_and_latest_folder(export_data: ExportData) -> None:
    latest = export_data.folder.replace("Sep 1", "Sep 2")
    with zipfile.ZipFile(export_data.archive, "a") as archive:
        archive.writestr(latest + "/answer.txt", "Revised balance")
        archive.writestr(latest + "/extra.txt", "Supporting work")
    normalized = export_data.root / "submissions/SYN_C1/S001/HW1"
    (normalized / "answer.txt").write_text("Revised balance")
    (normalized / "extra.txt").write_text("Supporting work")
    export_data.judgment()
    path = export_data.run(zero_missing=True)
    with zipfile.ZipFile(path / "feedback.zip") as archive:
        assert archive.namelist() == [latest + "/feedback.pdf"]


@pytest.mark.parametrize(
    "member",
    [
        "../escape.txt",
        "unexpected.txt",
        "123-999 - alice Alice Example - Sep 2, 2026 1200 PM/answer.txt",
    ],
)
def test_invalid_or_mixed_archive_is_rejected(export_data: ExportData, member: str) -> None:
    with zipfile.ZipFile(export_data.archive, "a") as archive:
        archive.writestr(member, "data")
    with pytest.raises(ingest.IngestError):
        export_data.run()


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="PDF integration requires host Pandoc")
def test_real_pdf_contains_grade_table_and_feedback(
    export_data: ExportData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exports, "render_pdf", feedback_pdf.render_pdf)
    export_data.judgment()
    path = export_data.run(zero_missing=True)
    with zipfile.ZipFile(path / "feedback.zip") as archive:
        data = archive.read(export_data.folder + "/feedback.pdf")
    reader = PdfReader(io.BytesIO(data))
    text = " ".join(page.extract_text() for page in reader.pages)
    assert "90 / 100 (90%)" in text
    assert "Alice Example" in text
    assert "Criterion scores" in text and "a: criterion a" in text
    assert "(bonus)" in text
    assert "correct balance" in text
    assert "STAFF ONLY" not in text


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="PDF integration requires host Pandoc")
def test_pdf_rejects_external_images_and_renders_code_as_text() -> None:
    with pytest.raises(feedback_pdf.FeedbackRenderError, match="text, tables"):
        feedback_pdf.render_pdf("![private](file:///etc/passwd)")
    source = (
        '```typst\n#read("/etc/passwd")\n```\n\nMath: $\\frac{a}{b}$.\n\n> Check units.\n\n---\n'
    )
    data = feedback_pdf.render_pdf(source)
    assert data == feedback_pdf.render_pdf(source)
    text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    assert 'read("/etc/passwd")' in text
