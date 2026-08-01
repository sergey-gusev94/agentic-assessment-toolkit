"""Pinned Harbor job-configuration emission.

The toolkit drives ``harbor run`` through a generated job-config file
rather than a long flag list: the config file is the one Harbor
interface that can express an explicit task list (and, later, container
startup env such as Gurobi WLS credentials), and it is recorded
verbatim beside the job output.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ConfigError, ExperimentConfig

HARBOR_JOB_NAME = "harbor"
HARBOR_JOB_CONFIG_FILENAME = "harbor-job.json"

# Conservative default: subscription capacity is the scarce resource
# (docs/research.md, "Development runs").
N_CONCURRENT_TRIALS = 1


def agent_kwargs(config: ExperimentConfig) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    if config.reasoning_effort is not None:
        kwargs["reasoning_effort"] = config.reasoning_effort
    for entry in config.agent_args:
        key, sep, value = entry.partition("=")
        if not sep or not key:
            raise ConfigError(f"config {config.path}: agent_args entry {entry!r} is not KEY=VALUE")
        kwargs[key] = value
    return kwargs


def build_harbor_job_config(
    *,
    config: ExperimentConfig,
    task_dirs: list[Path],
    job_dir: Path,
    repeats: int,
) -> dict[str, object]:
    """A Harbor JobConfig document (harbor.models.job.config:JobConfig)."""
    agent: dict[str, object] = {"name": config.agent, "model_name": config.model}
    kwargs = agent_kwargs(config)
    if kwargs:
        agent["kwargs"] = kwargs
    return {
        "jobs_dir": str(job_dir),
        "job_name": HARBOR_JOB_NAME,
        "n_attempts": repeats,
        "n_concurrent_trials": N_CONCURRENT_TRIALS,
        "agents": [agent],
        "tasks": [{"path": str(task_dir)} for task_dir in task_dirs],
    }


def write_harbor_job_config(job_dir: Path, job_config: dict[str, object]) -> Path:
    path = job_dir / HARBOR_JOB_CONFIG_FILENAME
    path.write_text(json.dumps(job_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
