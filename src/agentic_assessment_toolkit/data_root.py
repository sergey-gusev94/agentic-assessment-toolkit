"""Data-root resolution, refusal rules, and data-root layout accessors.

The conventions implemented here are specified in docs/data-conventions.md:
all real course and student data lives in a single directory outside the
toolkit's own repository, resolved explicitly or through ``AAT_DATA_DIR``,
never defaulted.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "AAT_DATA_DIR"

_TOOLKIT_NAME = "agentic-assessment-toolkit"

_KNOWN_COURSE_KEYS = frozenset({"environment"})
_KNOWN_SIDECAR_KEYS = frozenset({"environment"})


class DataRootError(Exception):
    """The data root cannot be resolved, is refused, or has invalid contents."""


def resolve_data_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the data root: explicit path, then ``AAT_DATA_DIR``, then a clear error."""
    if explicit is not None and str(explicit).strip():
        candidate = Path(explicit)
    else:
        env_value = os.environ.get(ENV_VAR, "").strip()
        if not env_value:
            raise DataRootError(
                "no data root: pass an explicit path (--data-root) or set "
                f"{ENV_VAR}; there is no default (docs/data-conventions.md)"
            )
        candidate = Path(env_value)
    root = candidate.expanduser().resolve()
    if not root.is_dir():
        raise DataRootError(f"data root {root} does not exist or is not a directory")
    _refuse_toolkit_tree(root)
    return root


def _refuse_toolkit_tree(root: Path) -> None:
    """Refuse a data root inside any git working tree of this toolkit.

    Checked trees: the installed package location (catches editable
    installs), the current working directory, and the data root itself
    (catches pointing at another checkout of the toolkit).
    """
    anchors = (Path(__file__).resolve(), Path.cwd().resolve(), root)
    for anchor in anchors:
        git_root = _find_git_root(anchor)
        if git_root is None:
            continue
        if _declares_toolkit(git_root / "pyproject.toml") and root.is_relative_to(git_root):
            raise DataRootError(
                f"data root {root} lies inside the toolkit repository at {git_root}; "
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
    """One immutable assignment resolved from the data root."""

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
        flavor = _sidecar_flavor(assignments_dir / f"{entry.name}.toml")
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
    data = _load_toml(course_toml)
    _reject_unknown_keys(data, _KNOWN_COURSE_KEYS, course_toml)
    return _optional_str(data, "environment", course_toml)


def _sidecar_flavor(sidecar: Path) -> str | None:
    if not sidecar.is_file():
        return None
    data = _load_toml(sidecar)
    _reject_unknown_keys(data, _KNOWN_SIDECAR_KEYS, sidecar)
    return _optional_str(data, "environment", sidecar)


def _load_toml(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
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
