"""Rubric criteria: the fixed line format and its parser.

A rubric is ordinary human-readable Markdown in which every criterion
is one bullet line of the fixed format documented in
docs/data-conventions.md ("Course content contract"):

    - `<id>` (<points> point[s][, bonus]): <title>

Any line whose stripped form starts with a dash, a space, and a
backtick must match the format; every other line is free prose and is
ignored. Grading never starts without a rubric that parses (docs/
design.md, decision 5): the CLI parses the rubric at plan time and the
grading materializer parses it again before writing the task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TRIGGER = "- `"
# The pattern begins with the trigger itself — exactly one space
# between dash and backtick — so trigger and pattern never disagree
# about which lines are criterion lines. After the id, internal
# whitespace is flexible: any amount between tokens, optional around
# the bonus comma.
_CRITERION_RE = re.compile(
    r"^- `([^`]+)`\s+\((\d+(?:\.\d+)?)\s+points?(\s*,\s*bonus)?\):\s+(\S.*)$"
)


class RubricError(Exception):
    """A rubric cannot be read or parsed into valid criteria."""


@dataclass(frozen=True)
class RubricCriterion:
    id: str
    title: str
    max_points: float
    bonus: bool


def parse_rubric_text(text: str, *, source: str = "rubric") -> list[RubricCriterion]:
    """Parse the criterion lines out of rubric Markdown, in rubric order.

    ``source`` names the rubric in error messages (the file path when
    parsing a file).
    """
    criteria: list[RubricCriterion] = []
    seen_ids: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith(_TRIGGER):
            continue  # free prose
        match = _CRITERION_RE.match(stripped)
        if match is None:
            raise RubricError(f"{source}, line {lineno}: not a valid criterion line: {stripped}")
        criterion_id, points_text, bonus_text, title = match.groups()
        if re.search(r"\s", criterion_id):
            raise RubricError(
                f"{source}, line {lineno}: criterion id contains whitespace: {criterion_id!r}"
            )
        max_points = float(points_text)
        if max_points <= 0:
            raise RubricError(f"{source}, line {lineno}: points must be greater than zero")
        if criterion_id in seen_ids:
            raise RubricError(f"{source}, line {lineno}: duplicate criterion id {criterion_id!r}")
        seen_ids.add(criterion_id)
        criteria.append(
            RubricCriterion(
                id=criterion_id,
                title=title.strip(),
                max_points=max_points,
                bonus=bonus_text is not None,
            )
        )
    if not criteria:
        raise RubricError(f"{source}: no criterion lines found")
    if all(criterion.bonus for criterion in criteria):
        raise RubricError(f"{source}: all criteria are bonus; at least one must not be")
    return criteria


def parse_rubric_file(path: Path) -> list[RubricCriterion]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RubricError(f"cannot read rubric {path}: {error}") from error
    return parse_rubric_text(text, source=str(path))
