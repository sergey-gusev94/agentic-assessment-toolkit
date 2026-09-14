"""Submission ingest: adapters, merge policy, identity, receipts, freeze rule."""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader

from agentic_assessment_toolkit import cli, ingest
from agentic_assessment_toolkit.hashing import sha256_dir
from tests.conftest import COURSE_ID, build_data_root


def make_pdf(pages: list[str]) -> bytes:
    """A minimal PDF with one text block per page, extractable by pypdf."""
    objects: list[bytes] = []
    count = len(pages)
    font_number = 3 + 2 * count
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    for index, text in enumerate(pages):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
                f"/Contents {4 + 2 * index} 0 R >>"
            ).encode()
        )
        parts = ["BT /F1 12 Tf 14 TL 72 720 Td"]
        for line_number, line in enumerate(text.splitlines() or [""]):
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            if line_number:
                parts.append("T*")
            parts.append(f"({escaped}) Tj")
        parts.append("ET")
        stream = " ".join(parts).encode()
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF"
    ).encode()
    return bytes(out)


def upload_dir(
    person: str, username: str, name: str, when: str, lms_assignment: str = "9001"
) -> str:
    return f"{person}-{lms_assignment} - {username} {name} - {when}"


def write_zip(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in sorted(files.items()):
            zf.writestr(name, data)


GRADESCOPE_SUMMARY = (
    "Homework 2\nGraded\nStudent\nAlice Smith\nTotal Points\n90 / 100 pts\nQuestion 1"
)
GRADESCOPE_BANNER = "Question assigned to the following page: 1"


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return build_data_root(tmp_path)


def raw_dir(root: Path) -> Path:
    return root / "raw-submissions" / COURSE_ID


def brightspace_zip(
    root: Path,
    zip_name: str = "HW 1 Download Aug 2, 2026 625 PM.zip",
    uploads: dict[str, dict[str, bytes]] | None = None,
) -> None:
    if uploads is None:
        uploads = {
            upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM"): {
                "hw1.pdf": b"alice hw1",
            }
        }
    files = {
        f"{directory}/{relative}": data
        for directory, members in uploads.items()
        for relative, data in members.items()
    }
    write_zip(raw_dir(root) / zip_name, files)


def gradescope_zip(
    root: Path,
    zip_name: str = "hw2.zip",
    pdfs: dict[str, bytes] | None = None,
) -> None:
    if pdfs is None:
        pdfs = {"12345": make_pdf([GRADESCOPE_SUMMARY, GRADESCOPE_BANNER, "work page"])}
    write_zip(
        raw_dir(root) / zip_name,
        {f"assignment_7_export/{stem}.pdf": data for stem, data in pdfs.items()},
    )


# ---------------------------------------------------------------------------
# Zip-name → assignment matching


def test_match_assignment_normalizes_names() -> None:
    known = ["HW1", "HW2"]
    manifest = ingest._Manifest(zips={}, identities={})
    assert ingest._match_assignment("HW 1 Download Aug 2.zip", known, manifest) == "HW1"
    assert ingest._match_assignment("Homework 2 Download.zip", known, manifest) == "HW2"
    assert ingest._match_assignment("hw2.zip", known, manifest) == "HW2"
    assert ingest._match_assignment("hw_1.zip", known, manifest) == "HW1"


def test_match_assignment_zero_padding() -> None:
    manifest = ingest._Manifest(zips={}, identities={})
    assert ingest._match_assignment("hw5.zip", ["HW05"], manifest) == "HW05"
    assert ingest._match_assignment("Homework 5.zip", ["HW05", "PSO05"], manifest) == "HW05"


def test_match_assignment_rejects_unknown_and_ambiguous() -> None:
    manifest = ingest._Manifest(zips={}, identities={})
    with pytest.raises(ingest.IngestError, match="cannot infer"):
        ingest._match_assignment("final-exam-stuff.zip", ["HW1"], manifest)
    with pytest.raises(ingest.IngestError, match="no known assignment"):
        ingest._match_assignment("hw9.zip", ["HW1"], manifest)
    with pytest.raises(ingest.IngestError, match=r"matches \['HW5', 'HW05'\]"):
        ingest._match_assignment("hw5.zip", ["HW5", "HW05"], manifest)


