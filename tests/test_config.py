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


def test_item_identity_folds_environment_and_rubric() -> None:
    base = "0" * 64
    env_a = item_identity(base, b"FROM python:3.12-slim")
    env_b = item_identity(base, b"FROM python:3.13-slim")
    assert env_a != env_b
    with_rubric = item_identity(base, b"FROM x", b"# rubric")
    without_rubric = item_identity(base, b"FROM x")
    assert with_rubric != without_rubric
    assert item_identity(base, b"FROM x", b"# rubric") == with_rubric


def test_environment_flavors_resolve() -> None:
    for flavor in config_mod.ENVIRONMENT_FLAVORS:
        assert config_mod.environment_path(flavor).is_file()
    with pytest.raises(ConfigError, match="unknown environment flavor"):
        config_mod.environment_path("latex")


def test_template_paths_exist() -> None:
    assert config_mod.prompt_path("solver").is_file()
    assert config_mod.prompt_path("grader").is_file()
    assert config_mod.verifier_path("solve").is_file()
    assert config_mod.verifier_path("grade").is_file()
    assert config_mod.task_skeleton_path().is_file()
    assert config_mod.grading_schema_source_path().is_file()
