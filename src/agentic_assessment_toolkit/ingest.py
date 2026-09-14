"""`aat ingest-submissions`: normalize LMS exports into the submissions tree.

Submission ingest (docs/data-conventions.md, "Submission ingest")
converts `raw-submissions/<course_id>/` — LMS export zips kept verbatim
— into `submissions/<course_id>/<student_id>/<assignment_id>/` plus the
identity and bookkeeping tables under `tables/<course_id>/`. It is
deterministic code, never an agent: the exports are uniformly
structured, and real student identities must be pseudonymized before
anything reaches an LLM.

Two adapters, auto-detected from each zip's internal shape:

- **Brightspace** — timestamped per-upload folders. All of a student's
  uploads for an assignment merge into one effective submission: union
  by relative path, where a later upload's file with exactly the same
  path supersedes the earlier version (recorded, never silent), and
  nothing else is discarded by the merge. A reviewed upload selection in
  the manifest instead retains one exact upload folder and records the
  excluded folders and reason.
- **Gradescope** — one graded "Print Submission" PDF per submission.
  The grade-summary pages are split off at the first page carrying a
  question-assignment banner; only the submission pages reach the
  submissions tree, and the summary pages are stored under
  `tables/<course_id>/gradescope-summaries/` for later use.

Doneness mirrors course intake: a course is processed when its
`submissions/<course_id>/ingest-record.json` receipt records the
current hash of the raw dump (including the optional `manifest.toml`,
so a manifest fix makes the course pending again).
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from . import __version__
from .course import CourseError, load_course
from .hashing import sha256_bytes, sha256_dir, sha256_manifest

RECORD_FILENAME = "ingest-record.json"
RECORD_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.toml"
STUDENTS_CSV = "students.csv"
SUBMISSIONS_CSV = "submissions.csv"
SUMMARIES_DIRNAME = "gradescope-summaries"
GRADING_JOBS_DIRNAME = "grading"

# File types that carry the solution itself (vs. supplementary data);
# used only for the advisory stale-version flag, never for discarding.
SOLUTION_SUFFIXES = frozenset({".pdf", ".ipynb"})

# Brightspace upload folder:
#   "<person>-<assignment> - <username> <Display Name> - Sep 7, 2025 516 PM"
_BRIGHTSPACE_DIR_RE = re.compile(
    r"^(?P<person_id>\d+)-(?P<lms_assignment_id>\d+)"
    r"\s+-\s+(?P<username>\S+)\s+(?P<display_name>.+?)"
    r"\s+-\s+(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),"
    r"\s+(?P<year>\d{4})\s+(?P<clock>\d{3,4})\s+(?P<ampm>AM|PM)$"
)

# Gradescope export root: "assignment_<id>_export".
_GRADESCOPE_DIR_RE = re.compile(r"^assignment_\d+_export$")

# The banner Gradescope prints at the top of every submission page of a
# graded-copy PDF; the first page carrying one starts the submission.
_GRADESCOPE_BANNER_RE = re.compile(
    r"^(?:Questions? assigned to the following pages?:"
    r"|No questions assigned to the following page)"
)

# The student name on a graded-copy PDF's first summary page.
_GRADESCOPE_STUDENT_RE = re.compile(r"(?:^|\n)\s*Student\s*\n+(?P<name>[^\n]+)")

# "Homework 3", "HW 3", "hw3", "hw_3" in a zip name → series HW, number 3.
_ZIP_NAME_RE = re.compile(
    r"(?i)\b(?P<series>homework|hw|pso|problem\s*set|exam)\s*_?\s*(?P<number>\d+)\b"
)
_SERIES_ALIASES = {"homework": "HW", "hw": "HW", "pso": "PSO", "problemset": "PSO", "exam": "EXAM"}

# Known assignment ids split into series letters + number: HW05 → (HW, 5).
_ASSIGNMENT_ID_RE = re.compile(r"^(?P<series>[A-Za-z]+?)0*(?P<number>\d+)$")

_STUDENT_ID_RE = re.compile(r"^S(?P<number>\d{3,})$")

STUDENTS_COLUMNS = (
    "student_id",
    "course_id",
    "lms_person_id",
    "lms_username",
    "display_name",
    "source",
)
SUBMISSIONS_COLUMNS = (
    "course_id",
    "assignment_id",
    "student_id",
    "source",
    "source_ref",
    "status",
    "submitted_at",
    "uploads",
    "files",
    "flags",
    "replaced_files",
    "selected_upload",
    "excluded_uploads",
    "selection_reason",
)


class IngestError(Exception):
    """A raw dump, manifest, or zip that ingest refuses to guess about."""


@dataclass(frozen=True)
class IngestCourse:
    """One raw submissions dump and its processing state."""

    course_id: str
    raw_dir: Path
    status: str  # "pending" | "done"


@dataclass(frozen=True)
class Student:
    student_id: str
    course_id: str
    lms_person_id: str  # empty for Gradescope-only students
    lms_username: str  # empty for Gradescope-only students
    display_name: str
    source: str  # "brightspace" | "gradescope" | "manifest"


@dataclass(frozen=True)
class Outcome:
    """One (assignment, student-or-unresolved) row of the ingest report."""

    course_id: str
    assignment_id: str
    student_id: str  # empty when identity is unresolved
    source: str  # "brightspace" | "gradescope" | ""
    source_ref: str  # LMS person id / Gradescope submission id
    status: str  # "ready" | "skipped" | "frozen" | "missing"
    submitted_at: str  # ISO string, empty when the source has none
    uploads: int
    files: int
    flags: tuple[str, ...] = ()
    replaced_files: tuple[str, ...] = ()
    selected_upload: str = ""
    excluded_uploads: tuple[str, ...] = ()
    selection_reason: str = ""

    @property
    def attention(self) -> bool:
        """True when a human must look: content skipped or frozen."""
        return self.status in ("skipped", "frozen")


@dataclass(frozen=True)
class CourseReport:
    course_id: str
    outcomes: tuple[Outcome, ...]
    students_added: tuple[Student, ...]

    @property
    def attention(self) -> tuple[Outcome, ...]:
        return tuple(o for o in self.outcomes if o.attention)


@dataclass(frozen=True)
class _Upload:
    """One Brightspace upload folder inside one zip."""

    person_id: str
    lms_assignment_id: str
    username: str
    display_name: str
    submitted_at: datetime
    dir_name: str
    zip_path: Path
    # zip member name per file, keyed by path relative to the upload dir.
    members: dict[str, str]


@dataclass(frozen=True)
class _MergedFile:
    member: str
    zip_path: Path
    upload_index: int  # which upload (chronological) the file came from


@dataclass
class _Plan:
    """Everything computed before anything is written."""

    outcomes: list[Outcome] = field(default_factory=list)
    # (assignment_id, student_id) → callable-free staged content:
    # relative path → bytes are produced lazily by `_stage`.
    writes: dict[tuple[str, str], dict[str, _MergedFile] | bytes] = field(default_factory=dict)
    # (assignment_id, student_id) → grade-summary PDF bytes.
    summaries: dict[tuple[str, str], bytes] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Selection and doneness


def list_raw_courses(root: Path, only: str | None = None) -> list[IngestCourse]:
    """Raw submission dumps with their processing status."""
    raw_root = root / "raw-submissions"
    if not raw_root.is_dir():
        return []
    courses = []
    for entry in sorted(raw_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or (only is not None and entry.name != only):
            continue
        courses.append(
            IngestCourse(course_id=entry.name, raw_dir=entry, status=_status(root, entry))
        )
    return courses


def _status(root: Path, raw_dir: Path) -> str:
    record = read_record(root, raw_dir.name)
    if record is None:
        return "pending"
    return "done" if record.get("raw_sha256") == sha256_dir(raw_dir) else "pending"


def record_path(root: Path, course_id: str) -> Path:
    return root / "submissions" / course_id / RECORD_FILENAME


def read_record(root: Path, course_id: str) -> dict[str, object] | None:
    path = record_path(root, course_id)
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def recorded_attention(record: dict[str, object] | None) -> int:
    """Skipped + frozen counts a receipt recorded for its last run."""
    if record is None:
        return 0
    counts = record.get("outcome_counts")
    if not isinstance(counts, dict):
        return 0
    total = 0
    for status in ("skipped", "frozen"):
        value = counts.get(status)
        if isinstance(value, int):
            total += value
    return total


def write_record(root: Path, report: CourseReport) -> Path:
    counts: dict[str, int] = {}
    for outcome in report.outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "created_utc": datetime.now(UTC).isoformat(),
        "toolkit_version": __version__,
        "course_id": report.course_id,
        "raw_sha256": sha256_dir(root / "raw-submissions" / report.course_id),
        "outcome_counts": dict(sorted(counts.items())),
    }
    path = record_path(root, report.course_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Manifest


@dataclass(frozen=True)
class UploadSelection:
    """An explicitly reviewed Brightspace upload to use for one submission."""

    folder: str
    reason: str


@dataclass(frozen=True)
class _Manifest:
    zips: dict[str, str]  # zip filename → assignment id
    identities: dict[str, str]  # Gradescope submission id → student id or display name
    upload_selections: dict[tuple[str, str], UploadSelection] = field(default_factory=dict)


def _load_manifest(raw_dir: Path) -> _Manifest:
    path = raw_dir / MANIFEST_FILENAME
    if not path.is_file():
        return _Manifest(zips={}, identities={})
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise IngestError(f"cannot read {path} as UTF-8: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise IngestError(f"{path} is not valid TOML: {error}") from error
    unknown = sorted(set(data) - {"zips", "identities", "upload_selections"})
    if unknown:
        raise IngestError(f"{path} has unknown keys: {', '.join(unknown)}")
    return _Manifest(
        zips=_str_table(data.get("zips"), path, "zips"),
        identities=_str_table(data.get("identities"), path, "identities"),
        upload_selections=_parse_upload_selections(data.get("upload_selections", []), path),
    )


def _parse_upload_selections(value: object, path: Path) -> dict[tuple[str, str], UploadSelection]:
    fields = {"assignment_id", "person_id", "folder", "reason"}
    if not isinstance(value, list):
        raise IngestError(f"{path}: upload_selections must be an array of tables")
    selections = {}
    for entry in value:
        if (
            not isinstance(entry, dict)
            or set(entry) != fields
            or not all(isinstance(v, str) and v.strip() for v in entry.values())
        ):
            raise IngestError(
                f"{path}: each upload selection requires nonempty strings: "
                "assignment_id, person_id, folder, reason"
            )
        match = _BRIGHTSPACE_DIR_RE.fullmatch(entry["folder"])
        if match is None or match["person_id"] != entry["person_id"]:
            raise IngestError(f"{path}: selected upload folder must belong to person_id")
        key = (entry["assignment_id"], entry["person_id"])
        if key in selections:
            raise IngestError(f"{path}: duplicate upload selection for {key}")
        selections[key] = UploadSelection(entry["folder"], entry["reason"])
    return selections


def read_upload_selections(raw_dir: Path, assignment_id: str) -> dict[str, UploadSelection]:
    """Read the course's reviewed upload selections for one assignment."""
    return {
        person: selection
        for (assignment, person), selection in _load_manifest(raw_dir).upload_selections.items()
        if assignment == assignment_id
    }


