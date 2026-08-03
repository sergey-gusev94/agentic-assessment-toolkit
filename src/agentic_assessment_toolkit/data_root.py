"""Data-root resolution, refusal rules, creation, and layout accessors.

The conventions implemented here are specified in docs/data-conventions.md:
all real course and student data lives in a single directory outside the
toolkit's own repository, resolved explicitly, through ``AAT_DATA_DIR``, or
from the per-user default at ``~/aat-data``. Resolution never creates
anything; ``aat init-data`` (`init_data_root`) is the explicit way to
create the data root and its top-level layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .course import load_course

ENV_VAR = "AAT_DATA_DIR"
DEFAULT_DIRNAME = "aat-data"

TOP_LEVEL_DIRS = (
    "raw",
    "raw-submissions",
    "courses",
    "submissions",
    "tables",
    "tasks",
    "solving",
    "grading",
    "analysis",
    "scratch",
)

_TOOLKIT_NAME = "agentic-assessment-toolkit"

_KNOWN_SIDECAR_KEYS = frozenset({"environment"})

_README = """\
# Agentic Assessment Toolkit data root

All real course and student data for the `aat` toolkit lives here: raw
course dumps, assignments, reference solutions, rubrics, student
submissions, identity tables, and every job's tasks, transcripts, and
reports. Treat the whole directory as private; never publish it or copy
its contents into the toolkit repository.

Each subdirectory's purpose and rules are specified in
docs/data-conventions.md in the toolkit repository.
"""

_GITIGNORE = """\
# Regenerable outputs (toolkit docs/data-conventions.md): materialized
# task inputs, derived analysis reports, and disposable scratch space.
# Everything else here is source data or experiment results; commit it
# deliberately, and never push this repository anywhere public.
/tasks/
/analysis/
/scratch/
"""


class DataRootError(Exception):
    """The data root cannot be resolved, is refused, or has invalid contents."""


def resolve_data_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the data root: explicit path, ``AAT_DATA_DIR``, then ``~/aat-data``."""
    root, using_default = _candidate_root(explicit)
    if not root.is_dir():
        if using_default:
            raise DataRootError(
                f"default data root {root} does not exist or is not a directory; "
                f"run `aat init-data`, pass --data-root, or set {ENV_VAR}"
            )
        raise DataRootError(f"data root {root} does not exist or is not a directory")
    ensure_outside_toolkit(root, what="data root")
    return root


