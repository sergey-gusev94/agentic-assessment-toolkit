#!/usr/bin/env python3
"""Generic grading-stage contract verifier.

Standalone by design: runs inside the grading task container beside a
verbatim copy of the toolkit's grading_schema.py — one source of truth
for validation (docs/design.md, "Grading output schema"). Validates the
two required grading deliverables and surfaces score_pct (0-100 over
required criteria) as the Harbor reward; any contract violation yields
reward 0.0 with the violations listed in the verifier details. Extra
scratch files in the output directory are tolerated.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grading_schema import (  # noqa: E402
    JUSTIFICATION_FILENAME,
    RESULT_FILENAME,
    load_grading_result,
)

# The environment overrides exist so repository tests can exercise this
# script as a real subprocess; inside a task container the defaults hold.
GRADING_OUTPUT_DIR = Path(os.environ.get("AAT_GRADING_OUTPUT_DIR", "/app/grading_output"))
REWARD_PATH = Path(os.environ.get("AAT_REWARD_PATH", "/logs/verifier/reward.json"))


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

    if errors:
        reward = 0.0
        score_pct = None
    else:
        score_pct = float(data["score_pct"])
        reward = score_pct

    emit(
        reward,
        {
            "contract": "grading-output-v1",
            "contract_valid": not errors,
            "errors": errors,
            "score_pct": score_pct,
        },
    )
    return 0


def emit(reward, details):
    # Harbor reads the reward from /logs/verifier/reward.json: a flat JSON
    # object of numbers. Details go to stdout, which Harbor captures as
    # the verifier's test-stdout.txt.
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(json.dumps({"reward": reward}), encoding="utf-8")
    print(json.dumps({"reward": reward, "details": details}, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
