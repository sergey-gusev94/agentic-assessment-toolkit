#!/usr/bin/env python3
"""Generic grading-stage contract verifier.

Standalone by design: runs inside the grading task container beside a
verbatim copy of the toolkit's grading_schema.py — one source of truth
for validation (docs/design.md, "Grading output schema"). Validates the
two required grading deliverables, cross-checks the grader's authored
criteria against the expected-criteria file the materializer wrote
beside this script (any divergence — dropped, renamed, added, or
reweighted criteria — is a contract failure; when the file is absent,
as in tasks materialized before it existed, the check is skipped), then
derives the percentage scores
from sums computed from the criteria: the bonus-inclusive score_pct
(may exceed 100) is the primary Harbor reward, with the bonus-free
base_pct (0-100) surfaced beside it. The grader's authored sums are
a self-check only — a mismatch is recorded in the details as
sums_consistent: false, never failed. Any structural contract violation
yields reward 0.0 with the violations listed in the verifier details.
Extra scratch files in the output directory are tolerated.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grading_schema import (  # noqa: E402
    EXPECTED_CRITERIA_FILENAME,
    JUSTIFICATION_FILENAME,
    RESULT_FILENAME,
    derive_scores,
    expected_criteria_errors,
    load_grading_result,
    sums_report,
)

# The environment overrides exist so repository tests can exercise this
# script as a real subprocess; inside a task container the defaults hold.
GRADING_OUTPUT_DIR = Path(os.environ.get("AAT_GRADING_OUTPUT_DIR", "/app/grading_output"))
REWARD_PATH = Path(os.environ.get("AAT_REWARD_PATH", "/logs/verifier/reward.json"))
EXPECTED_CRITERIA_PATH = Path(__file__).resolve().parent / EXPECTED_CRITERIA_FILENAME


def main():
    data, errors = load_grading_result(GRADING_OUTPUT_DIR / RESULT_FILENAME)

    justification = GRADING_OUTPUT_DIR / JUSTIFICATION_FILENAME
    if not justification.is_file():
        errors.append(f"missing required file {JUSTIFICATION_FILENAME}")
    else:
        try:
            if not justification.read_text(encoding="utf-8").strip():
                errors.append(f"{JUSTIFICATION_FILENAME} is empty")
        except UnicodeDecodeError:
            errors.append(f"{JUSTIFICATION_FILENAME} is not UTF-8 text")

    # Rubric cross-check: the authored criteria must reproduce the
    # rubric's expected criteria exactly, or the score denominator is
    # silently wrong. Tasks materialized before the expected-criteria
    # file existed have no file, and the check is skipped so
    # doneness-based reruns of old jobs keep working.
    # expected_criteria_checked is false exactly when the check did not
    # run: the file is absent, structural validation failed first, or
    # the file itself is malformed (which is a contract error, never an
    # uncaught crash that would leave no reward file).
    expected_criteria_checked = False
    if not errors and EXPECTED_CRITERIA_PATH.is_file():
        try:
            expected = json.loads(EXPECTED_CRITERIA_PATH.read_text(encoding="utf-8"))
            mismatches = expected_criteria_errors(expected, data)
        except Exception as error:  # noqa: BLE001 - malformed expected-criteria file
            errors.append(
                f"malformed {EXPECTED_CRITERIA_FILENAME}: {type(error).__name__}: {error}"
            )
        else:
            errors.extend(mismatches)
            expected_criteria_checked = True

    if errors:
        rewards = {"reward": 0.0}
        scores = {"score_pct": None, "base_pct": None}
        sums = None
    else:
        scores = derive_scores(data)
        sums = sums_report(data)
        rewards = {"reward": scores["score_pct"], "base_pct": scores["base_pct"]}

    emit(
        rewards,
        {
            "contract": "grading-output-v1",
            "contract_valid": not errors,
            "errors": errors,
            "expected_criteria_checked": expected_criteria_checked,
            "score_pct": scores["score_pct"],
            "base_pct": scores["base_pct"],
            # Authored-sum self-check: recorded, never a failure.
            "sums_consistent": None if sums is None else sums["consistent"],
            "sums": sums,
        },
    )
    return 0


def emit(rewards, details):
    # Harbor reads the rewards from /logs/verifier/reward.json: a flat JSON
    # object of numbers, "reward" being the primary one. Details go to
    # stdout, which Harbor captures as the verifier's test-stdout.txt.
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(json.dumps(rewards), encoding="utf-8")
    print(json.dumps({"reward": rewards["reward"], "details": details}, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
