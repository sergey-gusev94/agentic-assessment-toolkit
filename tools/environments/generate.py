"""Render the shipped environment templates from these sources.

Two kinds of source live here, and the extensions say which is which.
Each flavor source, `<flavor>.Dockerfile.in`, holds one flavor's image
with the agent's install lines replaced by a single `{agent_runtime}`
line; each agent fragment, `agent-<agent>.part`, holds those lines for
one agent. This script renders every (flavor, agent) pair into the
package's template directory: `<flavor>.Dockerfile` for Codex and
`<flavor>-claude.Dockerfile` for Claude Code.

Run it deliberately:

    python tools/environments/generate.py

and review the diff as a contract change. A rendered template's bytes
are item identity (docs/design.md, "Experiment configs and config
identity"): editing one forks the doneness and pooling key of every
item that uses it, so already-collected trials stop pooling with new
ones. That is exactly what should happen when the image really changes,
and exactly what must not happen by accident.

For the same reason the rendered Dockerfiles carry no "generated; do
not edit by hand" header: adding a line would change their bytes and
fork every existing item identity. The notice lives here and in the
sources instead. Sources live under `tools/` rather than in the package
so a ~1200-line duplicate of grading.Dockerfile does not ship as
package data; nothing at run time reads them.

A repository test (tests/test_environment_templates.py) asserts every
committed rendered Dockerfile is byte-identical to what this script
produces, so a hand edit to a rendered file fails `make check`.
"""

from __future__ import annotations

from pathlib import Path

from agentic_assessment_toolkit.config import AGENT_TEMPLATE_SUFFIXES, ENVIRONMENT_FLAVORS

SOURCE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SOURCE_DIR.parents[1]
TARGET_DIR = REPO_ROOT / "src" / "agentic_assessment_toolkit" / "templates" / "environments"

PLACEHOLDER = "{agent_runtime}\n"


def fragment_path(agent: str) -> Path:
    """The agent fragment holding one agent's install lines."""
    return SOURCE_DIR / f"agent-{agent}.part"


def render(flavor: str, agent: str) -> tuple[Path, str]:
    """The rendered path and text for one (flavor, agent) pair.

    Raises ``ValueError`` on a malformed source, so a caller other than
    ``main`` — the repository test renders in-process — gets an exception
    it can see rather than an interpreter exit.
    """
    # The flavor's own suffix comes from the package, so the set of
    # agents rendered here and the set the package resolves templates
    # for cannot drift apart.
    suffix = AGENT_TEMPLATE_SUFFIXES[agent]
    template = (SOURCE_DIR / f"{flavor}.Dockerfile.in").read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError(f"{flavor}.Dockerfile.in must hold exactly one {PLACEHOLDER!r} line")
    fragment = fragment_path(agent).read_text(encoding="utf-8")
    return TARGET_DIR / f"{flavor}{suffix}.Dockerfile", template.replace(PLACEHOLDER, fragment)


def main() -> None:
    # Every pair is rendered before anything is written: a failure
    # partway through would otherwise leave the identity-bearing template
    # directory half-regenerated, which is far harder to notice than a
    # command that did nothing.
    try:
        rendered = [
            render(flavor, agent)
            for flavor in ENVIRONMENT_FLAVORS
            for agent in AGENT_TEMPLATE_SUFFIXES
        ]
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    for path, text in rendered:
        path.write_text(text, encoding="utf-8")
        print(f"rendered {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
