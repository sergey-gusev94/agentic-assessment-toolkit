"""Base-image naming and the launch-time ensure/build/retry loop.

Docker is never run: subprocess.run and time.sleep are stubbed, per the
no-live-execution rule (AGENTS.md).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from agentic_assessment_toolkit import base_images
from agentic_assessment_toolkit.config import (
    CLAUDE_CODE_AGENT,
    CODEX_AGENT,
    ConfigError,
    environment_path,
)


def test_reference_is_deterministic_and_content_pinned() -> None:
    reference = base_images.base_image_reference("grading", CODEX_AGENT)
    assert reference == base_images.base_image_reference("grading", CODEX_AGENT)
    repository, tag = reference.split(":")
    assert repository == "aat-env-grading"
    assert len(tag) == 16
    # The tag pins the template content, so flavors never collide.
    assert reference != base_images.base_image_reference("scientific-python", CODEX_AGENT)


def test_agents_get_distinct_images_of_one_flavor() -> None:
    """The content-hash tag separates the two agents' images by itself."""
    codex = base_images.base_image_reference("grading", CODEX_AGENT)
    claude = base_images.base_image_reference("grading", CLAUDE_CODE_AGENT)
    # Same flavor name, different content tag: the flavor name never
    # carries the agent (it is compared by value elsewhere).
    assert codex.split(":")[0] == claude.split(":")[0] == "aat-env-grading"
    assert codex != claude


def test_unknown_flavor_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown environment flavor"):
        base_images.base_image_reference("no-such-flavor", CODEX_AGENT)


def _fake_run(return_codes: list[int], calls: list[list[str]]) -> Callable[..., SimpleNamespace]:
    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=return_codes.pop(0))

    return run


def test_existing_image_skips_build(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run([0], calls))
    base_images.ensure_base_images(["grading", "grading"], CODEX_AGENT)
    assert [command[:3] for command in calls] == [["docker", "image", "inspect"]]


def test_missing_image_is_built_with_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []
    # inspect: missing; first build fails; retry succeeds.
    monkeypatch.setattr(subprocess, "run", _fake_run([1, 1, 0], calls))
    monkeypatch.setattr(time, "sleep", sleeps.append)
    base_images.ensure_base_images(["grading"], CODEX_AGENT)
    assert [command[:2] for command in calls] == [
        ["docker", "image"],
        ["docker", "build"],
        ["docker", "build"],
    ]
    assert sleeps == [base_images.BUILD_RETRY_DELAYS_SEC[0]]
    build = calls[1]
    assert base_images.base_image_reference("grading", CODEX_AGENT) in build
    assert str(environment_path("grading", CODEX_AGENT)) in build
    out = capsys.readouterr().out
    assert "building base image aat-env-grading:" in out
    assert "retrying in 10 s" in out


def test_build_failure_after_retries_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # inspect: missing; every build attempt fails.
    return_codes = [1] + [1] * (len(base_images.BUILD_RETRY_DELAYS_SEC) + 1)
    sleeps: list[float] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(return_codes, []))
    monkeypatch.setattr(time, "sleep", sleeps.append)
    with pytest.raises(base_images.BaseImageError, match="after 4 attempts"):
        base_images.ensure_base_images(["grading"], CODEX_AGENT)
    # The full backoff ladder is waited out before giving up: the base
    # build is the launch's only registry contact, and observed registry
    # throttling persists for minutes.
    assert sleeps == [10.0, 60.0, 300.0]


def test_missing_docker_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(_command: list[str], **_kwargs: object) -> SimpleNamespace:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(base_images.BaseImageError, match="cannot run docker"):
        base_images.ensure_base_images(["grading"], CODEX_AGENT)
