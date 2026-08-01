"""`aat intake`: run the intake agent over unprocessed course dumps.

Course intake (docs/course-intake.md, design decision 14) converts
`raw/<course_id>/` into `courses/<course_id>/` by running the Codex CLI
once per course with the rendered `templates/prompts/intake.md` brief —
the same thin pattern as the Harbor commands: construct one command
line, run it as a subprocess, record what ran.

Doneness is derived from the data root: a course is processed when its
`courses/<course_id>/intake-record.json` receipt exists and records the
current hash of the raw dump. New material in `raw/` changes the hash,
so the course becomes unprocessed again and the next run performs an
incremental pass. The prompt template and model are recorded in the
receipt as provenance but deliberately excluded from doneness: intake
output is human-reviewed, so a prompt or model change never invalidates
an already-processed course.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, config
from .hashing import sha256_dir, sha256_file

DEFAULT_MODEL = "gpt-5.6-sol"  # the pipeline configs' model, in `codex -m` form
DEFAULT_REASONING_EFFORT = "high"
INTAKE_PROMPT_NAME = "intake"
RECORD_FILENAME = "intake-record.json"
RECORD_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IntakeCourse:
    """One raw dump and what intake knows about its processing state."""

    course_id: str
    raw_dir: Path
    # "pending": needs a run (no receipt, or the raw dump changed).
    # "done": receipt matches the current raw dump.
    # "manual": courses/<id> exists without a receipt — built by hand,
    #   skipped unless --force.
    status: str


def render_prompt(course_id: str) -> str:
    template = config.prompt_path(INTAKE_PROMPT_NAME).read_text(encoding="utf-8")
    # str.replace, not str.format: the template's TOML/Markdown examples
    # may contain braces that format() would choke on.
    return template.replace("{course_id}", course_id)


def build_command(prompt: str, model: str, reasoning_effort: str) -> list[str]:
    """The `codex exec` invocation for one course.

    workspace-write scopes the agent to the working directory — the
    data root — which is exactly the permission intake needs.
    """
    return [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        prompt,
    ]


def codex_path() -> str | None:
    """Where the codex binary is, or None when it is not installed."""
    return shutil.which("codex")


def list_raw_courses(root: Path, only: str | None = None) -> list[IntakeCourse]:
    """Raw dumps with their processing status; ``only`` narrows to one
    course before any hashing, so selecting one course never hashes the
    other dumps."""
    raw_root = root / "raw"
    if not raw_root.is_dir():
        return []
    courses = []
    for entry in sorted(raw_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or (only is not None and entry.name != only):
            continue
        courses.append(
            IntakeCourse(course_id=entry.name, raw_dir=entry, status=_status(root, entry))
        )
    return courses


def _status(root: Path, raw_dir: Path) -> str:
    course_dir = root / "courses" / raw_dir.name
    if not record_path(root, raw_dir.name).is_file():
        return "manual" if course_dir.is_dir() else "pending"
    record = read_record(root, raw_dir.name)
    if record is None:
        # A receipt that exists but does not parse (e.g. a crash during
        # its write) is treated as unprocessed: the next run performs an
        # incremental pass and rewrites it.
        return "pending"
    return "done" if record.get("raw_sha256") == sha256_dir(raw_dir) else "pending"


def record_path(root: Path, course_id: str) -> Path:
    return root / "courses" / course_id / RECORD_FILENAME


def read_record(root: Path, course_id: str) -> dict[str, object] | None:
    path = record_path(root, course_id)
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def write_record(
    root: Path,
    course_id: str,
    *,
    command: list[str],
    model: str,
    reasoning_effort: str,
    log_path: Path | None,
) -> Path:
    """The receipt `aat intake` writes after a successful agent run.

    ``raw_sha256`` is the doneness key; everything else is provenance.
    The prompt text itself is elided from the recorded command — the
    prompt hash identifies it without duplicating pages of text.
    """
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "created_utc": datetime.now(UTC).isoformat(),
        "toolkit_version": __version__,
        "course_id": course_id,
        "raw_sha256": sha256_dir(root / "raw" / course_id),
        "prompt_sha256": sha256_file(config.prompt_path(INTAKE_PROMPT_NAME)),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "command": [*command[:-1], "<rendered intake prompt>"],
        "log": str(log_path) if log_path is not None else None,
    }
    path = record_path(root, course_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def log_path_for(root: Path, course_id: str, now: datetime | None = None) -> Path:
    """A log path that never reuses an existing file, so an immediate
    same-second retry cannot overwrite the failure evidence."""
    moment = now if now is not None else datetime.now(UTC)
    base = f"{moment.strftime('%Y%m%dT%H%M%SZ')}__{course_id}"
    logs_dir = root / "scratch" / "intake"
    candidate = logs_dir / f"{base}.log"
    counter = 1
    while candidate.exists():
        candidate = logs_dir / f"{base}-{counter}.log"
        counter += 1
    return candidate


def execute(command: list[str], cwd: Path, log_path: Path) -> int:
    """Run the agent command from ``cwd``, teeing output to the log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        return process.wait()