def _str_table(value: object, path: Path, key: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise IngestError(f"{path}: [{key}] must map strings to strings")
    return dict(value)


# ---------------------------------------------------------------------------
# Zip → assignment matching


def _known_assignment_ids(root: Path, course_id: str) -> list[str]:
    """Assignment ids the course knows: directory names plus registry ids."""
    course_dir = root / "courses" / course_id
    ids: set[str] = set()
    assignments_dir = course_dir / "assignments"
    if assignments_dir.is_dir():
        ids.update(entry.name for entry in assignments_dir.iterdir() if entry.is_dir())
    course_toml = course_dir / "course.toml"
    if course_toml.is_file():
        try:
            course = load_course(course_toml)
        except CourseError as error:
            raise IngestError(f"cannot load {course_toml}: {error}") from error
        ids.update(a.id for a in course.assessments or ())
    return sorted(ids)


def _match_assignment(zip_name: str, known_ids: list[str], manifest: _Manifest) -> str:
    if zip_name in manifest.zips:
        return manifest.zips[zip_name]
    parsed = _ZIP_NAME_RE.search(Path(zip_name).stem)
    if parsed is None:
        raise IngestError(
            f"cannot infer an assignment from zip name {zip_name!r}; add it to "
            f"{MANIFEST_FILENAME} under [zips]"
        )
    series = _SERIES_ALIASES[re.sub(r"\s", "", parsed.group("series").lower())]
    number = int(parsed.group("number"))
    matches = []
    for known in known_ids:
        known_parsed = _ASSIGNMENT_ID_RE.match(known)
        if (
            known_parsed is not None
            and known_parsed.group("series").upper() == series
            and int(known_parsed.group("number")) == number
        ):
            matches.append(known)
    if len(matches) != 1:
        detail = (
            f"matches {matches!r}" if matches else f"no known assignment matches {series}{number}"
        )
        raise IngestError(
            f"zip name {zip_name!r} is ambiguous for course assignments: {detail}; "
            f"map it explicitly in {MANIFEST_FILENAME} under [zips]"
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Adapter detection and parsing


def _zip_top_dirs(zf: zipfile.ZipFile) -> list[str]:
    tops = {name.split("/", 1)[0] for name in zf.namelist() if "/" in name}
    return sorted(tops)


def _detect_adapter(zip_path: Path, zf: zipfile.ZipFile) -> str:
    tops = _zip_top_dirs(zf)
    if not tops:
        raise IngestError(f"{zip_path.name}: zip contains no submission directories")
    unrecognized = [
        t for t in tops if not (_GRADESCOPE_DIR_RE.match(t) or _BRIGHTSPACE_DIR_RE.match(t))
    ]
    if unrecognized:
        raise IngestError(
            f"{zip_path.name}: unrecognized export layout; "
            f"unmatched top-level directories: {unrecognized!r}"
        )
    if all(_GRADESCOPE_DIR_RE.match(t) for t in tops):
        return "gradescope"
    if all(_BRIGHTSPACE_DIR_RE.match(t) for t in tops):
        return "brightspace"
    raise IngestError(f"{zip_path.name}: mixed Brightspace and Gradescope layouts in one zip")


def _validate_member_path(zip_name: str, relative: str) -> None:
    """Refuse zip member paths that could escape the staging directory.

    Real LMS exports use plain forward-slash relative paths; anything
    else is a corrupt or crafted archive and fails loudly rather than
    being written somewhere unexpected.
    """
    parts = relative.split("/")
    if (
        relative.startswith("/")
        or "\\" in relative
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise IngestError(f"{zip_name}: unsafe zip member path {relative!r}")


def _parse_upload_time(match: re.Match[str]) -> datetime:
    clock = match.group("clock")
    hour, minute = (clock[0], clock[1:]) if len(clock) == 3 else (clock[:2], clock[2:])
    stamp = (
        f"{match.group('month')} {match.group('day')}, {match.group('year')} "
        f"{hour}:{minute} {match.group('ampm')}"
    )
    return datetime.strptime(stamp, "%b %d, %Y %I:%M %p")


def _brightspace_uploads(zip_path: Path, zf: zipfile.ZipFile) -> list[_Upload]:
    by_dir: dict[str, dict[str, str]] = {}
    for name in zf.namelist():
        if name.endswith("/") or "/" not in name:
            continue  # directory entries and root-level files (e.g. index.html)
        top, relative = name.split("/", 1)
        _validate_member_path(zip_path.name, relative)
        by_dir.setdefault(top, {})[relative] = name
    uploads = []
    for dir_name in sorted(by_dir):
        match = _BRIGHTSPACE_DIR_RE.match(dir_name)
        if match is None:
            raise IngestError(f"{zip_path.name}: unrecognized upload folder {dir_name!r}")
        uploads.append(
            _Upload(
                person_id=match.group("person_id"),
                lms_assignment_id=match.group("lms_assignment_id"),
                username=match.group("username"),
                display_name=match.group("display_name"),
                submitted_at=_parse_upload_time(match),
                dir_name=dir_name,
                zip_path=zip_path,
                members=by_dir[dir_name],
            )
        )
    return uploads


# ---------------------------------------------------------------------------
# Students table


@dataclass(frozen=True)
class BrightspaceSubmission:
    """A merged submission and its original folder for returning feedback."""

    person_id: str
    username: str
    display_name: str
    folder: str
    sha256: str
    selection: UploadSelection | None = None
    excluded_uploads: tuple[str, ...] = ()


def read_brightspace_submissions(
    paths: list[Path], *, selections: dict[str, UploadSelection] | None = None
) -> list[BrightspaceSubmission]:
    """Read one assignment's downloads using the same merge rules as ingest.

    No files are extracted. The selected folder, or otherwise the latest
    upload folder, receives the feedback. Assignment IDs and identities
    must agree.
    """
    uploads = []
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise IngestError(f"{path.name}: duplicate ZIP member names")
            for name in names:
                _validate_member_path(path.name, name.rstrip("/"))
                if "/" not in name and name != "index.html":
                    raise IngestError(f"{path.name}: unexpected root file {name!r}")
            uploads.extend(_brightspace_uploads(path, archive))
    if not uploads:
        raise IngestError("the selected ZIPs contain no file submissions")
    if len({upload.lms_assignment_id for upload in uploads}) != 1:
        raise IngestError("the selected ZIPs contain more than one Brightspace assignment")
    people: dict[str, list[_Upload]] = {}
    for upload in uploads:
        people.setdefault(upload.person_id, []).append(upload)
    selections = selections or {}
    unknown = selections.keys() - people.keys()
    if unknown:
        raise IngestError(
            f"upload selections refer to people absent from the ZIPs: {sorted(unknown)}"
        )
    result = []
    for person_id, group in sorted(people.items()):
        if len({u.username.lstrip("#").casefold() for u in group}) != 1:
            raise IngestError(f"Brightspace person {person_id} has conflicting usernames")
        selection = selections.get(person_id)
        selected, excluded = _select_uploads(group, selection)
        merged, _, _ = _merge_uploads(selected)
        manifest = {}
        for relative, entry in merged.items():
            with zipfile.ZipFile(entry.zip_path) as archive:
                manifest[relative] = sha256_bytes(archive.read(entry.member))
        latest = max(selected, key=lambda u: (u.submitted_at, u.dir_name))
        result.append(
            BrightspaceSubmission(
                person_id,
                latest.username,
                latest.display_name,
                latest.dir_name,
                sha256_manifest(manifest),
                selection,
                excluded,
            )
        )
    return result


def read_students(root: Path, course_id: str) -> list[Student]:
    path = root / "tables" / course_id / STUDENTS_CSV
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or set(STUDENTS_COLUMNS) - set(reader.fieldnames):
            raise IngestError(f"{path} does not have the expected columns {STUDENTS_COLUMNS}")
        return [
            Student(
                student_id=row["student_id"],
                course_id=row["course_id"],
                lms_person_id=row["lms_person_id"],
                lms_username=row["lms_username"],
                display_name=row["display_name"],
                source=row["source"],
            )
            for row in reader
        ]


def _next_student_number(students: list[Student]) -> int:
    highest = 0
    for student in students:
        match = _STUDENT_ID_RE.match(student.student_id)
        if match is not None:
            highest = max(highest, int(match.group("number")))
    return highest + 1


def _write_students(root: Path, course_id: str, students: list[Student]) -> None:
    path = root / "tables" / course_id / STUDENTS_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STUDENTS_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for student in sorted(students, key=lambda s: s.student_id):
            writer.writerow(
                {
                    "student_id": student.student_id,
                    "course_id": student.course_id,
                    "lms_person_id": student.lms_person_id,
                    "lms_username": student.lms_username,
                    "display_name": student.display_name,
                    "source": student.source,
                }
            )


def _write_submissions_csv(root: Path, course_id: str, outcomes: list[Outcome]) -> None:
    path = root / "tables" / course_id / SUBMISSIONS_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUBMISSIONS_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for outcome in sorted(
            outcomes, key=lambda o: (o.assignment_id, o.student_id, o.source_ref)
        ):
            writer.writerow(
                {
                    "course_id": outcome.course_id,
                    "assignment_id": outcome.assignment_id,
                    "student_id": outcome.student_id,
                    "source": outcome.source,
                    "source_ref": outcome.source_ref,
                    "status": outcome.status,
                    "submitted_at": outcome.submitted_at,
                    "uploads": outcome.uploads,
                    "files": outcome.files,
                    "flags": ";".join(outcome.flags),
                    "replaced_files": ";".join(outcome.replaced_files),
                    "selected_upload": outcome.selected_upload,
                    "excluded_uploads": json.dumps(outcome.excluded_uploads),
                    "selection_reason": outcome.selection_reason,
                }
            )


# ---------------------------------------------------------------------------
# Frozen submissions


def frozen_student_items(root: Path) -> set[tuple[str, str, str]]:
    """(course, student, assignment) triples referenced by any grading run.

    A submission directory a grading run record points at is frozen
    (docs/data-conventions.md): its hash is recorded with results, and
    the grading per-item identity deliberately does not fold in the
    submission bytes — so a silent rewrite would never trigger a
    regrade. Ingest refuses to modify these and flags them instead.
    """
    frozen: set[tuple[str, str, str]] = set()
    jobs_root = root / GRADING_JOBS_DIRNAME
    if not jobs_root.is_dir():
        return frozen
    for job_dir in jobs_root.iterdir():
        record_file = job_dir / "aat-run.json"
        if not record_file.is_file():
            continue
        try:
            record = json.loads(record_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        items = record.get("items") if isinstance(record, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("submission_source") != "student":
                continue
            course_id = item.get("course_id")
            student_id = item.get("student_id")
            assignment_id = item.get("assignment_id")
            if (
                isinstance(course_id, str)
                and isinstance(student_id, str)
                and isinstance(assignment_id, str)
            ):
                frozen.add((course_id, student_id, assignment_id))
    return frozen


# ---------------------------------------------------------------------------
# Brightspace merge policy


def _select_uploads(
    uploads: list[_Upload], selection: UploadSelection | None
) -> tuple[list[_Upload], tuple[str, ...]]:
    if selection is None:
        return uploads, ()
    selected = [u for u in uploads if u.dir_name == selection.folder]
    if not selected:
        raise IngestError(f"selected upload folder is absent from the ZIPs: {selection.folder}")
    excluded = tuple(sorted({u.dir_name for u in uploads if u.dir_name != selection.folder}))
    return selected, excluded


def _merge_uploads(
    uploads: list[_Upload],
) -> tuple[dict[str, _MergedFile], tuple[str, ...], tuple[str, ...]]:
    """Union by relative path; an exact-path re-upload supersedes.

    Returns (merged files, flags, replaced descriptions). Nothing is
    ever discarded except an older version of the identical relative
    path, and every such replacement is recorded. Type alone never
    supersedes anything: a later supplementary PDF must not erase an
    earlier solution PDF (docs/data-conventions.md, merge policy).
    """
    ordered = sorted(uploads, key=lambda u: (u.submitted_at, u.dir_name))
    merged: dict[str, _MergedFile] = {}
    origin_time: dict[str, datetime] = {}
    hashes: dict[str, str] = {}
    flags: set[str] = set()
    replaced: list[str] = []
    for index, upload in enumerate(ordered):
        with zipfile.ZipFile(upload.zip_path) as zf:
            for relative in sorted(upload.members):
                digest = sha256_bytes(zf.read(upload.members[relative]))
                if relative in merged:
                    if hashes[relative] == digest:
                        flags.add("duplicate_reupload")
                    else:
                        flags.add("replaced_files")
                        replaced.append(
                            f"{relative} (superseded upload of "
                            f"{origin_time[relative].isoformat(timespec='minutes')})"
                        )
                merged[relative] = _MergedFile(
                    member=upload.members[relative], zip_path=upload.zip_path, upload_index=index
                )
                origin_time[relative] = upload.submitted_at
                hashes[relative] = digest
    if len(ordered) > 1:
        flags.add("merged_uploads")
        # Same-type solution files surviving from different uploads may
        # be a renamed re-upload; advisory only, a human decides.
        by_suffix: dict[str, set[int]] = {}
        for relative, merged_file in merged.items():
            suffix = Path(relative).suffix.lower()
            if suffix in SOLUTION_SUFFIXES:
                by_suffix.setdefault(suffix, set()).add(merged_file.upload_index)
        if any(len(indexes) > 1 for indexes in by_suffix.values()):
            flags.add("possible_stale_solution")
    return merged, tuple(sorted(flags)), tuple(replaced)


# ---------------------------------------------------------------------------
# Gradescope PDF splitting


@dataclass(frozen=True)
class _SplitPdf:
    submission: bytes
    summary: bytes | None
    student_name: str | None


def split_gradescope_pdf(pdf_bytes: bytes) -> _SplitPdf | None:
    """Split a graded-copy PDF at the first banner page.

    Returns None when no banner page exists — the PDF layout is not the
    graded-copy shape this adapter understands, and ingest never
    guesses at a cut.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    texts = [page.extract_text() or "" for page in reader.pages]
    split = None
    for index, text in enumerate(texts):
        if any(_GRADESCOPE_BANNER_RE.match(line.strip()) for line in text.splitlines()):
            split = index
            break
    if split is None:
        return None
    name_match = _GRADESCOPE_STUDENT_RE.search(texts[0]) if split > 0 else None
    student_name = name_match.group("name").strip() if name_match else None

    submission_writer = PdfWriter()
    for page in reader.pages[split:]:
        submission_writer.add_page(page)
    submission = io.BytesIO()
    submission_writer.write(submission)

    summary: bytes | None = None
    if split > 0:
        summary_writer = PdfWriter()
        for page in reader.pages[:split]:
            summary_writer.add_page(page)
        buffer = io.BytesIO()
        summary_writer.write(buffer)
        summary = buffer.getvalue()
    return _SplitPdf(submission=submission.getvalue(), summary=summary, student_name=student_name)


# ---------------------------------------------------------------------------
# Course ingest


def ingest_course(root: Path, course_id: str) -> CourseReport:
    """Process one course's raw submission dump end to end."""
    raw_dir = root / "raw-submissions" / course_id
    if not raw_dir.is_dir():
        raise IngestError(f"no raw submissions dump at {raw_dir}")
    manifest = _load_manifest(raw_dir)
    zip_paths = sorted(p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() == ".zip")
    if not zip_paths:
        raise IngestError(f"no export zips under {raw_dir}")
    known_ids = _known_assignment_ids(root, course_id)
    if not known_ids and not manifest.zips:
        raise IngestError(
            f"course {course_id!r} has no course tree to match assignments against; "
            f"run `aat intake` first or map zips explicitly in {MANIFEST_FILENAME}"
        )

    existing_students = read_students(root, course_id)
    plan = _Plan()
    brightspace_pools: dict[tuple[str, str], list[_Upload]] = {}
    gradescope_pdfs: dict[str, dict[str, tuple[Path, str]]] = {}

    for zip_path in zip_paths:
        assignment_id = _match_assignment(zip_path.name, known_ids, manifest)
        with zipfile.ZipFile(zip_path) as zf:
            adapter = _detect_adapter(zip_path, zf)
            if adapter == "brightspace":
                for upload in _brightspace_uploads(zip_path, zf):
                    pool = brightspace_pools.setdefault((assignment_id, upload.person_id), [])
                    pool.append(upload)
            else:
                _collect_gradescope(zip_path, zf, assignment_id, gradescope_pdfs)
    overlap = sorted(
        {assignment_id for (assignment_id, _) in brightspace_pools} & set(gradescope_pdfs)
    )
    if overlap:
        raise IngestError(
            f"assignments {overlap!r} have both Brightspace and Gradescope exports; "
            "one assignment must come from one system"
        )

    unknown = manifest.upload_selections.keys() - brightspace_pools.keys()
    if unknown:
        raise IngestError(
            f"upload selections refer to absent Brightspace submissions: {sorted(unknown)}"
        )
    students = _ingest_brightspace(
        course_id, brightspace_pools, existing_students, plan, manifest.upload_selections
    )
    students = _ingest_gradescope(course_id, gradescope_pdfs, students, manifest, plan)
    _add_missing_rows(course_id, plan, students)

    _apply_plan(root, course_id, plan)
    _write_students(root, course_id, students)
    _write_submissions_csv(root, course_id, plan.outcomes)
    known = {student.student_id for student in existing_students}
    report = CourseReport(
        course_id=course_id,
        outcomes=tuple(plan.outcomes),
        students_added=tuple(s for s in students if s.student_id not in known),
    )
    write_record(root, report)
    return report


def _collect_gradescope(
    zip_path: Path,
    zf: zipfile.ZipFile,
    assignment_id: str,
    pools: dict[str, dict[str, tuple[Path, str]]],
) -> None:
    pool = pools.setdefault(assignment_id, {})
    for name in zf.namelist():
        if name.endswith("/") or "/" not in name:
            continue
        relative = name.split("/", 1)[1]
        if "/" in relative or not relative.lower().endswith(".pdf"):
            continue  # nested paths and non-PDF metadata are not submissions
        submission_id = Path(relative).stem
        if submission_id in pool:
            existing_zip, existing_member = pool[submission_id]
            with zipfile.ZipFile(existing_zip) as existing:
                if sha256_bytes(existing.read(existing_member)) != sha256_bytes(zf.read(name)):
                    raise IngestError(
                        f"Gradescope submission {submission_id} appears in both "
                        f"{existing_zip.name} and {zip_path.name} with different bytes"
                    )
            continue
        pool[submission_id] = (zip_path, name)


def _ingest_brightspace(
    course_id: str,
    pools: dict[tuple[str, str], list[_Upload]],
    students: list[Student],
    plan: _Plan,
    selections: dict[tuple[str, str], UploadSelection],
) -> list[Student]:
    by_person = {s.lms_person_id: s for s in students if s.lms_person_id}
    new_person_ids = sorted(
        {person_id for (_, person_id) in pools if person_id not in by_person}, key=int
    )
    students = list(students)
    next_number = _next_student_number(students)
    for person_id in new_person_ids:
        uploads = [u for key, pool in pools.items() if key[1] == person_id for u in pool]
        latest = max(uploads, key=lambda u: (u.submitted_at, u.dir_name))
        student = Student(
            student_id=f"S{next_number:03d}",
            course_id=course_id,
            lms_person_id=person_id,
            lms_username=latest.username,
            display_name=latest.display_name,
            source="brightspace",
        )
        students.append(student)
        by_person[person_id] = student
        next_number += 1

    for (assignment_id, person_id), uploads in sorted(pools.items()):
        student = by_person[person_id]
        selection = selections.get((assignment_id, person_id))
        selected, excluded = _select_uploads(uploads, selection)
        merged, flags, replaced = _merge_uploads(selected)
        latest = max(selected, key=lambda u: (u.submitted_at, u.dir_name))
        if selection is not None:
            flags = tuple(sorted({*flags, "upload_selected"}))
        if (latest.username, latest.display_name) != (student.lms_username, student.display_name):
            flags = tuple(sorted({*flags, "identity_changed"}))
        plan.writes[(assignment_id, student.student_id)] = merged
        plan.outcomes.append(
            Outcome(
                course_id=course_id,
                assignment_id=assignment_id,
                student_id=student.student_id,
                source="brightspace",
                source_ref=person_id,
                status="ready",
                submitted_at=latest.submitted_at.isoformat(timespec="minutes"),
                uploads=len(uploads),
                files=len(merged),
                flags=flags,
                replaced_files=replaced,
                selected_upload=selection.folder if selection else "",
                excluded_uploads=excluded,
                selection_reason=selection.reason if selection else "",
            )
        )
    return students


def _ingest_gradescope(
    course_id: str,
    pools: dict[str, dict[str, tuple[Path, str]]],
    students: list[Student],
    manifest: _Manifest,
    plan: _Plan,
) -> list[Student]:
    students = list(students)
    seen: dict[tuple[str, str], str] = {}  # (assignment, student) → submission id
    # Two passes: resolve every PDF first so new-student ids are
    # allocated in deterministic (name-sorted) order, then plan writes.
    # A resolution is a Student (matched directly, e.g. a manifest S-id
    # or a unique name match), a display name (a new student to
    # create), or None (skip with the flags).
    resolved: list[tuple[str, str, _SplitPdf | None, Student | str | None, tuple[str, ...]]] = []
    new_names: dict[str, str] = {}  # normalized → first-seen display name
    for assignment_id, pool in sorted(pools.items()):
        for submission_id, (zip_path, member) in sorted(pool.items()):
            with zipfile.ZipFile(zip_path) as zf:
                pdf_bytes = zf.read(member)
            try:
                split = split_gradescope_pdf(pdf_bytes)
            except Exception:  # noqa: BLE001 — malformed PDFs must not kill the run
                resolved.append((assignment_id, submission_id, None, None, ("unreadable_pdf",)))
                continue
            if split is None:
                resolved.append((assignment_id, submission_id, None, None, ("no_split_marker",)))
                continue
            reference, flags = _gradescope_identity(
                submission_id, split.student_name, students, manifest
            )
            if isinstance(reference, str):
                new_names.setdefault(_normalize_name(reference), reference)
            resolved.append((assignment_id, submission_id, split, reference, flags))

    next_number = _next_student_number(students)
    created: dict[str, Student] = {}
    for normalized in sorted(new_names):
        newcomer = Student(
            student_id=f"S{next_number:03d}",
            course_id=course_id,
            lms_person_id="",
            lms_username="",
            display_name=new_names[normalized],
            source="gradescope",
        )
        students.append(newcomer)
        created[normalized] = newcomer
        next_number += 1

    for assignment_id, submission_id, split, reference, flags in resolved:
        student: Student | None = None
        if isinstance(reference, Student):
            student = reference
        elif isinstance(reference, str):
            student = created[_normalize_name(reference)]
        if split is None or student is None:
            plan.outcomes.append(
                Outcome(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    student_id="",
                    source="gradescope",
                    source_ref=submission_id,
                    status="skipped",
                    submitted_at="",
                    uploads=1,
                    files=0,
                    flags=flags if flags else ("identity_unresolved",),
                )
            )
            continue
        key = (assignment_id, student.student_id)
        if key in seen:
            plan.outcomes.append(
                Outcome(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    student_id=student.student_id,
                    source="gradescope",
                    source_ref=submission_id,
                    status="skipped",
                    submitted_at="",
                    uploads=1,
                    files=0,
                    flags=("duplicate_student",),
                )
            )
            continue
        seen[key] = submission_id
        plan.writes[key] = split.submission
        if split.summary is not None:
            plan.summaries[key] = split.summary
        plan.outcomes.append(
            Outcome(
                course_id=course_id,
                assignment_id=assignment_id,
                student_id=student.student_id,
                source="gradescope",
                source_ref=submission_id,
                status="ready",
                submitted_at="",
                uploads=1,
                files=1,
                flags=flags,
            )
        )
    return students


def _gradescope_identity(
    submission_id: str,
    extracted_name: str | None,
    students: list[Student],
    manifest: _Manifest,
) -> tuple[Student | str | None, tuple[str, ...]]:
    """Resolve a PDF to a student, a new display name, or None (skip).

    A manifest S-id override resolves to its student directly — never
    through the display name, so it works even when several students
    share a name (that ambiguity is exactly what the override exists
    to settle).
    """
    override = manifest.identities.get(submission_id)
    if override is not None:
        if _STUDENT_ID_RE.match(override):
            for student in students:
                if student.student_id == override:
                    return student, ("identity_from_manifest",)
            return None, ("identity_from_manifest", "unknown_student_id")
        matches = [s for s in students if _same_name(s.display_name, override)]
        if len(matches) > 1:
            return None, ("ambiguous_identity", "identity_from_manifest")
        if matches:
            return matches[0], ("identity_from_manifest",)
        return override, ("identity_from_manifest",)
    if extracted_name is None:
        return None, ("identity_unresolved",)
    matches = [s for s in students if _same_name(s.display_name, extracted_name)]
    if len(matches) > 1:
        return None, ("ambiguous_identity",)
    if matches:
        return matches[0], ()
    return extracted_name, ()


def _normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _same_name(a: str, b: str) -> bool:
    return _normalize_name(a) == _normalize_name(b)


def _add_missing_rows(course_id: str, plan: _Plan, students: list[Student]) -> None:
    """A known student with no submission for an ingested assignment."""
    covered = {outcome.assignment_id for outcome in plan.outcomes}
    present = {(o.assignment_id, o.student_id) for o in plan.outcomes if o.student_id}
    for assignment_id in sorted(covered):
        for student in students:
            if (assignment_id, student.student_id) in present:
                continue
            plan.outcomes.append(
                Outcome(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    student_id=student.student_id,
                    source="",
                    source_ref="",
                    status="missing",
                    submitted_at="",
                    uploads=0,
                    files=0,
                )
            )


def _apply_plan(root: Path, course_id: str, plan: _Plan) -> None:
    """Stage each submission, honor the freeze rule, and swap into place."""
    frozen = frozen_student_items(root)
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    updated: list[Outcome] = []
    for outcome in plan.outcomes:
        key = (outcome.assignment_id, outcome.student_id)
        if outcome.status != "ready" or key not in plan.writes:
            updated.append(outcome)
            continue
        target = root / "submissions" / course_id / outcome.student_id / outcome.assignment_id
        item = (course_id, outcome.student_id, outcome.assignment_id)
        with tempfile.TemporaryDirectory(prefix=".ingest-", dir=scratch) as temporary:
            staged = Path(temporary) / "submission"
            _stage(plan.writes[key], staged)
            if target.is_dir() and sha256_dir(target) == sha256_dir(staged):
                updated.append(outcome)  # byte-identical: nothing to do
            elif target.is_dir() and item in frozen:
                updated.append(
                    replace(
                        outcome,
                        status="frozen",
                        flags=tuple(sorted({*outcome.flags, "frozen_submission_changed"})),
                    )
                )
                continue
            else:
                if target.is_dir():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged), str(target))
                updated.append(outcome)
        summary = plan.summaries.get(key)
        if summary is not None:
            summary_path = (
                root
                / "tables"
                / course_id
                / SUMMARIES_DIRNAME
                / outcome.assignment_id
                / f"{outcome.student_id}.pdf"
            )
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_bytes(summary)
    plan.outcomes[:] = updated


def _stage(content: dict[str, _MergedFile] | bytes, staged: Path) -> None:
    if isinstance(content, bytes):
        staged.mkdir(parents=True)
        (staged / "submission.pdf").write_bytes(content)
        return
    by_zip: dict[Path, list[tuple[str, str]]] = {}
    for relative, merged_file in content.items():
        by_zip.setdefault(merged_file.zip_path, []).append((relative, merged_file.member))
    staged.mkdir(parents=True)
    for zip_path, entries in sorted(by_zip.items()):
        with zipfile.ZipFile(zip_path) as zf:
            for relative, member in sorted(entries):
                destination = staged / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(zf.read(member))


# ---------------------------------------------------------------------------
# Report rendering


def format_report(report: CourseReport) -> str:
    """The human review summary printed after each course."""
    counts: dict[str, int] = {}
    for outcome in report.outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    lines = [
        f"ingest {report.course_id}: "
        + ", ".join(f"{counts[status]} {status}" for status in sorted(counts))
    ]
    if report.students_added:
        lines.append(f"  new students: {', '.join(s.student_id for s in report.students_added)}")
    flagged = [o for o in report.outcomes if o.flags or o.attention]
    for outcome in sorted(flagged, key=lambda o: (o.assignment_id, o.student_id, o.source_ref)):
        who = outcome.student_id or f"?[{outcome.source_ref}]"
        detail = ", ".join(outcome.flags)
        lines.append(f"  {outcome.assignment_id} {who}: {outcome.status} ({detail})")
        for replaced in outcome.replaced_files:
            lines.append(f"    superseded: {replaced}")
        if outcome.selected_upload:
            lines.append(f"    selected: {outcome.selected_upload} ({outcome.selection_reason})")
            for excluded in outcome.excluded_uploads:
                lines.append(f"    excluded: {excluded}")
    if not flagged:
        lines.append("  nothing needs review")
    return "\n".join(lines)
