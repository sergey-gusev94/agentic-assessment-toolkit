#!/usr/bin/env python3
"""Generic solve-stage output-contract verifier (0/1 reward).

Standalone and stdlib-only by design: this script is copied into every
materialized solve task and runs where the toolkit package is not
installed. The contract (docs/design.md, "Solve task layout and
verifier"): /app/submission exists, is non-empty, contains at least one
file that is new or changed relative to the materialized inputs, and
contains no bookkeeping entries.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

# The environment overrides exist so repository tests can exercise this
# script as a real subprocess; inside a task container the defaults hold.
SUBMISSION_DIR = Path(os.environ.get("AAT_SUBMISSION_DIR", "/app/submission"))
REWARD_PATH = Path(os.environ.get("AAT_REWARD_PATH", "/logs/verifier/reward.json"))
MANIFEST_PATH = Path(__file__).resolve().parent / "input_manifest.json"

# The fixed bookkeeping blocklist. This list is the documentation of the
# exact rule; the design doc names the categories.
BLOCKED_NAMES = {".git", "__pycache__", ".ipynb_checkpoints", ".DS_Store", "node_modules"}
BLOCKED_SUFFIXES = (".swp", ".swo")


def is_bookkeeping(name):
    if name in BLOCKED_NAMES:
        return True
    if name.endswith(BLOCKED_SUFFIXES) or name.endswith("~"):
        return True
    return len(name) > 2 and name.startswith("#") and name.endswith("#")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def check_contract():
    failures = []
    if not SUBMISSION_DIR.is_dir():
        return [f"{SUBMISSION_DIR} does not exist or is not a directory"]

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    files = []
    bookkeeping = set()
    for path in sorted(SUBMISSION_DIR.rglob("*")):
        relative = path.relative_to(SUBMISSION_DIR)
        blocked = [part for part in relative.parts if is_bookkeeping(part)]
        if blocked:
            bookkeeping.add(relative.as_posix())
            continue
        if path.is_file():
            files.append(relative.as_posix())

    if bookkeeping:
        failures.append(
            "submission contains bookkeeping entries: " + ", ".join(sorted(bookkeeping))
        )
    if not files:
        failures.append("submission contains no files")
        return failures

    changed = [
        relpath
        for relpath in files
        if manifest.get(relpath) != sha256_file(SUBMISSION_DIR / relpath)
    ]
    if not changed:
        failures.append("every submission file is an unchanged copy of the materialized inputs")
    return failures


def main():
    failures = check_contract()
    reward = 0.0 if failures else 1.0
    emit(reward, {"contract": "solve-output-v1", "failures": failures})
    return 0


def emit(reward, details):
    # Harbor reads the reward from /logs/verifier/reward.json: a flat JSON
    # object of numbers. Details go to stdout, which Harbor captures as
    # the verifier's test-stdout.txt.
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(json.dumps({"reward": reward}), encoding="utf-8")
    print(json.dumps({"reward": reward, "details": details}, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
