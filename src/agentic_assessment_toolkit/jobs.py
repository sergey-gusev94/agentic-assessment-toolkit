"""Pinned Harbor job-configuration emission.

The toolkit drives ``harbor run`` through a generated job-config file
rather than a long flag list: the config file is the one Harbor
interface that can express an explicit task list and a run-time Gurobi
license mount, and it is recorded verbatim beside the job output.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ConfigError, ExperimentConfig

HARBOR_JOB_CONFIG_FILENAME = "harbor-job.json"

DEFAULT_MAX_CONCURRENT_TRIALS = 8

GUROBI_LICENSE_CONTAINER_PATH = "/opt/gurobi/gurobi.lic"


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
    max_concurrent_trials: int,
    gurobi_license_file: Path | None = None,
) -> dict[str, object]:
    """A Harbor JobConfig document (harbor.models.job.config:JobConfig).

    The AAT job directory is itself the Harbor job directory
    (docs/data-conventions.md): the stage parent is Harbor's jobs
    directory and the AAT directory name is the Harbor job name, so
    Harbor's files land beside aat-run.json and this config with no
    nesting, and `harbor view` works on the shared parent.
    """
    agent: dict[str, object] = {"name": config.agent, "model_name": config.model}
    kwargs = agent_kwargs(config)
    if kwargs:
        agent["kwargs"] = kwargs
    job_config: dict[str, object] = {
        "jobs_dir": str(job_dir.parent),
        "job_name": job_dir.name,
        "n_attempts": repeats,
        "n_concurrent_trials": max_concurrent_trials,
        "agents": [agent],
        "tasks": [{"path": str(task_dir)} for task_dir in task_dirs],
    }
    if gurobi_license_file is not None:
        job_config["environment"] = {
            "mounts": [
                {
                    "type": "bind",
                    "source": str(gurobi_license_file),
                    "target": GUROBI_LICENSE_CONTAINER_PATH,
                    "read_only": True,
                    "bind": {"create_host_path": False},
                }
            ]
        }
    return job_config


def write_harbor_job_config(job_dir: Path, job_config: dict[str, object]) -> Path:
    path = job_dir / HARBOR_JOB_CONFIG_FILENAME
    path.write_text(json.dumps(job_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
