# Maintainer Live-Validation Record

Live validation is performed manually by the maintainer outside repository
development. Repository tests remain deterministic, local, offline, and
credential-free, and no repository deliverable depends on these results.

This document records only reproducible configuration and conclusions. Harbor
jobs, artifacts, transcripts, trajectories, credentials, and real course data
remain in the external data root and are never committed here.

## 2026-07-31: Codex Harbor smoke tests

### Configuration

- Harbor: `0.20.0`
- Codex CLI: `0.146.0`
- Harbor agent: `codex`
- Model: `openai/gpt-5.6-sol`
- Authentication: `CODEX_AUTH_JSON_PATH` pointing to the maintainer's cached
  Codex authentication file
- `OPENAI_API_KEY`: unset
- Task: Harbor Cookbook's synthetic `simple-task`
- Concurrency: 1

The authentication observation records how the run was configured. It does
not independently establish a provider billing conclusion.

### Commands

Initial execution and deterministic verification:

```bash
export CODEX_AUTH_JSON_PATH="$HOME/.codex/auth.json"
unset OPENAI_API_KEY

harbor run \
  -p harbor_cookbook/recipes/simple-task \
  --agent codex \
  --model openai/gpt-5.6-sol \
  --n-concurrent 1
```

Artifact collection check:

```bash
harbor run \
  -p harbor_cookbook/recipes/simple-task \
  --agent codex \
  --model openai/gpt-5.6-sol \
  --n-concurrent 1 \
  --artifact /app/hello.txt
```

### Validated

- Codex completed the synthetic Harbor task without an exception.
- The deterministic verifier returned reward `1.0`.
- Harbor retained raw Codex agent output.
- Harbor retained an ATIF v1.7 trajectory.
- Harbor captured `/app/hello.txt` as a declared artifact.
- The artifact manifest reported `status: "ok"` for `/app/hello.txt`.
- The captured artifact contained exactly `Hello, world!` followed by a
  newline.

These checks validate synthetic Codex execution and artifact collection. They
do not validate the complete assessment or grading pipeline.

## 2026-07-31: Real-assignment Harbor pilot

A manually materialized `PU_CHE597DS_S2026/HW5` notebook task ran with the
same Harbor, Codex, model, and cached-auth configuration. The Docker image
provided pinned Python, NumPy, Matplotlib, nbconvert, and ipykernel packages
with public network access.

- One trial completed in 8m32s with no exception and completion reward `1.0`.
- Harbor retained the raw output, ATIF trajectory, and `/app/submission`.
- The notebook ran top-to-bottom with 10 executed code cells, no error outputs,
  and 8 embedded plots; source data remained unchanged.
- Problems 1–6 and the optional extension were completed without installing
  additional packages, and manual review found results consistent with the
  reference solution.

This validates a manually authored real-assignment solve and artifact path. The
verifier checked completion, not scientific correctness; automatic task
materialization and rubric/agent judging remain pending.

The same assignment was then run with `harbor-rewardkit==0.1.7` and seven
programmatic reward dimensions. One trial completed in 10m10s with no
exception; every dimension and the aggregate reward were `1.0`, verifier
stderr was empty, `reward-details.json` was retained, and all submission
artifacts were collected. This validates RewardKit's shared-environment,
multi-dimension Harbor integration, not scientific correctness or a rubric
judge.

### Design revision, 2026-07-31

RewardKit and `harbor job regrade` were descoped after the checks above were
recorded; grading now runs as ordinary Harbor grading jobs, per
[design.md](design.md). The validated results above stand as history; the
pending list below reflects the revised design.

### Pending

- Automatic real-assignment materialization by the toolkit importer.
- A Harbor grading job grades a Harbor-produced submission directory using
  cached Codex authentication, producing a valid `grading_result.json` and
  Markdown justification, with the generic grading verifier surfacing the
  score as the reward.
- The same grading task re-run with a revised rubric over unchanged
  artifacts (regrade-by-rerun).
- Judge sanity trio: reference solution near full marks, empty or
  irrelevant submission near zero, stable repeated gradings.
- Repeated attempts, explicit failure accounting, and statistical reporting.

### Deferred by project priority

- Claude Code authentication, execution, and judging.
- Gemini CLI and other agent stacks.
- Cross-agent comparisons.

These items follow completion and hardening of the end-to-end Codex pipeline,
as specified in [design.md](design.md).
