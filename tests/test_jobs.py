from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_assessment_toolkit.config import ConfigError, load_config
from agentic_assessment_toolkit.jobs import (
    HARBOR_JOB_CONFIG_FILENAME,
    agent_kwargs,
    build_harbor_job_config,
    write_harbor_job_config,
)
from tests.test_config import SOLVE_TOML, write_config


def test_job_config_shape(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, SOLVE_TOML))
    job_dir = tmp_path / "job"
    job_config = build_harbor_job_config(
        config=config,
        task_dirs=[tmp_path / "tasks" / "t1", tmp_path / "tasks" / "t2"],
        job_dir=job_dir,
        repeats=3,
    )
    assert job_config == {
        "jobs_dir": str(job_dir),
        "job_name": "harbor",
        "n_attempts": 3,
        "n_concurrent_trials": 1,
        "agents": [
            {
                "name": "codex",
                "model_name": "openai/gpt-5.6-sol",
                "kwargs": {"reasoning_effort": "high"},
            }
        ],
        "tasks": [
            {"path": str(tmp_path / "tasks" / "t1")},
            {"path": str(tmp_path / "tasks" / "t2")},
        ],
    }


def test_agent_args_become_kwargs(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, SOLVE_TOML + 'agent_args = ["version=0.146.0"]\n'))
    assert agent_kwargs(config) == {"reasoning_effort": "high", "version": "0.146.0"}


def test_malformed_agent_arg_rejected(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, SOLVE_TOML + 'agent_args = ["no-equals"]\n'))
    with pytest.raises(ConfigError, match="KEY=VALUE"):
        agent_kwargs(config)


def test_write_harbor_job_config(tmp_path: Path) -> None:
    path = write_harbor_job_config(tmp_path, {"n_attempts": 1})
    assert path.name == HARBOR_JOB_CONFIG_FILENAME
    assert json.loads(path.read_text(encoding="utf-8")) == {"n_attempts": 1}
