# Agentic Assessment Toolkit

A Python toolkit built around one assignment-and-grading pipeline in which a
"submission" can come either from an autonomous coding agent or from a real
student. It serves two workflows:

1. **Benchmarking** coding agents (Codex CLI, Claude Code, Gemini CLI, and
   others, running on existing user subscriptions) on real chemical
   engineering coursework.
2. **Grading assistance** for professors and teaching assistants: an
   independent grade with per-criterion explanations, produced by the same
   machinery, as an information tool.

The project is in its initial development stage.

The initial implementation is **Codex-first**: the complete solving, grading,
validation, reporting, and hardening pipeline will be built for Codex before
other agent stacks and cross-agent comparisons are added. See the
[design](docs/design.md) for the authoritative sequencing decision.

## Documentation

- [Project brief](docs/brief.md) — vision, use cases, scope, and constraints.
- [Design](docs/design.md) — decisions, architecture of the solving and
  grading pipelines, and roadmap.
- [Live validation](docs/live-validation.md) — maintainer-run integration
  checks and the exact limits of what they establish.
- [Research](docs/research.md) — frozen research snapshot: alternatives
  considered, methodology, security model.
- [Data conventions](docs/data-conventions.md) — strict code–data separation:
  this repository is always safe to publish; all real course and student data
  lives in an external data root and is never committed.

## Usage

The `aat` command materializes Harbor tasks from a data root (external
to this repository, see [data conventions](docs/data-conventions.md))
and launches `harbor run`:

```bash
export AAT_DATA_DIR=/path/to/data-root

aat solve --course PU_CHE597DS_S2026 --config codex-high
aat grade --from-solve codex-high --config codex-grader-high
aat grade --course PU_CHE597DS_S2026 --config codex-grader-high  # student folders
```

Experiment configs live under [`configs/`](configs/); a bare
`--config NAME` resolves to `configs/NAME.toml` relative to the current
working directory, so run from the repository root or pass an explicit
path. Selection and mechanics (`--repeats`, `--max-concurrent-trials`,
`--force`, `--dry-run`, `--materialize-only`) are CLI flags. Concurrent
trials default to 8. Already-done items are skipped by default, so bulk
commands are naturally incremental. See the
[design](docs/design.md) for the full CLI contract.

To repeat a completed item, add `--force`. Repeats are additional trials;
they never replace prior results:

```bash
# Add one new solver trial.
aat solve --course PU_CHE597DS_S2026 --assignment HW5 \
  --config codex-high --force

# Add three new solver trials in one job.
aat solve --course PU_CHE597DS_S2026 --assignment HW5 \
  --config codex-high --force --repeats 3
```

A subsequent `aat grade --from-solve codex-high ...` automatically selects
new solver trials that have not yet been graded. To run another independent
grader trial over an already-graded submission, use `--force` on `aat grade`
(and optionally `--repeats N`).

Inspect a run with the exact per-job command printed by `aat`, for example:

```bash
harbor view "$AAT_DATA_DIR/grading/20260801T044338Z__codex-grader-high__44292e75"
```

Do not point `harbor view --jobs` at the shared `runs/` or `grading/` parent:
those directories contain AAT job wrappers, with each Harbor job nested one
level deeper, and Harbor 0.20 misidentifies the nested job as a trial. Also do
not use Harbor's suggested `upload` command for real coursework unless the
assignment, reference solution, submissions, and transcripts are authorized
for disclosure or have been sanitized.

## Development

Requires Python 3.12+. Install and validate with:

```bash
pip install -e ".[dev]"
make check
```

Harbor is installed as the package's single runtime dependency. Running
actual jobs additionally requires Docker and the agent CLIs (Codex CLI
first), which are external tools packaging cannot provide; repository
tests never invoke any of them.