def test_match_assignment_manifest_override() -> None:
    manifest = ingest._Manifest(zips={"odd name.zip": "HW2"}, identities={})
    assert ingest._match_assignment("odd name.zip", ["HW1", "HW2"], manifest) == "HW2"


# ---------------------------------------------------------------------------
# Brightspace: parsing and the merge policy


def test_single_upload_ingests_verbatim(data_root: Path) -> None:
    brightspace_zip(data_root)
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.status == "ready"]
    assert outcome.assignment_id == "HW1"
    assert outcome.student_id == "S001"
    assert outcome.flags == ()
    submission = data_root / "submissions" / COURSE_ID / "S001" / "HW1"
    assert (submission / "hw1.pdf").read_bytes() == b"alice hw1"
    students = ingest.read_students(data_root, COURSE_ID)
    assert [(s.student_id, s.display_name) for s in students] == [("S001", "Alice Smith")]


def test_exact_path_reupload_supersedes_and_is_recorded(data_root: Path) -> None:
    brightspace_zip(
        data_root,
        uploads={
            upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM"): {
                "hw1.pdf": b"v1",
                "notes.txt": b"notes",
            },
            upload_dir("101", "alice", "Alice Smith", "Sep 8, 2025 1004 AM"): {
                "hw1.pdf": b"v2",
            },
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.status == "ready"]
    assert "replaced_files" in outcome.flags
    assert "merged_uploads" in outcome.flags
    assert any("hw1.pdf" in item for item in outcome.replaced_files)
    submission = data_root / "submissions" / COURSE_ID / "S001" / "HW1"
    assert (submission / "hw1.pdf").read_bytes() == b"v2"
    assert (submission / "notes.txt").read_bytes() == b"notes"  # carried forward


def test_different_names_never_discard(data_root: Path) -> None:
    brightspace_zip(
        data_root,
        uploads={
            upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM"): {
                "solution.pdf": b"first",
            },
            upload_dir("101", "alice", "Alice Smith", "Sep 8, 2025 900 AM"): {
                "solution-final.pdf": b"second",
            },
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.status == "ready"]
    submission = data_root / "submissions" / COURSE_ID / "S001" / "HW1"
    assert (submission / "solution.pdf").read_bytes() == b"first"
    assert (submission / "solution-final.pdf").read_bytes() == b"second"
    assert "possible_stale_solution" in outcome.flags
    assert "replaced_files" not in outcome.flags


def test_identical_reupload_dedupes(data_root: Path) -> None:
    brightspace_zip(
        data_root,
        uploads={
            upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM"): {
                "hw1.pdf": b"same",
            },
            upload_dir("101", "alice", "Alice Smith", "Sep 8, 2025 900 AM"): {
                "hw1.pdf": b"same",
            },
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.status == "ready"]
    assert "duplicate_reupload" in outcome.flags
    assert "replaced_files" not in outcome.flags
    assert outcome.uploads == 2


