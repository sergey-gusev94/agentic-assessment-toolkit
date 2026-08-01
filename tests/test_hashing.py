from __future__ import annotations

from pathlib import Path

from agentic_assessment_toolkit.hashing import (
    file_manifest,
    sha256_bytes,
    sha256_dir,
    sha256_file,
    sha256_parts,
)


def make_tree(base: Path) -> Path:
    root = base / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    return root


def test_file_manifest_uses_posix_relpaths(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    manifest = file_manifest(root)
    assert sorted(manifest) == ["a.txt", "sub/b.txt"]
    assert manifest["a.txt"] == sha256_file(root / "a.txt")


def test_sha256_dir_is_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    first = sha256_dir(root)
    assert first == sha256_dir(root)

    (root / "a.txt").write_text("changed", encoding="utf-8")
    assert sha256_dir(root) != first


def test_sha256_dir_is_name_sensitive(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    before = sha256_dir(root)
    (root / "a.txt").rename(root / "renamed.txt")
    assert sha256_dir(root) != before


def test_sha256_parts_separates_labels_and_boundaries() -> None:
    assert sha256_parts([("a", b"bc")]) != sha256_parts([("ab", b"c")])
    assert sha256_parts([("x", b"1"), ("y", b"2")]) != sha256_parts([("x", b"12"), ("y", b"")])
    assert sha256_parts([("x", b"1")]) == sha256_parts([("x", b"1")])


def test_sha256_bytes_matches_known_prefix() -> None:
    assert sha256_bytes(b"").startswith("e3b0c44298fc1c14")


def test_file_manifest_follows_directory_symlinks(tmp_path: Path) -> None:
    """copytree materializes symlinked dirs as real content; hashing must too."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "inner.txt").write_text("inner", encoding="utf-8")
    root = make_tree(tmp_path)
    (root / "linked").symlink_to(real, target_is_directory=True)
    manifest = file_manifest(root)
    assert "linked/inner.txt" in manifest
    # A symlink loop terminates rather than recursing forever.
    (root / "loop").symlink_to(root, target_is_directory=True)
    assert file_manifest(root)
