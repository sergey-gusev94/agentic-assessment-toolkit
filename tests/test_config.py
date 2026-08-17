from __future__ import annotations

from pathlib import Path

import pytest

from agentic_assessment_toolkit import config as config_mod
from agentic_assessment_toolkit.config import (
    ConfigError,
    config_identity,
    item_identity,
    load_config,
)

SOLVE_TOML = """\
stage = "solve"
agent = "codex"
model = "openai/gpt-5.6-sol"
reasoning_effort = "high"
prompt = "solver"
"""

GRADE_TOML = """\
stage = "grade"
agent = "codex"
model = "openai/gpt-5.6-sol"
prompt = "grader"
"""

JUDGE_TOML = """\
stage = "grade"
agent = "codex"
model = "openai/gpt-5.6-sol"
prompt = "judge"
judge = true
"""

CLAUDE_SOLVE_TOML = """\
stage = "solve"
agent = "claude-code"
model = "anthropic/claude-opus-5"
reasoning_effort = "high"
prompt = "solver"
"""

CONFIGS_DIR = Path(__file__).parents[1] / "configs"


def write_config(tmp_path: Path, text: str, name: str = "cfg") -> Path:
    path = tmp_path / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_solve_config(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, SOLVE_TOML, "codex-high"))
    assert config.name == "codex-high"
    assert config.stage == "solve"
    assert config.rubric_name is None
    assert config.agent_args == ()


def test_grade_config_defaults_rubric(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, GRADE_TOML))
    assert config.stage == "grade"
    assert config.rubric_name == "default"


def test_judge_config_loads(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, JUDGE_TOML, "codex-judge"))
    assert config.judge is True
    assert config.prompt_name == "judge"
    # An ordinary grading config defaults to judge = False.
    assert load_config(write_config(tmp_path, GRADE_TOML)).judge is False


COMMITTED_CONFIGS = sorted(CONFIGS_DIR.glob("*.toml"))
# Every committed judge config with the initial grader it mirrors, paired
# by filename so a config added without a test row is still compared.
GRADER_JUDGE_PAIRS = [
    (judge.name.replace("-judge-", "-grader-", 1).removesuffix(".toml"), judge.stem)
    for judge in sorted(CONFIGS_DIR.glob("*-judge-*.toml"))
]


@pytest.mark.parametrize("path", COMMITTED_CONFIGS, ids=lambda path: path.stem)
def test_committed_configs_load(path: Path) -> None:
    """Every config in configs/ loads.

    Without this, a malformed committed config — an agent name the toolkit
    cannot resolve a template for, an effort level Harbor rejects — is
    first noticed by the launch that was supposed to run it.
    """
    assert load_config(path).name == path.stem


def test_committed_configs_are_found() -> None:
    """The two checks above and below must not run over an empty glob."""
    assert len(COMMITTED_CONFIGS) > 1
    graders = {path.stem for path in CONFIGS_DIR.glob("*-grader-*.toml")}
    assert graders
    assert {grader for grader, _ in GRADER_JUDGE_PAIRS} == graders


@pytest.mark.parametrize(("grader_name", "judge_name"), GRADER_JUDGE_PAIRS)
def test_committed_judge_configs_match_initial_graders(grader_name: str, judge_name: str) -> None:
    grader = load_config(CONFIGS_DIR / f"{grader_name}.toml")
    judge = load_config(CONFIGS_DIR / f"{judge_name}.toml")

    assert judge.stage == grader.stage == "grade"
    assert judge.agent == grader.agent
    assert judge.model == grader.model
    assert judge.reasoning_effort == grader.reasoning_effort
    assert judge.rubric_name == grader.rubric_name
    # agent_args is the remaining key that changes how the agent runs, so
    # a judge must inherit it too, or it judges under a different setup
    # than the gradings it reads.
    assert judge.agent_args == grader.agent_args
    assert grader.prompt_name == "grader"
    assert grader.judge is False
    assert judge.prompt_name == "judge"
    assert judge.judge is True


