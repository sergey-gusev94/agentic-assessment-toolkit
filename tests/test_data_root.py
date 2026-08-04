from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentic_assessment_toolkit.data_root import (
    DEFAULT_DIRNAME,
    ENV_VAR,
    RUBRIC_ARCHIVE_DIRNAME,
    TOP_LEVEL_DIRS,
    DataRootError,
    find_rubric,
    init_data_root,
    list_assignments,
    list_courses,
    list_student_submissions,
    reference_solution_dir,
    resolve_data_root,
    rubric_versions,
)
from agentic_assessment_toolkit.hashing import sha256_file
from tests.conftest import COURSE_ID


def test_explicit_path_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "does-not-exist"))
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    assert resolve_data_root(explicit) == explicit.resolve()


def test_env_var_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv(ENV_VAR, str(root))
    assert resolve_data_root() == root.resolve()


def test_default_path_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / DEFAULT_DIRNAME
    root.mkdir()
    assert resolve_data_root() == root.resolve()


def test_missing_default_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / DEFAULT_DIRNAME
    with pytest.raises(DataRootError, match=rf"default data root {expected} does not exist"):
        resolve_data_root()


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DataRootError, match="does not exist"):
        resolve_data_root(tmp_path / "missing")


def make_fake_toolkit_repo(base: Path) -> Path:
    repo = base / "toolkit-clone"
    (repo / ".git").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "agentic-assessment-toolkit"\n', encoding="utf-8"
    )
    return repo


def test_data_root_inside_toolkit_clone_is_refused(tmp_path: Path) -> None:
    repo = make_fake_toolkit_repo(tmp_path)
    inside = repo / "data"
    inside.mkdir()
    with pytest.raises(DataRootError, match="inside the toolkit repository"):
        resolve_data_root(inside)


def test_data_root_inside_toolkit_cwd_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_fake_toolkit_repo(tmp_path)
    inside = repo / "data"
    inside.mkdir()
    monkeypatch.chdir(repo)
    with pytest.raises(DataRootError, match="inside the toolkit repository"):
        resolve_data_root(Path("data"))


def test_data_root_inside_other_git_repo_is_allowed(tmp_path: Path) -> None:
    repo = tmp_path / "private-data"
    (repo / ".git").mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "something-else"\n', encoding="utf-8")
    root = repo / "root"
    root.mkdir()
    assert resolve_data_root(root) == root.resolve()


def test_list_courses_and_assignments(data_root: Path) -> None:
    assert list_courses(data_root) == [COURSE_ID]
    assignments = list_assignments(data_root, COURSE_ID)
    assert [a.assignment_id for a in assignments] == ["HW1", "HW2"]
    by_id = {a.assignment_id: a for a in assignments}
    assert by_id["HW1"].environment_flavor == "scientific-python"  # course default
    assert by_id["HW2"].environment_flavor == "data-science"  # sidecar override
    assert by_id["HW1"].item_id == f"{COURSE_ID}/HW1"


def test_unknown_course_is_an_error(data_root: Path) -> None:
    with pytest.raises(DataRootError, match="not found"):
        list_assignments(data_root, "NOPE")


def test_missing_environment_is_an_error(data_root: Path) -> None:
    (data_root / "courses" / COURSE_ID / "course.toml").unlink()
    with pytest.raises(DataRootError, match="names no environment"):
        list_assignments(data_root, COURSE_ID)


def test_unknown_sidecar_key_is_an_error(data_root: Path) -> None:
    sidecar = data_root / "courses" / COURSE_ID / "assignments" / "HW2.toml"
    sidecar.write_text('envronment = "typo"\n', encoding="utf-8")
    with pytest.raises(DataRootError, match="unknown keys"):
        list_assignments(data_root, COURSE_ID)


def test_reference_solution_and_rubric_resolution(data_root: Path) -> None:
    assert reference_solution_dir(data_root, COURSE_ID, "HW1").is_dir()
    with pytest.raises(DataRootError, match="no reference solution"):
        reference_solution_dir(data_root, COURSE_ID, "HW9")
    rubric = find_rubric(data_root, COURSE_ID, "HW1", "default")
    assert rubric is not None and rubric.name == "default.md"
    assert find_rubric(data_root, COURSE_ID, "HW2", "default") is None
    assert find_rubric(data_root, COURSE_ID, "HW1", "strict-v2") is None