def _candidate_root(explicit: str | os.PathLike[str] | None) -> tuple[Path, bool]:
    """The resolution order shared by resolve and init; True means the default."""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve(), False
    env_value = os.environ.get(ENV_VAR, "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve(), False
    return (Path.home() / DEFAULT_DIRNAME).resolve(), True


def init_data_root(
    explicit: str | os.PathLike[str] | None = None, *, git: bool = False
) -> tuple[Path, list[str]]:
    """Create the data root and its top-level layout (``aat init-data``).

    The explicit counterpart to resolution never creating anything:
    makes the resolved directory, the top-level layout, and a short
    README; with ``git`` also a private git repository with a
    ``.gitignore`` for the regenerable directories. Idempotent —
    existing directories and files are kept untouched — so it also
    fills in missing top-level directories of an existing data root.
    Returns the root and the entries it created, in creation order.
    """
    root, _ = _candidate_root(explicit)
    if root.exists() and not root.is_dir():
        raise DataRootError(f"data root {root} exists and is not a directory")
    ensure_outside_toolkit(root, what="data root")
    if git and shutil.which("git") is None:
        raise DataRootError("--git needs a `git` executable on PATH, and none was found")

    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    for name in TOP_LEVEL_DIRS:
        directory = root / name
        if directory.is_dir():
            continue
        if directory.exists():
            raise DataRootError(f"{directory} exists and is not a directory")
        directory.mkdir()
        created.append(f"{name}/")
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(_README, encoding="utf-8")
        created.append(readme.name)
    if git:
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE, encoding="utf-8")
            created.append(gitignore.name)
        if not (root / ".git").exists():
            result = subprocess.run(
                ["git", "init", "--quiet", str(root)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise DataRootError(f"`git init` failed in {root}: {result.stderr.strip()}")
            created.append(".git/")
    return root, created


def ensure_outside_toolkit(path: Path, *, what: str) -> None:
    """Refuse a path inside any git working tree of this toolkit.

    Applied to the data root and to report destinations: real data and
    the reports derived from it (student identifiers, grades) must never
    land inside the always-publishable repository. Checked trees: the
    installed package location (catches editable installs), the current
    working directory, and the path itself (catches pointing at another
    checkout of the toolkit). ``what`` names the refused path in the
    error message.
    """
    anchors = (Path(__file__).resolve(), Path.cwd().resolve(), path)
    for anchor in anchors:
        git_root = _find_git_root(anchor)
        if git_root is None:
            continue
        if _declares_toolkit(git_root / "pyproject.toml") and path.is_relative_to(git_root):
            raise DataRootError(
                f"{what} {path} lies inside the toolkit repository at {git_root}; "
                "real data must live outside it (docs/data-conventions.md)"
            )


def _find_git_root(start: Path) -> Path | None:
    node = start if start.is_dir() else start.parent
    for candidate in (node, *node.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _declares_toolkit(pyproject: Path) -> bool:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return False
    project = data.get("project")
    return isinstance(project, dict) and project.get("name") == _TOOLKIT_NAME


@dataclass(frozen=True)
class Assignment:
    """One assignment resolved from the data root (frozen at first use)."""

    course_id: str
    assignment_id: str
    directory: Path
    environment_flavor: str

    @property
    def item_id(self) -> str:
        return f"{self.course_id}/{self.assignment_id}"


@dataclass(frozen=True)
class StudentSubmission:
    """One as-received student submission directory."""

    course_id: str
    student_id: str
    assignment_id: str
    directory: Path

    @property
    def item_id(self) -> str:
        return f"{self.course_id}/{self.student_id}/{self.assignment_id}"


def list_courses(root: Path) -> list[str]:
    courses_dir = root / "courses"
    if not courses_dir.is_dir():
        return []
    return sorted(entry.name for entry in courses_dir.iterdir() if entry.is_dir())


def list_assignments(root: Path, course_id: str) -> list[Assignment]:
    course_dir = root / "courses" / course_id
    if not course_dir.is_dir():
        raise DataRootError(f"course {course_id!r} not found under {root / 'courses'}")
    assignments_dir = course_dir / "assignments"
    if not assignments_dir.is_dir():
        return []
    course_default = _course_default_flavor(course_dir)
    assignments = []
    for entry in sorted(assignments_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        flavor = sidecar_flavor(assignments_dir / f"{entry.name}.toml")
        if flavor is None:
            flavor = course_default
        if flavor is None:
            raise DataRootError(
                f"assignment {course_id}/{entry.name} names no environment: set "
                f"'environment' in {entry.name}.toml or a course default in course.toml"
            )
        assignments.append(
            Assignment(
                course_id=course_id,
                assignment_id=entry.name,
                directory=entry,
                environment_flavor=flavor,
            )
        )
    return assignments


def _course_default_flavor(course_dir: Path) -> str | None:
    course_toml = course_dir / "course.toml"
    if not course_toml.is_file():
        return None
    return load_course(course_toml).environment


def sidecar_flavor(sidecar: Path) -> str | None:
    """The sidecar's environment override; also used by `aat check-course`."""
    if not sidecar.is_file():
        return None
    data = _load_toml(sidecar)
    _reject_unknown_keys(data, _KNOWN_SIDECAR_KEYS, sidecar)
    return _optional_str(data, "environment", sidecar)


def _load_toml(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise DataRootError(f"cannot read {path} as UTF-8: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise DataRootError(f"{path} is not valid TOML: {error}") from error


def _reject_unknown_keys(data: dict[str, object], known: frozenset[str], path: Path) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise DataRootError(f"{path} has unknown keys: {', '.join(unknown)}")


def _optional_str(data: dict[str, object], key: str, path: Path) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise DataRootError(f"{path}: {key!r} must be a non-empty string")
    return value


def assignment_dir(root: Path, course_id: str, assignment_id: str) -> Path:
    """The as-received handout directory; grading tasks present it too."""
    directory = root / "courses" / course_id / "assignments" / assignment_id
    if not directory.is_dir():
        raise DataRootError(
            f"no assignment directory for {course_id}/{assignment_id} under "
            f"{root / 'courses' / course_id / 'assignments'}"
        )
    return directory


def reference_solution_dir(root: Path, course_id: str, assignment_id: str) -> Path:
    directory = root / "courses" / course_id / "reference_solutions" / assignment_id
    if not directory.is_dir():
        raise DataRootError(
            f"no reference solution for {course_id}/{assignment_id} under "
            f"{root / 'courses' / course_id / 'reference_solutions'}"
        )
    return directory


def find_rubric(root: Path, course_id: str, assignment_id: str, name: str) -> Path | None:
    """Resolve a rubric by name; ``None`` when the assignment has no such rubric."""
    rubric = root / "courses" / course_id / "rubrics" / assignment_id / f"{name}.md"
    return rubric if rubric.is_file() else None


def find_rubric_source(root: Path, course_id: str, assignment_id: str) -> Path | None:
    """The professor's standalone rubric document(s), when the course has any.

    ``rubrics/<assignment_id>/source/`` holds them verbatim; most
    assignments have none, so ``None`` is the normal case.
    """
    source = root / "courses" / course_id / "rubrics" / assignment_id / "source"
    return source if source.is_dir() else None


def list_student_submissions(
    root: Path,
    course_id: str,
    assignment_id: str | None = None,
) -> list[StudentSubmission]:
    course_dir = root / "submissions" / course_id
    if not course_dir.is_dir():
        return []
    submissions = []
    for student_dir in sorted(course_dir.iterdir(), key=lambda p: p.name):
        if not student_dir.is_dir():
            continue
        for entry in sorted(student_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            if assignment_id is not None and entry.name != assignment_id:
                continue
            submissions.append(
                StudentSubmission(
                    course_id=course_id,
                    student_id=student_dir.name,
                    assignment_id=entry.name,
                    directory=entry,
                )
            )
    return submissions