def test_judge_key_must_be_boolean(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'judge' must be a boolean"):
        load_config(write_config(tmp_path, GRADE_TOML + 'judge = "yes"\n'))


def test_judge_key_is_grading_only(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="only valid in grading configs"):
        load_config(write_config(tmp_path, SOLVE_TOML + "judge = true\n"))


def test_judge_key_and_judge_prompt_travel_together(tmp_path: Path) -> None:
    # judge = true with the grader prompt would fail every trial after
    # full agent cost (the verifier requires feedback.md the prompt
    # never mentions); the reverse runs the judge prompt against tasks
    # with no prior gradings.
    with pytest.raises(ConfigError, match="must use the 'judge' prompt template"):
        load_config(write_config(tmp_path, GRADE_TOML + "judge = true\n"))
    with pytest.raises(ConfigError, match="requires judge = true"):
        load_config(
            write_config(tmp_path, GRADE_TOML.replace('prompt = "grader"', 'prompt = "judge"'))
        )


def test_prior_gradings_fold_into_item_identity() -> None:
    base = "c" * 64
    without = item_identity(base, b"env", b"rubric", "a" * 64, None)
    with_prior = item_identity(base, b"env", b"rubric", "a" * 64, None, "p" * 64)
    other_prior = item_identity(base, b"env", b"rubric", "a" * 64, None, "q" * 64)
    assert len({without, with_prior, other_prior}) == 3


def test_unknown_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(write_config(tmp_path, SOLVE_TOML + 'modle = "typo"\n'))


def test_missing_stage_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="stage"):
        load_config(write_config(tmp_path, 'agent = "codex"\nmodel = "m"\nprompt = "solver"\n'))


def test_rubric_in_solve_config_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="only valid in grading configs"):
        load_config(write_config(tmp_path, SOLVE_TOML + 'rubric = "default"\n'))


def test_unknown_prompt_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="prompt template"):
        load_config(write_config(tmp_path, SOLVE_TOML.replace("solver", "nonexistent")))


def test_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(tmp_path / "absent.toml")


def test_agent_args_must_be_strings(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="agent_args"):
        load_config(write_config(tmp_path, SOLVE_TOML + "agent_args = [1]\n"))


@pytest.mark.parametrize("agent", ["claude", "claude_code", "Codex", "gpt-5"])
def test_agent_must_be_a_harbor_agent_name(tmp_path: Path, agent: str) -> None:
    """A near-miss agent name is worse than a typo, so it is caught here.

    `claude` (the CLI's own name) would load, resolve the *Codex*
    environment template, and skip Claude's launch-time authentication —
    running on the wrong image against whichever credential Harbor
    happened to prefer.
    """
    text = CLAUDE_SOLVE_TOML.replace('agent = "claude-code"', f'agent = "{agent}"')
    with pytest.raises(ConfigError, match=f"agent '{agent}' is not a Harbor agent name"):
        load_config(write_config(tmp_path, text))


def test_claude_reasoning_effort_must_be_a_level_harbor_accepts(tmp_path: Path) -> None:
    """Harbor's Claude adapter rejects an unknown level once per trial.

    By then the job directory exists and the base image is built, so
    every trial of the run fails on a typo; loading the config is where
    that costs nothing.
    """
    text = CLAUDE_SOLVE_TOML.replace('reasoning_effort = "high"', 'reasoning_effort = "maximum"')
    with pytest.raises(ConfigError, match="'reasoning_effort' 'maximum' is not one of"):
        load_config(write_config(tmp_path, text))
    for level in config_mod.CLAUDE_CODE_REASONING_EFFORTS:
        accepted = CLAUDE_SOLVE_TOML.replace('"high"', f'"{level}"')
        assert load_config(write_config(tmp_path, accepted)).reasoning_effort == level
    # Harbor's Codex effort flag takes any string, so Codex is not checked.
    codex = SOLVE_TOML.replace('reasoning_effort = "high"', 'reasoning_effort = "maximum"')
    assert load_config(write_config(tmp_path, codex)).reasoning_effort == "maximum"


def test_config_identity_tracks_config_bytes(tmp_path: Path) -> None:
    first = load_config(write_config(tmp_path, SOLVE_TOML, "a"))
    same = load_config(write_config(tmp_path, SOLVE_TOML, "b"))
    changed = load_config(write_config(tmp_path, SOLVE_TOML.replace('"high"', '"medium"'), "c"))
    assert config_identity(first) == config_identity(same)  # name is not identity
    assert config_identity(first) != config_identity(changed)