def test_rubric_versions_cover_the_selectable_and_archived_bytes(data_root: Path) -> None:
    """A superseded version resolves by hash after `default` advances."""
    rubric = data_root / "courses" / COURSE_ID / "rubrics" / "HW1" / "default.md"
    old_sha = sha256_file(rubric)
    archive = rubric.parent / RUBRIC_ARCHIVE_DIRNAME
    archive.mkdir()
    (archive / f"{old_sha[:8]}.md").write_bytes(rubric.read_bytes())
    rubric.write_text("# HW1\n\n- `a` (10 points): corrected split.\n", encoding="utf-8")
    new_sha = sha256_file(rubric)

    versions = rubric_versions(data_root, COURSE_ID, "HW1")
    assert versions[new_sha] == rubric
    assert versions[old_sha] == archive / f"{old_sha[:8]}.md"
    # An assignment with no rubric directory has no versions, not an error.
    assert rubric_versions(data_root, COURSE_ID, "HW2") == {}


def test_list_student_submissions(data_root: Path) -> None:
    submissions = list_student_submissions(data_root, COURSE_ID)
    assert [s.item_id for s in submissions] == [f"{COURSE_ID}/stu1/HW1"]
    assert list_student_submissions(data_root, COURSE_ID, "HW2") == []
    assert list_student_submissions(data_root, "NOPE") == []


def test_init_creates_layout(tmp_path: Path) -> None:
    target = tmp_path / "aat-data"
    root, created = init_data_root(target)
    assert root == target.resolve()
    assert set(created) == {f"{name}/" for name in TOP_LEVEL_DIRS} | {"README.md"}
    for name in TOP_LEVEL_DIRS:
        assert (root / name).is_dir()
    assert "never publish" in (root / "README.md").read_text(encoding="utf-8")
    assert not (root / ".gitignore").exists()
    assert not (root / ".git").exists()
    assert resolve_data_root(target) == root


def test_init_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root, created = init_data_root()
    assert root == (tmp_path / DEFAULT_DIRNAME).resolve()
    assert created


def test_init_is_idempotent_and_fills_gaps(tmp_path: Path) -> None:
    root, _ = init_data_root(tmp_path / "aat-data")
    marker = root / "courses" / "SYN_C9"
    marker.mkdir()
    shutil.rmtree(root / "scratch")
    again, created = init_data_root(tmp_path / "aat-data")
    assert again == root
    assert created == ["scratch/"]
    assert marker.is_dir()
    assert init_data_root(tmp_path / "aat-data") == (root, [])


def test_init_inside_toolkit_clone_is_refused(tmp_path: Path) -> None:
    repo = make_fake_toolkit_repo(tmp_path)
    with pytest.raises(DataRootError, match="inside the toolkit repository"):
        init_data_root(repo / "data")
    assert not (repo / "data").exists()


def test_init_refuses_file_targets(tmp_path: Path) -> None:
    target = tmp_path / "aat-data"
    target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(DataRootError, match="exists and is not a directory"):
        init_data_root(target)
    target.unlink()
    target.mkdir()
    (target / "raw").write_text("not a directory", encoding="utf-8")
    with pytest.raises(DataRootError, match="raw.*exists and is not a directory"):
        init_data_root(target)


@pytest.mark.skipif(shutil.which("git") is None, reason="no git executable on PATH")
def test_init_git_creates_repository_and_gitignore(tmp_path: Path) -> None:
    root, created = init_data_root(tmp_path / "aat-data", git=True)
    assert (root / ".git").is_dir()
    assert created[-2:] == [".gitignore", ".git/"]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    for line in ("/tasks/", "/analysis/", "/scratch/"):
        assert line in ignored
    assert init_data_root(tmp_path / "aat-data", git=True) == (root, [])


def test_init_git_without_git_executable_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(DataRootError, match="git.*PATH"):
        init_data_root(tmp_path / "aat-data", git=True)


def test_empty_explicit_path_falls_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    default_root = tmp_path / DEFAULT_DIRNAME
    default_root.mkdir()
    assert resolve_data_root("") == default_root.resolve()
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv(ENV_VAR, str(root))
    assert resolve_data_root("  ") == root.resolve()