@pytest.mark.parametrize("earlier_content", [b"same", b"older solution"])
def test_reviewed_upload_selection_matches_frozen_submission(
    data_root: Path, earlier_content: bytes
) -> None:
    earlier = upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM")
    selected = upload_dir("101", "alice", "Alice Smith", "Sep 8, 2025 900 AM")
    brightspace_zip(data_root, uploads={selected: {"final.pdf": b"same"}})
    ingest.ingest_course(data_root, COURSE_ID)
    submission = data_root / "submissions" / COURSE_ID / "S001" / "HW1"
    before = sha256_dir(submission)
    original_mtime = (submission / "final.pdf").stat().st_mtime_ns
    job = data_root / "grading" / "existing"
    job.mkdir(parents=True)
    (job / "aat-run.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "course_id": COURSE_ID,
                        "assignment_id": "HW1",
                        "student_id": "S001",
                        "submission_source": "student",
                    }
                ]
            }
        )
    )
    brightspace_zip(
        data_root, zip_name="HW1 earlier.zip", uploads={earlier: {"old.pdf": earlier_content}}
    )
    manifest = raw_dir(data_root) / "manifest.toml"
    manifest.write_text(
        '[[upload_selections]]\nassignment_id = "HW1"\nperson_id = "101"\n'
        f'folder = "{selected}"\nreason = "Reviewed final attempt"\n'
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = report.outcomes
    assert outcome.status == "ready"
    assert outcome.uploads == 2 and outcome.files == 1
    assert outcome.selected_upload == selected
    assert outcome.excluded_uploads == (earlier,)
    assert outcome.selection_reason == "Reviewed final attempt"
    assert sha256_dir(submission) == before
    assert (submission / "final.pdf").stat().st_mtime_ns == original_mtime
    with (data_root / "tables" / COURSE_ID / "submissions.csv").open() as handle:
        [row] = csv.DictReader(handle)
    assert json.loads(row["excluded_uploads"]) == [earlier]
    assert row["selected_upload"] == selected
    assert row["selection_reason"] == outcome.selection_reason
    assert f"excluded: {earlier}" in ingest.format_report(report)
    selections = ingest.read_upload_selections(raw_dir(data_root), "HW1")
    [exported] = ingest.read_brightspace_submissions(
        sorted(raw_dir(data_root).glob("*.zip")), selections=selections
    )
    assert exported.sha256 == before
    assert exported.folder == selected
    assert exported.excluded_uploads == (earlier,)
    assert ingest.read_upload_selections(raw_dir(data_root), "HW2") == {}
    manifest.write_text(manifest.read_text().replace(selected, earlier))
    [outcome] = ingest.ingest_course(data_root, COURSE_ID).outcomes
    assert outcome.status == "frozen"
    assert sha256_dir(submission) == before


@pytest.mark.parametrize(
    "problem",
    ["missing_folder", "absent_person", "wrong_person", "duplicate", "empty_reason", "unknown_key"],
)
def test_invalid_upload_selection_stops_ingest(data_root: Path, problem: str) -> None:
    brightspace_zip(data_root)
    person = "999" if problem == "absent_person" else "101"
    folder_person = "999" if problem == "wrong_person" else person
    date = "Sep 8, 2025 900 AM" if problem == "missing_folder" else "Sep 7, 2025 516 PM"
    folder = upload_dir(folder_person, "alice", "Alice Smith", date)
    reason = "" if problem == "empty_reason" else "Reviewed"
    entry = (
        '[[upload_selections]]\nassignment_id = "HW1"\n'
        f'person_id = "{person}"\nfolder = "{folder}"\nreason = "{reason}"\n'
    )
    if problem == "duplicate":
        entry += entry
    if problem == "unknown_key":
        entry += 'typo = "value"\n'
    (raw_dir(data_root) / "manifest.toml").write_text(entry)
    before = sha256_dir(data_root)
    with pytest.raises(ingest.IngestError):
        ingest.ingest_course(data_root, COURSE_ID)
    assert sha256_dir(data_root) == before


def test_three_digit_clock_parses() -> None:
    match = ingest._BRIGHTSPACE_DIR_RE.match(
        upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM")
    )
    assert match is not None
    assert ingest._parse_upload_time(match).isoformat() == "2025-09-07T17:16:00"


def test_unrecognized_upload_folder_fails_loudly(data_root: Path) -> None:
    write_zip(raw_dir(data_root) / "hw1.zip", {"junk-folder/file.pdf": b"x"})
    with pytest.raises(ingest.IngestError, match="unrecognized"):
        ingest.ingest_course(data_root, COURSE_ID)


def test_traversal_member_paths_are_rejected(data_root: Path) -> None:
    directory = upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM")
    write_zip(
        raw_dir(data_root) / "hw1.zip",
        {f"{directory}/hw1.pdf": b"work", f"{directory}/../../evil.txt": b"escape"},
    )
    with pytest.raises(ingest.IngestError, match="unsafe zip member path"):
        ingest.ingest_course(data_root, COURSE_ID)
    assert not (data_root / "evil.txt").exists()


def test_mixed_and_empty_zip_layouts_fail_clearly(data_root: Path) -> None:
    directory = upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM")
    write_zip(
        raw_dir(data_root) / "hw1.zip",
        {f"{directory}/hw1.pdf": b"a", "assignment_7_export/1.pdf": b"b"},
    )
    with pytest.raises(ingest.IngestError, match="mixed Brightspace and Gradescope"):
        ingest.ingest_course(data_root, COURSE_ID)
    write_zip(raw_dir(data_root) / "hw1.zip", {"loose-file.pdf": b"c"})
    with pytest.raises(ingest.IngestError, match="no submission directories"):
        ingest.ingest_course(data_root, COURSE_ID)


def test_both_adapters_for_one_assignment_fail(data_root: Path) -> None:
    brightspace_zip(data_root, zip_name="hw1-brightspace.zip")
    gradescope_zip(data_root, zip_name="hw1-gradescope.zip")
    with pytest.raises(ingest.IngestError, match="both Brightspace and Gradescope exports"):
        ingest.ingest_course(data_root, COURSE_ID)


def test_identity_changed_is_flagged(data_root: Path) -> None:
    brightspace_zip(data_root)  # run 1: Alice Smith → S001
    ingest.ingest_course(data_root, COURSE_ID)
    brightspace_zip(
        data_root,
        zip_name="HW 1 Download later.zip",
        uploads={
            upload_dir("101", "alice", "Alice Jones", "Sep 9, 2025 900 AM"): {
                "hw1.pdf": b"renamed student"
            },
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.status == "ready"]
    assert "identity_changed" in outcome.flags
    [student] = ingest.read_students(data_root, COURSE_ID)
    assert student.display_name == "Alice Smith"  # table keeps first-seen identity


def test_root_level_files_are_ignored(data_root: Path) -> None:
    directory = upload_dir("101", "alice", "Alice Smith", "Sep 7, 2025 516 PM")
    write_zip(
        raw_dir(data_root) / "hw1.zip",
        {"index.html": b"<html>", f"{directory}/hw1.pdf": b"work"},
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    assert [o.status for o in report.outcomes] == ["ready"]


# ---------------------------------------------------------------------------
# Gradescope: splitting and identity


def test_gradescope_split_writes_submission_pages_only(data_root: Path) -> None:
    gradescope_zip(data_root)
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.status == "ready"]
    assert outcome.assignment_id == "HW2"
    assert outcome.source == "gradescope"
    submission = data_root / "submissions" / COURSE_ID / outcome.student_id / "HW2"
    pages = PdfReader(submission / "submission.pdf").pages
    texts = [page.extract_text() for page in pages]
    assert len(texts) == 2
    assert texts[0].startswith("Question assigned")
    assert "Total Points" not in "".join(texts)
    summary = (
        data_root
        / "tables"
        / COURSE_ID
        / ingest.SUMMARIES_DIRNAME
        / "HW2"
        / f"{outcome.student_id}.pdf"
    )
    assert "Total Points" in PdfReader(summary).pages[0].extract_text()


def test_gradescope_name_joins_existing_student(data_root: Path) -> None:
    brightspace_zip(data_root)  # creates S001 Alice Smith
    gradescope_zip(data_root)  # PDF names the same Alice Smith
    report = ingest.ingest_course(data_root, COURSE_ID)
    ready = {o.assignment_id: o for o in report.outcomes if o.status == "ready"}
    assert ready["HW1"].student_id == ready["HW2"].student_id == "S001"
    assert len(ingest.read_students(data_root, COURSE_ID)) == 1


def test_gradescope_unmatched_name_creates_student(data_root: Path) -> None:
    gradescope_zip(data_root)
    report = ingest.ingest_course(data_root, COURSE_ID)
    [added] = report.students_added
    assert added.source == "gradescope"
    assert added.display_name == "Alice Smith"
    assert added.lms_person_id == ""


def test_gradescope_without_banner_is_skipped(data_root: Path) -> None:
    gradescope_zip(data_root, pdfs={"777": make_pdf(["just some page", "another page"])})
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = report.outcomes
    assert outcome.status == "skipped"
    assert outcome.flags == ("no_split_marker",)
    assert not (data_root / "submissions" / COURSE_ID).exists() or not any(
        (data_root / "submissions" / COURSE_ID).glob("S*/")
    )


def test_gradescope_without_name_is_skipped_then_manifest_resolves(data_root: Path) -> None:
    headerless = make_pdf(["rubric only, no header", GRADESCOPE_BANNER, "work"])
    gradescope_zip(data_root, pdfs={"888": headerless})
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = report.outcomes
    assert outcome.status == "skipped"
    assert outcome.flags == ("identity_unresolved",)

    (raw_dir(data_root) / ingest.MANIFEST_FILENAME).write_text(
        '[identities]\n"888" = "Casey Jones"\n', encoding="utf-8"
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.status == "ready"]
    assert "identity_from_manifest" in outcome.flags
    students = ingest.read_students(data_root, COURSE_ID)
    assert [s.display_name for s in students] == ["Casey Jones"]


def _two_alices_zip(root: Path) -> None:
    """Two distinct LMS persons who share a display name."""
    brightspace_zip(
        root,
        uploads={
            upload_dir("101", "alice1", "Alice Smith", "Sep 7, 2025 516 PM"): {
                "hw1.pdf": b"first alice"
            },
            upload_dir("202", "alice2", "Alice Smith", "Sep 7, 2025 600 PM"): {
                "hw1.pdf": b"second alice"
            },
        },
    )


def test_gradescope_ambiguous_name_is_skipped(data_root: Path) -> None:
    _two_alices_zip(data_root)
    gradescope_zip(data_root)  # PDF names "Alice Smith"
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.assignment_id == "HW2" and o.status != "missing"]
    assert outcome.status == "skipped"
    assert outcome.flags == ("ambiguous_identity",)


def test_manifest_sid_override_beats_duplicate_names(data_root: Path) -> None:
    _two_alices_zip(data_root)
    gradescope_zip(data_root)
    (raw_dir(data_root) / ingest.MANIFEST_FILENAME).write_text(
        '[identities]\n"12345" = "S002"\n', encoding="utf-8"
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.assignment_id == "HW2" and o.status == "ready"]
    assert outcome.student_id == "S002"
    assert "identity_from_manifest" in outcome.flags


def test_manifest_unknown_student_id_is_skipped(data_root: Path) -> None:
    gradescope_zip(data_root)
    (raw_dir(data_root) / ingest.MANIFEST_FILENAME).write_text(
        '[identities]\n"12345" = "S999"\n', encoding="utf-8"
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = report.outcomes
    assert outcome.status == "skipped"
    assert "unknown_student_id" in outcome.flags
    # A typo'd S-id must never become a phantom student named "S999".
    assert all(s.display_name != "S999" for s in ingest.read_students(data_root, COURSE_ID))


def test_gradescope_duplicate_student_is_skipped(data_root: Path) -> None:
    gradescope_zip(
        data_root,
        pdfs={
            "1": make_pdf([GRADESCOPE_SUMMARY, GRADESCOPE_BANNER, "first"]),
            "2": make_pdf([GRADESCOPE_SUMMARY, GRADESCOPE_BANNER, "second"]),
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    statuses = {o.source_ref: o for o in report.outcomes if o.source_ref}
    assert statuses["1"].status == "ready"
    assert statuses["2"].status == "skipped"
    assert statuses["2"].flags == ("duplicate_student",)


def test_unreadable_pdf_is_skipped_not_fatal(data_root: Path) -> None:
    gradescope_zip(
        data_root,
        pdfs={
            "1": b"not a pdf at all",
            "2": make_pdf([GRADESCOPE_SUMMARY, GRADESCOPE_BANNER, "fine"]),
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    statuses = {o.source_ref: o for o in report.outcomes if o.source_ref}
    assert statuses["1"].status == "skipped"
    assert statuses["1"].flags == ("unreadable_pdf",)
    assert statuses["2"].status == "ready"


def test_gradescope_conflicting_duplicate_ids_fail(data_root: Path) -> None:
    gradescope_zip(data_root, zip_name="hw2.zip")
    gradescope_zip(
        data_root,
        zip_name="hw2-again.zip",
        pdfs={"12345": make_pdf([GRADESCOPE_SUMMARY, GRADESCOPE_BANNER, "different"])},
    )
    with pytest.raises(ingest.IngestError, match="different bytes"):
        ingest.ingest_course(data_root, COURSE_ID)


# ---------------------------------------------------------------------------
# Receipts, idempotence, and the freeze rule


def test_receipt_and_doneness(data_root: Path) -> None:
    brightspace_zip(data_root)
    [course] = ingest.list_raw_courses(data_root)
    assert course.status == "pending"
    ingest.ingest_course(data_root, COURSE_ID)
    [course] = ingest.list_raw_courses(data_root)
    assert course.status == "done"
    (raw_dir(data_root) / "late-note.txt").write_text("new material", encoding="utf-8")
    [course] = ingest.list_raw_courses(data_root)
    assert course.status == "pending"


def test_incremental_run_keeps_student_ids_stable(data_root: Path) -> None:
    brightspace_zip(data_root)  # run 1: Alice Smith → S001
    ingest.ingest_course(data_root, COURSE_ID)
    # Run 2 adds a Gradescope zip naming a student who sorts before
    # Alice; the pseudonym mapping must extend, never renumber.
    gradescope_zip(
        data_root,
        pdfs={
            "9": make_pdf(
                ["Homework 2\nStudent\nAaron Aardvark\nTotal Points", GRADESCOPE_BANNER, "work"]
            )
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    students = {s.display_name: s.student_id for s in ingest.read_students(data_root, COURSE_ID)}
    assert students == {"Alice Smith": "S001", "Aaron Aardvark": "S002"}
    ready = {o.assignment_id: o.student_id for o in report.outcomes if o.status == "ready"}
    assert ready == {"HW1": "S001", "HW2": "S002"}


def test_frozen_item_with_unchanged_bytes_stays_ready(data_root: Path) -> None:
    brightspace_zip(data_root)
    ingest.ingest_course(data_root, COURSE_ID)
    job_dir = data_root / "grading" / "20260802T000000Z__cfg__deadbeef"
    job_dir.mkdir(parents=True)
    (job_dir / "aat-run.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "item_id": f"{COURSE_ID}/S001/HW1",
                        "course_id": COURSE_ID,
                        "assignment_id": "HW1",
                        "submission_source": "student",
                        "student_id": "S001",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.assignment_id == "HW1"]
    assert outcome.status == "ready"  # byte-identical recompute is not a conflict


def test_second_run_is_idempotent(data_root: Path) -> None:
    brightspace_zip(data_root)
    gradescope_zip(data_root)
    ingest.ingest_course(data_root, COURSE_ID)
    tables = data_root / "tables" / COURSE_ID
    first = {p: p.read_bytes() for p in tables.rglob("*") if p.is_file()}
    ingest.ingest_course(data_root, COURSE_ID)
    second = {p: p.read_bytes() for p in tables.rglob("*") if p.is_file()}
    assert first == second


def test_frozen_submission_is_never_rewritten(data_root: Path) -> None:
    brightspace_zip(data_root)
    ingest.ingest_course(data_root, COURSE_ID)
    submission = data_root / "submissions" / COURSE_ID / "S001" / "HW1"
    before = (submission / "hw1.pdf").read_bytes()

    job_dir = data_root / "grading" / "20260802T000000Z__cfg__deadbeef"
    job_dir.mkdir(parents=True)
    (job_dir / "aat-run.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "item_id": f"{COURSE_ID}/S001/HW1",
                        "course_id": COURSE_ID,
                        "assignment_id": "HW1",
                        "submission_source": "student",
                        "student_id": "S001",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    brightspace_zip(
        data_root,
        zip_name="HW 1 Download later.zip",
        uploads={
            upload_dir("101", "alice", "Alice Smith", "Sep 9, 2025 900 AM"): {
                "hw1.pdf": b"late rewrite",
            },
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    [outcome] = [o for o in report.outcomes if o.assignment_id == "HW1"]
    assert outcome.status == "frozen"
    assert "frozen_submission_changed" in outcome.flags
    assert (submission / "hw1.pdf").read_bytes() == before


def test_missing_rows_cover_known_students(data_root: Path) -> None:
    brightspace_zip(data_root)  # Alice submits HW1
    gradescope_zip(
        data_root,
        pdfs={
            "1": make_pdf(
                ["Homework 2\nStudent\nBob Stone\nTotal Points", GRADESCOPE_BANNER, "bob work"]
            )
        },
    )
    report = ingest.ingest_course(data_root, COURSE_ID)
    missing = {(o.assignment_id, o.student_id) for o in report.outcomes if o.status == "missing"}
    alice = next(s for s in ingest.read_students(data_root, COURSE_ID) if "Alice" in s.display_name)
    bob = next(s for s in ingest.read_students(data_root, COURSE_ID) if "Bob" in s.display_name)
    assert (("HW2", alice.student_id)) in missing
    assert (("HW1", bob.student_id)) in missing


# ---------------------------------------------------------------------------
# CLI


def test_cli_dry_run_lists_courses_and_writes_nothing(
    data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    brightspace_zip(data_root)
    assert (
        cli.main(["ingest-submissions", "--data-root", str(data_root), "--all", "--dry-run"]) == 0
    )
    out = capsys.readouterr().out
    assert "pending" in out
    assert COURSE_ID in out
    assert not (data_root / "submissions" / COURSE_ID / "S001").exists()
    assert not (data_root / "tables" / COURSE_ID).exists()
    assert ingest.read_record(data_root, COURSE_ID) is None


def test_cli_force_reprocesses_done_course(
    data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    brightspace_zip(data_root)
    assert (
        cli.main(["ingest-submissions", "--data-root", str(data_root), "--course", COURSE_ID]) == 0
    )
    capsys.readouterr()
    assert (
        cli.main(
            ["ingest-submissions", "--data-root", str(data_root), "--course", COURSE_ID, "--force"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "nothing to do" not in out
    assert "1 ready" in out


def test_cli_unchanged_course_still_reports_persisted_attention(
    data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gradescope_zip(data_root, pdfs={"777": make_pdf(["no banner here"])})
    assert (
        cli.main(["ingest-submissions", "--data-root", str(data_root), "--course", COURSE_ID]) == 1
    )
    capsys.readouterr()
    # The dump is unchanged, so the course is skipped — but its
    # unresolved rows must keep the run loud, never "nothing to do".
    assert (
        cli.main(["ingest-submissions", "--data-root", str(data_root), "--course", COURSE_ID]) == 1
    )
    out = capsys.readouterr().out
    assert "still need review" in out
    assert "nothing to do" not in out


def test_cli_ingests_and_reports(data_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    brightspace_zip(data_root)
    assert (
        cli.main(["ingest-submissions", "--data-root", str(data_root), "--course", COURSE_ID]) == 0
    )
    out = capsys.readouterr().out
    assert "1 ready" in out
    assert (
        cli.main(["ingest-submissions", "--data-root", str(data_root), "--course", COURSE_ID]) == 0
    )
    assert "nothing to do" in capsys.readouterr().out


def test_cli_flags_attention_with_exit_1(
    data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gradescope_zip(data_root, pdfs={"777": make_pdf(["no banner here"])})
    assert (
        cli.main(["ingest-submissions", "--data-root", str(data_root), "--course", COURSE_ID]) == 1
    )
    out = capsys.readouterr().out
    assert "need review" in out
    assert "no_split_marker" in out


def test_cli_requires_selection(data_root: Path) -> None:
    assert cli.main(["ingest-submissions", "--data-root", str(data_root)]) == 2
