"""The shipped environment templates match their generator sources.

`tools/environments/generate.py` renders every (flavor, agent) pair from
one flavor source per flavor plus one agent fragment per agent. This test
only verifies; it never writes, because a rendered template's bytes are
item identity — regenerating is a deliberate act reviewed as a contract
change (docs/design.md, "Environment templates").
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agentic_assessment_toolkit.config import (
    AGENT_TEMPLATE_SUFFIXES,
    CLAUDE_CODE_AGENT,
    CODEX_AGENT,
    ENVIRONMENT_FLAVORS,
    environment_path,
)

GENERATOR_PATH = Path(__file__).parents[1] / "tools" / "environments" / "generate.py"


def load_generator() -> ModuleType:
    """Import the generator by path: tools/ is not an installed package."""
    spec = importlib.util.spec_from_file_location("aat_environment_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = load_generator()


def test_every_agent_and_flavor_is_actually_checked() -> None:
    """The parametrized render check must never run over an empty set.

    Both parameter lists come from the same mapping the package resolves
    templates with, so an emptied mapping would turn the check below into
    a no-op that still reports success.
    """
    assert set(AGENT_TEMPLATE_SUFFIXES) == {CODEX_AGENT, CLAUDE_CODE_AGENT}
    assert ENVIRONMENT_FLAVORS


@pytest.mark.parametrize("flavor", ENVIRONMENT_FLAVORS)
@pytest.mark.parametrize("agent", sorted(AGENT_TEMPLATE_SUFFIXES))
def test_committed_template_matches_the_generator(flavor: str, agent: str) -> None:
    path, text = GENERATOR.render(flavor, agent)
    assert path == environment_path(flavor, agent)
    assert path.read_text(encoding="utf-8") == text, (
        f"{path.name} differs from its generator sources; edit "
        "tools/environments/ and re-run 'make environments'"
    )


def test_no_unexpected_dockerfile_ships() -> None:
    """The template directory holds exactly the expected renders.

    Nothing else verifies a file that is in the directory but in no
    (flavor, agent) pair — a stale render left behind by a renamed flavor,
    or a hand-written Dockerfile — so it would ship in the wheel and
    build real images while every other test passed.
    """
    directory = environment_path(ENVIRONMENT_FLAVORS[0], CODEX_AGENT).parent
    expected = {
        environment_path(flavor, agent).name
        for flavor in ENVIRONMENT_FLAVORS
        for agent in AGENT_TEMPLATE_SUFFIXES
    }
    assert {path.name for path in directory.glob("*.Dockerfile")} == expected


def test_agent_fragments_verify_their_own_cli() -> None:
    """Each fragment ends by running the agent it installed.

    A CLI that installs but cannot run would otherwise surface as a
    failed trial instead of a failed build.
    """
    for agent, expected in (
        (CODEX_AGENT, "codex --version"),
        (CLAUDE_CODE_AGENT, "claude --version"),
    ):
        fragment = GENERATOR.fragment_path(agent).read_text(encoding="utf-8")
        assert fragment.rstrip("\n").endswith(expected)


@pytest.mark.parametrize("agent", sorted(AGENT_TEMPLATE_SUFFIXES))
def test_agent_fragment_substitutes_cleanly(agent: str) -> None:
    """A fragment must not hold the placeholder, and must end in one newline.

    Rendering is a single `str.replace` of the `{agent_runtime}` line, so
    a fragment containing that text would substitute twice, and one
    missing its trailing newline would weld the following source line
    onto the fragment's last command.
    """
    fragment = GENERATOR.fragment_path(agent).read_text(encoding="utf-8")
    assert GENERATOR.PLACEHOLDER.strip() not in fragment
    assert fragment.endswith("\n")
    assert not fragment.endswith("\n\n")


NODE_DOWNLOAD = 'curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}'
NODE_CLEANUP = 'rm "node-v${NODE_VERSION}-linux-x64.tar.gz" SHASUMS256.txt'


def node_install_commands(fragment: str) -> list[str]:
    """The shared Node download, checksum, unpack, and cleanup commands.

    Each command keeps its exact text; only the shell lead-in joining it
    to the enclosing RUN is dropped, because the two fragments open their
    RUN at different points (Codex with the download, Claude Code with an
    apt install before it).
    """
    commands = [
        line.strip().removeprefix("RUN ").removeprefix("&& ") for line in fragment.splitlines()
    ]
    start = next(index for index, text in enumerate(commands) if text.startswith(NODE_DOWNLOAD))
    end = next(index for index, text in enumerate(commands) if text.startswith(NODE_CLEANUP))
    return commands[start : end + 1]


def node_version_line(fragment: str) -> str:
    return next(
        line.strip().removeprefix("ENV ")
        for line in fragment.splitlines()
        if "NODE_VERSION=" in line
    )


def test_both_fragments_install_the_same_node() -> None:
    """The Node install is duplicated between the fragments; it must agree.

    Node is not what a Codex-versus-Claude comparison is about, so a
    version bump applied to one fragment only would turn every
    cross-agent result into a Node comparison as well. The duplication
    stays (a third shared file would buy little for five lines), and this
    test is what keeps the copies equal.
    """
    codex = GENERATOR.fragment_path(CODEX_AGENT).read_text(encoding="utf-8")
    claude = GENERATOR.fragment_path(CLAUDE_CODE_AGENT).read_text(encoding="utf-8")

    shared = node_install_commands(codex)
    assert len(shared) == 5, shared
    assert node_install_commands(claude) == shared
    assert node_version_line(claude) == node_version_line(codex)


def test_claude_templates_install_procps() -> None:
    """Claude Code shells out to ps/pgrep, and Harbor's installer never runs.

    Once `claude` is on PATH, Harbor's agent-install step — the step that
    would have apt-installed procps — becomes a no-op, so the image must
    carry it. The assertion names the install itself: no other layer
    brings procps in, and a comment mentioning the package would satisfy
    a looser check.
    """
    for flavor in ENVIRONMENT_FLAVORS:
        dockerfile = environment_path(flavor, CLAUDE_CODE_AGENT).read_text(encoding="utf-8")
        assert "--no-install-recommends procps" in dockerfile


def test_claude_templates_disable_the_self_updater() -> None:
    """A CLI that can update itself in-container breaks the version pin."""
    for flavor in ENVIRONMENT_FLAVORS:
        dockerfile = environment_path(flavor, CLAUDE_CODE_AGENT).read_text(encoding="utf-8")
        assert "ENV DISABLE_AUTOUPDATER=1" in dockerfile