def test_config_identity_differs_between_stages(tmp_path: Path) -> None:
    solve = load_config(write_config(tmp_path, SOLVE_TOML, "s"))
    grade = load_config(write_config(tmp_path, GRADE_TOML, "g"))
    assert config_identity(solve) != config_identity(grade)


def test_item_identity_folds_environment_rubric_and_assignment() -> None:
    base = "0" * 64
    env_a = item_identity(base, b"FROM python:3.12-slim")
    env_b = item_identity(base, b"FROM python:3.13-slim")
    assert env_a != env_b
    with_rubric = item_identity(base, b"FROM x", b"# rubric")
    without_rubric = item_identity(base, b"FROM x")
    assert with_rubric != without_rubric
    assert item_identity(base, b"FROM x", b"# rubric") == with_rubric
    with_assignment = item_identity(base, b"FROM x", b"# rubric", "a" * 64)
    assert with_assignment != with_rubric
    assert item_identity(base, b"FROM x", b"# rubric", "b" * 64) != with_assignment
    with_source = item_identity(base, b"FROM x", b"# rubric", "a" * 64, "c" * 64)
    assert with_source != with_assignment
    assert item_identity(base, b"FROM x", b"# rubric", "a" * 64, "d" * 64) != with_source


# The agents with a shipped template, from the mapping that resolves them.
AGENTS = tuple(config_mod.AGENT_TEMPLATE_SUFFIXES)


def test_environment_flavors_resolve() -> None:
    for flavor in config_mod.ENVIRONMENT_FLAVORS:
        for agent in AGENTS:
            assert config_mod.environment_path(flavor, agent).is_file()
    with pytest.raises(ConfigError, match="unknown environment flavor"):
        config_mod.environment_path("latex", config_mod.CODEX_AGENT)
    with pytest.raises(ConfigError, match="unknown environment flavor"):
        config_mod.require_environment_flavor("latex")


def test_environment_template_is_per_agent() -> None:
    """Each agent's template differs, and an unmanaged agent gets the plain one."""
    for flavor in config_mod.ENVIRONMENT_FLAVORS:
        codex = config_mod.environment_path(flavor, config_mod.CODEX_AGENT)
        claude = config_mod.environment_path(flavor, config_mod.CLAUDE_CODE_AGENT)
        assert codex.name == f"{flavor}.Dockerfile"
        assert claude.name == f"{flavor}-claude.Dockerfile"
        assert codex.read_bytes() != claude.read_bytes()
        # Harbor installs an agent it does not find on PATH itself, so an
        # agent with no shipped template resolves to the plain Dockerfile.
        assert config_mod.environment_path(flavor, "gemini-cli") == codex


def test_missing_environment_template_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known flavor whose render is absent must say how to produce it.

    The renders are generated files; forgetting to commit one would
    otherwise surface as a bare FileNotFoundError from whichever caller
    read the bytes first.
    """
    (tmp_path / "environments").mkdir()
    monkeypatch.setattr(config_mod, "_template_root", lambda: tmp_path)
    with pytest.raises(ConfigError, match="make environments"):
        config_mod.environment_path(config_mod.GRADING_FLAVOR, config_mod.CLAUDE_CODE_AGENT)


def test_environment_flavors_include_basic_inspection_tools() -> None:
    for flavor in config_mod.ENVIRONMENT_FLAVORS:
        for agent in AGENTS:
            dockerfile = config_mod.environment_path(flavor, agent).read_text(encoding="utf-8")
            assert "\n        file \\" in dockerfile
            assert "\n        jq \\" in dockerfile


def test_template_paths_exist() -> None:
    assert config_mod.prompt_path("solver").is_file()
    assert config_mod.prompt_path("grader").is_file()
    assert config_mod.verifier_path("solve").is_file()
    assert config_mod.verifier_path("grade").is_file()
    assert config_mod.task_template_path().is_file()
    assert config_mod.grading_schema_source_path().is_file()
