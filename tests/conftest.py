from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
GOLDEN_DIR = FIXTURES_DIR / "golden"
COURSE_ID = "SYN_C1"


def build_data_root(base: Path) -> Path:
    """A synthetic data root: one course, one student submission."""
    root = base / "data-root"
    shutil.copytree(FIXTURES_DIR / "course" / COURSE_ID, root / "courses" / COURSE_ID)
    shutil.copytree(
        FIXTURES_DIR / "submission",
        root / "submissions" / COURSE_ID / "stu1" / "HW1",
    )
    return root


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return build_data_root(tmp_path)


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Map of posix relpath -> content for every file under root."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def assert_trees_equal(actual: Path, expected: Path) -> None:
    actual_tree = tree_bytes(actual)
    expected_tree = tree_bytes(expected)
    assert sorted(actual_tree) == sorted(expected_tree)
    for relpath, expected_bytes in expected_tree.items():
        assert actual_tree[relpath] == expected_bytes, f"content differs: {relpath}"
