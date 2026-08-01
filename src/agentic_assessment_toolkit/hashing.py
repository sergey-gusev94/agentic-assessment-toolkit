"""File and directory sha256 hashing for provenance records and identities."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path

_CHUNK_SIZE = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def file_manifest(root: Path) -> dict[str, str]:
    """Map every regular file under ``root`` to its sha256, keyed by POSIX relpath.

    Directory symlinks are followed (with a loop guard) so the manifest
    covers exactly what ``shutil.copytree`` materializes into a task.
    """
    manifest: dict[str, str] = {}
    seen_dirs: set[tuple[int, int]] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        stat = Path(dirpath).stat()
        key = (stat.st_dev, stat.st_ino)
        if key in seen_dirs:
            dirnames.clear()
            continue
        seen_dirs.add(key)
        dirnames.sort()
        directory = Path(dirpath)
        for filename in sorted(filenames):
            path = directory / filename
            if path.is_file():  # follows symlinks; skips dangling ones
                manifest[path.relative_to(root).as_posix()] = sha256_file(path)
    return dict(sorted(manifest.items()))


def sha256_dir(root: Path) -> str:
    """Deterministic digest of a directory: relative names and file contents only."""
    digest = hashlib.sha256(b"aat-dir-sha256-v1\0")
    for relpath, file_digest in sorted(file_manifest(root).items()):
        digest.update(relpath.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def sha256_parts(parts: Iterable[tuple[str, bytes]]) -> str:
    """Digest of labeled byte blobs; length-prefixed so concatenation is unambiguous."""
    digest = hashlib.sha256(b"aat-parts-sha256-v1\0")
    for label, blob in parts:
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(blob).to_bytes(8, "big"))
        digest.update(blob)
    return digest.hexdigest()
