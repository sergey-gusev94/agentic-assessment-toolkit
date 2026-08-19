"""Experiment-config loading, template access, and config identity.

Experiment configs are TOML files (docs/design.md, "Experiment configs
and config identity"): they hold how to run — agent, model, effort,
prompt template, rubric name, agent-argument passthrough — never what to
run on or mechanics. The config identity hashes the config bytes, the
referenced prompt template bytes, and the stage's generic verifier
bytes; per-item identities additionally fold in each item's resolved
inputs (environment template bytes; for grading also rubric bytes and
the assignment directory hash). `aat grade --rubric NAME` replaces a
grading config's rubric name after loading (apply_rubric_override); the
override is then one more identity part, so it never pools with the
config as written.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import tomllib
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Literal

from harbor.models.agent.name import AgentName

from .hashing import sha256_parts

Stage = Literal["solve", "grade"]

ENVIRONMENT_FLAVORS = ("data-science", "grading", "optimization", "scientific-python")
GRADING_FLAVOR = "grading"

# The agents whose environment images the toolkit ships. Each flavor has
# one rendered Dockerfile per agent (docs/design.md, "Environment
# templates"): the agent's CLI is baked in, so the template — and every
# item identity derived from it — differs per agent.
CODEX_AGENT = "codex"
CLAUDE_CODE_AGENT = "claude-code"
# Rendered-filename suffix per agent; Codex has none, so its templates
# and the item identities computed from them are unchanged. This mapping
# is the one place the suffixes are written down: the generator
# (tools/environments/generate.py) imports it to decide what to render,
# so a new agent cannot be half-added.
AGENT_TEMPLATE_SUFFIXES = {CODEX_AGENT: "", CLAUDE_CODE_AGENT: "-claude"}
# The reasoning-effort levels Harbor's Claude Code adapter accepts,
# mirroring the `reasoning_effort` CliFlag in
# harbor.agents.installed.claude_code, which maps them to the Claude
# CLI's --effort flag and rejects anything else. Mirrored so a typo
# fails when the config is loaded instead of once per trial; update it
# when Harbor adds a level. Codex effort is not checked because Harbor's
# Codex flag takes any string.
CLAUDE_CODE_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultracode")
# The final-judge prompt template; paired with the `judge` config key
# (see load_config).
JUDGE_PROMPT_NAME = "judge"

# Per-stage task.toml substitutions: (artifact source path, agent timeout).
# These render into every materialized task and are part of the config
# identity via rendered_task_toml().
STAGE_TASK_SETTINGS: dict[Stage, tuple[str, float]] = {
    "solve": ("/app/submission", 7200.0),
    "grade": ("/app/grading_output", 7200.0),
}

# Keeps importlib.resources-provided paths alive for the process lifetime
# (for archive installs, as_file() otherwise deletes its extraction when
# the context closes).
_RESOURCE_STACK = contextlib.ExitStack()
atexit.register(_RESOURCE_STACK.close)

_KNOWN_KEYS = frozenset(
    {"stage", "agent", "model", "reasoning_effort", "prompt", "rubric", "agent_args", "judge"}
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
    # True for a final-judge grading config: its tasks additionally
    # present prior gradings of the same submission and require the
    # student-facing feedback deliverable (docs/design.md, "Final
    # judge").
    judge: bool = False
    # Set when `aat grade --rubric NAME` replaced the file's rubric name
    # (see apply_rubric_override): the name that then also folds into
    # the config identity. None for a config used as written.
    rubric_override: str | None = None


def apply_rubric_override(config: ExperimentConfig, rubric_name: str) -> ExperimentConfig:
    """The config as run with ``--rubric NAME`` in place of its own rubric.

    The override is the one experiment setting that a flag may change
    (docs/design.md, "Experiment configs and config identity"): rubric
    variants of one assignment are experiment conditions, and one config
    file per variant would multiply files that differ in a single line.
    The returned config keeps the file's bytes — the identity of the
    unmodified config is unchanged, so runs made without the flag stay
    done — and differs in three ways: its rubric name is the override,
    ``rubric_override`` records it, and its name carries a ``+NAME``
    suffix so job directories, run records, and reports say which
    rubric ran without decoding a hash. config_identity() folds the
    override in as one more part, so a config run with the flag never
    pools with the same config run without it.
    """
    if config.stage != "grade":
        raise ConfigError(
            f"config {config.name!r} has stage {config.stage!r}; --rubric applies to "
            "grading configs only"
        )
    if not rubric_name:
        raise ConfigError("--rubric needs a non-empty rubric name")
    return replace(
        config,
        name=f"{config.name}+{rubric_name}",
        rubric_name=rubric_name,
        rubric_override=rubric_name,
    )


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

    # Harbor runs the agent by this exact name, so a name it does not
    # know fails at launch. A near miss is worse than a typo: `claude` or
    # `claude_code` instead of `claude-code` would resolve the Codex
    # environment template and skip Claude's launch-time authentication,
    # running the config on the wrong image against the wrong billing
    # route.
    if agent not in AgentName.values():
        raise ConfigError(
            f"config {path}: agent {agent!r} is not a Harbor agent name (the two the "
            f"toolkit ships environment templates and authentication for are "
            f"{CODEX_AGENT!r} and {CLAUDE_CODE_AGENT!r}; the full list is "
            "harbor.models.agent.name.AgentName)"
        )

    reasoning_effort = data.get("reasoning_effort")
    if reasoning_effort is not None and (
        not isinstance(reasoning_effort, str) or not reasoning_effort
    ):
        raise ConfigError(f"config {path}: 'reasoning_effort' must be a non-empty string")
    if (
        agent == CLAUDE_CODE_AGENT
        and reasoning_effort is not None
        and reasoning_effort not in CLAUDE_CODE_REASONING_EFFORTS
    ):
        raise ConfigError(
            f"config {path}: 'reasoning_effort' {reasoning_effort!r} is not one of "
            f"{', '.join(CLAUDE_CODE_REASONING_EFFORTS)}, the levels Harbor's "
            f"{CLAUDE_CODE_AGENT} adapter accepts"
        )

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

    judge = data.get("judge", False)
    if not isinstance(judge, bool):
        raise ConfigError(f"config {path}: 'judge' must be a boolean")
    if judge and stage != "grade":
        raise ConfigError(f"config {path}: 'judge' is only valid in grading configs")
    # The judge flag and the judge prompt template must travel together:
    # a judge = true config with the grader prompt materializes tasks
    # whose instruction never mentions the prior gradings or the
    # feedback deliverable the verifier requires — every trial fails
    # the contract after full agent cost — and the reverse runs the
    # judge prompt against tasks that present no prior gradings.
    if judge != (prompt_name == JUDGE_PROMPT_NAME):
        detail = (
            f"a judge config must use the {JUDGE_PROMPT_NAME!r} prompt template"
            if judge
            else f"the {JUDGE_PROMPT_NAME!r} prompt template requires judge = true"
        )
        raise ConfigError(f"config {path}: {detail}")

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
        judge=judge,
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


def require_environment_flavor(flavor: str) -> None:
    """Reject a flavor name no template exists for."""
    if flavor not in ENVIRONMENT_FLAVORS:
        raise ConfigError(
            f"unknown environment flavor {flavor!r}; known flavors: "
            + ", ".join(ENVIRONMENT_FLAVORS)
        )


def environment_path(flavor: str, agent: str) -> Path:
    """The environment template one agent's tasks of this flavor build on.

    Every flavor ships one Dockerfile per supported agent, differing only
    in the agent CLI baked in. An agent the toolkit ships no template for
    resolves to the plain ``<flavor>.Dockerfile``: Harbor installs an
    agent it does not find on PATH itself, so that image still works —
    the install just costs a per-trial network step.

    The templates are rendered — one flavor source plus one agent
    fragment per file, from ``tools/environments/`` (see "Environment
    templates" in docs/design.md) — so a missing one means the renders
    were never committed rather than that the flavor is unknown, which is
    why it is reported as its own error.
    """
    require_environment_flavor(flavor)
    suffix = AGENT_TEMPLATE_SUFFIXES.get(agent, "")
    path = _template_root() / "environments" / f"{flavor}{suffix}.Dockerfile"
    if not path.is_file():
        raise ConfigError(
            f"environment template for flavor {flavor!r} and agent {agent!r} is missing "
            f"at {path}; render the templates from their sources with "
            "'make environments' (python tools/environments/generate.py) and commit them"
        )
    return path


def preflight_source_path() -> Path:
    """The canonical PDF-preflight script.

    Every grading template embeds a verbatim copy of it as a heredoc (the
    image builds from an empty context, so nothing can be COPY'd in); a
    repository test asserts the embedded copies match this file.
    """
    return _template_root() / "environments" / "preflight.py"


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
    if config.rubric_override is not None:
        # Appended, never present for a config used as written, so every
        # identity computed before the flag existed is unchanged.
        parts.append(("rubric-override", config.rubric_override.encode("utf-8")))
    return sha256_parts(parts)


def item_identity(
    config_identity: str,
    environment_template_bytes: bytes,
    rubric_bytes: bytes | None = None,
    assignment_hash: str | None = None,
    rubric_source_hash: str | None = None,
    prior_gradings_hash: str | None = None,
) -> str:
    """Per-item identity: the config identity plus the item's resolved inputs.

    Grading items fold in the rubric bytes, the assignment directory
    hash, and — when the assignment has one — the professor rubric
    source directory hash: everything the grader is shown is part of
    the frozen judge (docs/design.md, "Experiment configs and config
    identity"). Final-judge items additionally fold in the hash of the
    prior gradings presented in the task, so a judgment over three
    initial gradings and one over the topped-up five are distinct
    items — never merged by doneness or pooling.
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
    if prior_gradings_hash is not None:
        parts.append(("prior-gradings", prior_gradings_hash.encode("ascii")))
    return sha256_parts(parts)
