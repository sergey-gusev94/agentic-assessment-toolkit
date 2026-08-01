from __future__ import annotations

from pathlib import Path

import pytest

from agentic_assessment_toolkit.rubric import (
    RubricCriterion,
    RubricError,
    parse_rubric_file,
    parse_rubric_text,
)
from tests.conftest import COURSE_ID, FIXTURES_DIR

HW1_RUBRIC = FIXTURES_DIR / "course" / COURSE_ID / "rubrics" / "HW1" / "default.md"


def test_hw1_fixture_rubric_parses_to_its_three_criteria() -> None:
    assert parse_rubric_file(HW1_RUBRIC) == [
        RubricCriterion(
            id="slope", title="the reported slope equals 2.", max_points=8.0, bonus=False
        ),
        RubricCriterion(
            id="method", title="the fitting method is stated.", max_points=2.0, bonus=False
        ),
        RubricCriterion(
            id="plot", title="a plot of the fit is included.", max_points=1.0, bonus=True
        ),
    ]


def test_singular_point_is_accepted() -> None:
    criteria = parse_rubric_text("- `a` (1 point): the single point.")
    assert criteria == [
        RubricCriterion(id="a", title="the single point.", max_points=1.0, bonus=False)
    ]


def test_decimal_points_are_accepted() -> None:
    criteria = parse_rubric_text("- `a` (2.5 points): a half-point split.")
    assert criteria[0].max_points == 2.5


def test_flexible_spacing_is_accepted() -> None:
    criteria = parse_rubric_text(
        "  - `a`   (3   points):   spaced out.\n- `b`  (1  point ,  bonus):  extra.\n"
    )
    assert criteria == [
        RubricCriterion(id="a", title="spaced out.", max_points=3.0, bonus=False),
        RubricCriterion(id="b", title="extra.", max_points=1.0, bonus=True),
    ]


def test_extra_space_after_the_dash_is_prose() -> None:
    """The trigger is exactly dash, space, backtick; "-   `a`" does not fire it."""
    text = "-   `a` (1 point): not triggered.\n- `b` (1 point): parsed.\n"
    assert [criterion.id for criterion in parse_rubric_text(text)] == ["b"]


def test_prose_headings_and_plain_bullets_are_ignored() -> None:
    text = (
        "# Rubric, HW9\n"
        "\n"
        "Free prose about the assignment.\n"
        "\n"
        "- a plain bullet without a backticked id\n"
        "- `a` (1 point): the only criterion.\n"
    )
    criteria = parse_rubric_text(text)
    assert [criterion.id for criterion in criteria] == ["a"]


def test_malformed_backticked_line_names_the_line() -> None:
    text = "# Rubric\n\n- `a` (one point): spelled-out points.\n"
    with pytest.raises(RubricError, match="line 3") as excinfo:
        parse_rubric_text(text)
    assert "one point" in str(excinfo.value)


def test_unterminated_id_is_malformed() -> None:
    with pytest.raises(RubricError, match="line 1"):
        parse_rubric_text("- `a (1 point): missing closing backtick.")


def test_duplicate_id_is_an_error() -> None:
    text = "- `a` (1 point): first.\n- `a` (2 points): again.\n"
    with pytest.raises(RubricError, match="duplicate criterion id 'a'"):
        parse_rubric_text(text)


def test_all_bonus_is_an_error() -> None:
    with pytest.raises(RubricError, match="all criteria are bonus"):
        parse_rubric_text("- `a` (1 point, bonus): only a bonus.")


def test_zero_criteria_is_an_error() -> None:
    with pytest.raises(RubricError, match="no criterion lines"):
        parse_rubric_text("# Rubric\n\nProse only, no criteria.\n")


def test_id_with_whitespace_is_an_error() -> None:
    with pytest.raises(RubricError, match="whitespace"):
        parse_rubric_text("- `a b` (1 point): spaced id.")


def test_zero_points_is_an_error() -> None:
    with pytest.raises(RubricError, match="greater than zero"):
        parse_rubric_text("- `a` (0 points): worthless.\n- `b` (1 point): fine.")


def test_source_names_the_file_in_errors(tmp_path: Path) -> None:
    rubric = tmp_path / "bad.md"
    rubric.write_text("- `a` (): no points.\n", encoding="utf-8")
    with pytest.raises(RubricError, match="bad.md, line 1"):
        parse_rubric_file(rubric)


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RubricError, match="cannot read rubric"):
        parse_rubric_file(tmp_path / "absent.md")
