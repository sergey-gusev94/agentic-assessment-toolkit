"""Regenerate the golden materialized-task fixtures.

Run from the repository root after a deliberate contract change:

    python tests/update_goldens.py

Golden fixtures are byte-exact expectations for the materializers
(docs/design.md, first vertical slice); regenerating them is a contract
change and must be reviewed as such.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentic_assessment_toolkit.materialize.grading import materialize_grading_task
from agentic_assessment_toolkit.materialize.solve import materialize_solve_task
from tests.conftest import COURSE_ID, FIXTURES_DIR, GOLDEN_DIR


def main() -> None:
    course_dir = FIXTURES_DIR / "course" / COURSE_ID
    if GOLDEN_DIR.exists():
        shutil.rmtree(GOLDEN_DIR)

    solve_dir = GOLDEN_DIR / "solve_tasks"
    solve_dir.mkdir(parents=True)
    materialize_solve_task(
        assignment_dir=course_dir / "assignments" / "HW1",
        course_id=COURSE_ID,
        assignment_id="HW1",
        environment_flavor="scientific-python",
        prompt_name="solver",
        tasks_dir=solve_dir,
    )

    grading_dir = GOLDEN_DIR / "grading_tasks"
    grading_dir.mkdir(parents=True)
    materialize_grading_task(
        submission_dir=FIXTURES_DIR / "submission",
        reference_solution_dir=course_dir / "reference_solutions" / "HW1",
        rubric_path=course_dir / "rubrics" / "HW1" / "default.md",
        item_id=f"{COURSE_ID}/stu1/HW1",
        name_parts=(COURSE_ID, "stu1", "HW1"),
        prompt_name="grader",
        tasks_dir=grading_dir,
    )
    print(f"regenerated goldens under {GOLDEN_DIR}")


if __name__ == "__main__":
    main()
