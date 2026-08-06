"""Experiment-config loading, template access, and config identity.

Experiment configs are TOML files (docs/design.md, "Experiment configs
and config identity"): they hold how to run — agent, model, effort,
prompt template, rubric name, agent-argument passthrough — never what to
run on or mechanics. The config identity hashes the config bytes, the
referenced prompt template bytes, and the stage's generic verifier
bytes; per-item identities additionally fold in each item's resolved
inputs (environment template bytes; for grading also rubric bytes and
the assignment directory hash).
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

from .hashing import sha256_parts

Stage = Literal["solve", "grade"]

ENVIRONMENT_FLAVORS = ("data-science", "grading", "optimization", "scientific-python")
GRADING_FLAVOR = "grading"

# Per-stage task.toml substitutions: (artifact source path, agent timeout).
# These render into every materialized task and are part of the config
# identity via rendered_task_toml().
STAGE_TASK_SETTINGS: dict[Stage, tuple[str, float]] = {
    "solve": ("/app/submission", 3600.0),
    "grade": ("/app/grading_output", 3600.0),
}

# Keeps importlib.resources-provided paths alive for the process lifetime
# (for archive installs, as_file() otherwise deletes its extraction when
# the context closes).
_RESOURCE_STACK = contextlib.ExitStack()
atexit.register(_RESOURCE_STACK.close)

_KNOWN_KEYS = frozenset(
    {"stage", "agent", "model", "reasoning_effort", "prompt", "rubric", "agent_args"}
)


class ConfigError(Exception):
    """An experiment config is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class ExperimentConfig:
    """One loaded experiment config plus the exact bytes that identify it."""

    name: str
    path: Path
    raw_bytes: bytes
    stage: Stage
    agent: str
    model: str
    reasoning_effort: str | None
    prompt_name: str
    rubric_name: str | None
    agent_args: tuple[str, ...]


def load_config(path: Path) -> ExperimentConfig:
    try:
        raw_bytes = path.read_bytes()
    except OSError as error:
        raise ConfigError(f"cannot read config {path}: {error}") from error
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"config {path} is not valid TOML: {error}") from error

    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ConfigError(f"config {path} has unknown keys: {', '.join(unknown)}")

    stage = data.get("stage")
    if stage not in ("solve", "grade"):
        raise ConfigError(f'config {path}: \'stage\' must be "solve" or "grade"')

    agent = _required_str(data, "stage-independent", "agent", path)
    model = _required_str(data, "stage-independent", "model", path)
    prompt_name = _required_str(data, "stage-independent", "prompt", path)

    reasoning_effort = data.get("reasoning_effort")
    if reasoning_effort is not None and (
        not isinstance(reasoning_effort, str) or not reasoning_effort
    ):
        raise ConfigError(f"config {path}: 'reasoning_effort' must be a non-empty string")

    rubric_name = data.get("rubric")
    if stage == "solve" and rubric_name is not None:
        raise ConfigError(f"config {path}: 'rubric' is only valid in grading configs")
    if rubric_name is not None and (not isinstance(rubric_name, str) or not rubric_name):
        raise ConfigError(f"config {path}: 'rubric' must be a non-empty string")
    if stage == "grade" and rubric_name is None:
        rubric_name = "default"

    agent_args_value = data.get("agent_args", [])
    if not isinstance(agent_args_value, list) or not all(
        isinstance(item, str) for item in agent_args_value
    ):
        raise ConfigError(f"config {path}: 'agent_args' must be a list of strings")

    prompt_path(prompt_name)  # fail early if the template does not exist

    return ExperimentConfig(
        name=path.stem,
        path=path,
        raw_bytes=raw_bytes,
        stage=stage,
        agent=agent,
        model=model,
        reasoning_effort=reasoning_effort,
        prompt_name=prompt_name,
        rubric_name=rubric_name,
        agent_args=tuple(agent_args_value),
    )


def _required_str(data: dict[str, object], _context: str, key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"config {path}: {key!r} must be a non-empty string")
    return value


@functools.cache
def _template_root() -> Path:
    root = resources.files("agentic_assessment_toolkit") / "templates"
    return _RESOURCE_STACK.enter_context(resources.as_file(root))


def prompt_path(name: str) -> Path:
    path = _template_root() / "prompts" / f"{name}.md"
    if not path.is_file():
        raise ConfigError(f"prompt template {name!r} not found at {path}")
    return path


def environment_path(flavor: str) -> Path:
    if flavor not in ENVIRONMENT_FLAVORS:
        raise ConfigError(
            f"unknown environment flavor {flavor!r}; known flavors: "
            + ", ".join(ENVIRONMENT_FLAVORS)
        )
    return _template_root() / "environments" / f"{flavor}.Dockerfile"


def verifier_path(stage: Stage) -> Path:
    filename = "solve_verifier.py" if stage == "solve" else "grading_verifier.py"
    return _template_root() / "verifiers" / filename


def task_template_path() -> Path:
    return _template_root() / "task" / "task.toml"


@functools.cache
def grading_schema_source_path() -> Path:
    root = resources.files("agentic_assessment_toolkit") / "grading_schema.py"
    return _RESOURCE_STACK.enter_context(resources.as_file(root))


def rendered_task_toml(stage: Stage) -> str:
    """The stage's task.toml as materialized: template plus stage settings."""
    artifact_source, agent_timeout_sec = STAGE_TASK_SETTINGS[stage]
    template = task_template_path().read_text(encoding="utf-8")
    return template.format(
        artifact_source=artifact_source,
        agent_timeout_sec=f"{agent_timeout_sec:.1f}",
    )


def verifier_parts(stage: Stage) -> list[tuple[str, bytes]]:
    """The verifier bytes that enter the config identity for a stage.

    For grading this includes the copied ``grading_schema.py``: the
    schema module executes inside the task, so editing it changes the
    experiment exactly like editing the verifier script.
    """
    parts = [("verifier", verifier_path(stage).read_bytes())]
    if stage == "grade":
        parts.append(("grading-schema", grading_schema_source_path().read_bytes()))
    return parts


def config_identity(config: ExperimentConfig) -> str:
    # The rendered (not raw) task.toml is hashed so that the per-stage
    # substitutions — artifact path, agent timeout — are inside the
    # identity along with the template's network policy and timeouts.
    parts = [
        ("config", config.raw_bytes),
        ("prompt", prompt_path(config.prompt_name).read_bytes()),
        *verifier_parts(config.stage),
        ("task-toml", rendered_task_toml(config.stage).encode("utf-8")),
    ]
    return sha256_parts(parts)


def item_identity(
    config_identity: str,
    environment_template_bytes: bytes,
    rubric_bytes: bytes | None = None,
    assignment_hash: str | None = None,
    rubric_source_hash: str | None = None,
) -> str:
    """Per-item identity: the config identity plus the item's resolved inputs.

    Grading items fold in the rubric bytes, the assignment directory
    hash, and — when the assignment has one — the professor rubric
    source directory hash: everything the grader is shown is part of
    the frozen judge (docs/design.md, "Experiment configs and config
    identity").
    """
    parts = [
        ("config-identity", config_identity.encode("ascii")),
        ("environment", environment_template_bytes),
    ]
    if rubric_bytes is not None:
        parts.append(("rubric", rubric_bytes))
    if assignment_hash is not None:
        parts.append(("assignment", assignment_hash.encode("ascii")))
    if rubric_source_hash is not None:
        parts.append(("rubric-source", rubric_source_hash.encode("ascii")))
    return sha256_parts(parts)
