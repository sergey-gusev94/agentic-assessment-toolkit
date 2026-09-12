# Agentic Assessment Toolkit

Agentic Assessment Toolkit is a Python command-line tool for two related
workflows:

1. Benchmarking coding agents on university coursework.
2. Producing rubric-based grading assistance for student submissions.

Both workflows use the same assignment, reference solution, rubric, task
materialization, grading schema, and reporting machinery. A benchmark
submission comes from a coding agent. A student submission comes from an LMS
export. The toolkit is a thin layer over
[Harbor](https://github.com/harbor-framework/harbor), which runs the agents in
containers and stores their artifacts and trajectories.

Codex and Claude Code are supported as solvers, initial graders, and final
judges. Solving and grading are separate jobs, so either agent can grade the
other agent's work.

## Status and limitations

This is an actively developed research tool. Its command-line and data
contracts are documented and tested, but they may still change between
revisions.

The current security model is suitable for research over trusted
historical coursework. Grading reads untrusted submission content inside a
networked container that holds a model-provider credential. The grader is
instructed to inspect submissions without executing them, but that rule is not
enforced by a separate security boundary. Do not use the toolkit for
adversarial or institution-wide deployment without reviewing the security and
privacy model in [the design](docs/design.md).

This repository contains the toolkit only. Course materials, reference
solutions, student submissions, identity tables, credentials, and experimental
results are not included because they contain protected or institution-owned
data. The files under `tests/fixtures/` are synthetic and exist only to test the
software.

## Requirements

- A Linux x86_64 host, or an equivalent Docker environment. The shipped task
  images currently install x86_64 agent binaries.
- Python 3.12 or later. The installation commands below use Conda to create
  the Python environment.
- Git.
- Docker for live solve and grading jobs. Repository tests do not use Docker.
- A supported agent credential for live jobs. See
  [Authentication](docs/authentication.md).
- The host Codex CLI for course intake. Claude Code needs its host CLI only to
  create a subscription token.
- Pandoc 3.1.2 or later on the host for `aat export-results`. PDF rendering
  uses the Typst Python package installed with the toolkit and its bundled fonts.

## Installation

Create a Conda environment, clone the repository, and install the package in
editable mode:

```bash
conda create --name aat python=3.12 pip
conda activate aat

git clone ANONYMOUS_REPOSITORY_URL
cd agentic-assessment-toolkit
python -m pip install -e .
```

Run commands from the repository root. A bare configuration name resolves to
`configs/<name>.toml` relative to the current directory. An explicit config
path works from any directory.

For development, install the optional tools and run the full local check:

```bash
python -m pip install -e ".[dev]"
make check
```

## Data root

All real course data and generated results live in a data root outside this
repository. The default is `~/aat-data`. Use `--data-root PATH` for one command
or set `AAT_DATA_DIR` for a shell or environment.

Create the data root once:

```bash
aat init-data
```

Add `--git` to create it as a separate private Git repository. The complete
layout and immutability rules are in
[Data conventions](docs/data-conventions.md).

## Main workflow

The normal workflow processes all available courses and submissions:

```bash
aat intake --all
aat check-course --course SYN_COURSE1_F2025
aat ingest-submissions --all
aat solve --all --config codex-high
aat grade --from-solve codex-high --config codex-grader-sol-high
aat grade --all --config codex-grader-sol-high
aat report
```

These commands are incremental. Repeating the same sequence skips work that is
already complete and processes only new or incomplete items. Narrow a run with
`--course ID` and, where supported, `--assignment ID`.

Before a live run, check which credential the selected config will use:

```bash
aat check-auth --config codex-grader-sol-high
```

Use `--dry-run` to inspect a selection without creating anything. Use
`--materialize-only` to write the Harbor tasks and job configuration without
launching Harbor. Both modes are offline and require no credential.

### Course intake

Place collected professor materials under `raw/<course_id>/` in the data root,
then run `aat intake`. Intake asks the host Codex CLI to create the structured
course tree, including a rubric draft for each material-backed assignment.

Review the generated course and `intake-notes.md`, then run `aat check-course`
until it reports no contract violations. Grading refuses an assignment without
an approved rubric. See [Course intake](docs/course-intake.md) for the review
procedure.

### Student submission ingest

Place Brightspace download zips or Gradescope graded-copy zips under
`raw-submissions/<course_id>/`, then run `aat ingest-submissions`. Ingest is
deterministic code, not an agent. It normalizes submissions, assigns pseudonym
identifiers, stores the identity mapping under `tables/`, and removes
Gradescope grade-summary pages before a submission can reach a grader.

The command prints rows that need review and exits nonzero while anything is
skipped or frozen. Resolve ambiguous names and filenames in the course's
`manifest.toml`, then run the command again. The full contract is in
[Data conventions](docs/data-conventions.md), under "Submission ingest."

### Solving and grading

`aat solve` materializes one Harbor task per selected assignment. A separate
`aat grade` job grades either verified solver artifacts or normalized student
folders. Every grading produces structured per-criterion scores and a Markdown
justification.

Initial graders can be followed by a final judge. The judge receives a fixed
number of stored initial gradings and independently reconciles them against the
submission:

```bash
aat grade --all --config codex-judge-sol-high \
  --context-from codex-grader-sol-high --gradings 3
```

Use `--repeats N` to ensure each selected item has N valid trials. Use `--force`
to add new trials even when an item is already complete. Existing trials are
never overwritten.

### Export final results to Brightspace

After final judging, `aat export-results` creates a fresh directory containing
`feedback.zip`, `grades.csv`, and a staff-only `manifest.json`. It reads stored
results across jobs, so successful retries contribute without rerunning models.
The CSV and each student's PDF use the same final judgment.

```bash
aat export-results --course COURSE_ID --assignment HW1 \
  --config codex-judge-sol-high \
  --context-from codex-grader-sol-high --gradings 3 \
  --submissions-zip '/path/to/Assignment 1 Download.zip' \
  --grade-export '/path/to/GradesExport.csv'
```

Use the original Brightspace submission ZIP and an actual grade export with
**Username**, **Points grade**, and exactly one numeric grade item. The CSV
supplies the full roster, exact grade-item name, and maximum points. It is not
the sample import CSV. Repeat `--submissions-zip` if the assignment spans
multiple downloads. Complete submission ingest before exporting.

Missing or ambiguous judgments block export. Use `--zero-missing` only after
confirming the downloads are complete and roster students without a submission
should receive zero. Submitted work with failed grading still needs a successful
judgment. `--allow-partial` explicitly omits unresolved students from both upload
files and lists them in the manifest. Multiple eligible judgments require
`--trial JOB/TRIAL`; multiple stored config versions require the displayed
identity selectors. Bonus grades above the gradebook maximum require Brightspace's
**Can Exceed** setting and `--can-exceed`.

Outputs default to `analysis/exports/` in the data root. `--out PATH` selects
another parent directory outside the repository and source-data trees. Each run
creates a new snapshot, including when you rerun the same command after grading
more students. Read [the export contract](docs/design.md#brightspace-results-export)
for selection, validation, and PDF details.

Upload **feedback.zip** through the assignment's **Add Feedback Files**. Import
**grades.csv** through **Grades > Enter Grades > Import**, matching the existing
grade item. Keep **manifest.json** locally. Review both files before import:
Brightspace synchronizes imported grades for linked assignments as published
feedback. See [D2L's grade import documentation](https://community.d2l.com/brightspace/kb/articles/3538-importing-grades)
and [the feedback attachment workflow](https://community.d2l.com/brightspace/kb/articles/5175-evaluate-assignments-using-the-assignments-tool).
The maintainer validates a small upload in the target Brightspace instance;
repository checks cover local generation only.

## Experiment configurations

Committed configs live under [`configs/`](configs/). Selection, repeat count,
concurrency, and dry-run behavior are command-line options. Agent, model,
reasoning effort, prompt, rubric, and agent arguments belong to the config and
form part of its recorded identity.

Initial-grader and final-judge configs use the same model matrix:

| Model and reasoning | Initial grader | Final judge |
| --- | --- | --- |
| Sol, high | `codex-grader-sol-high` | `codex-judge-sol-high` |
| Luna, high | `codex-grader-luna-high` | `codex-judge-luna-high` |
| Luna, max | `codex-grader-luna-max` | `codex-judge-luna-max` |
| Terra, high | `codex-grader-terra-high` | `codex-judge-terra-high` |
| Opus 5, high | `claude-grader-opus5-high` | `claude-judge-opus5-high` |
| Opus 5, max | `claude-grader-opus5-max` | `claude-judge-opus5-max` |
| Sonnet 5, high | `claude-grader-sonnet5-high` | `claude-judge-sonnet5-high` |

The solve configs are `codex-high`, `claude-opus5-high`, and
`claude-sonnet5-high`.

For a rubric experiment, `aat grade --rubric NAME` selects
`rubrics/<assignment_id>/NAME.md`. The rubric name and bytes enter the config
and item identities, so results from different rubric variants never pool.

## Reporting and inspection

`aat report` reads stored Harbor jobs and writes one timestamped report
directory under `analysis/` in the data root. It contains tidy trial and
criterion tables, benchmark and grading summaries, failure accounting, review
queues, a Markdown report, and provenance. Use `--out PATH` to choose another
destination outside this repository.

Harbor can inspect one job or a whole stage:

```bash
harbor view ~/aat-data/grading/20260801T044338Z__codex-grader-sol-high__44292e75
harbor view ~/aat-data/solving
```

Do not use Harbor's upload suggestion for real coursework unless every input,
submission, result, and transcript is authorized for disclosure or has been
sanitized.

Optimization tasks can use a Gurobi WLS license mounted from outside both the
repository and data root. See [Gurobi WLS setup](docs/gurobi.md).

## Documentation

- [Authentication](docs/authentication.md): Codex and Claude Code credential
  setup and selection.
- [Course intake](docs/course-intake.md): raw professor materials to a reviewed
  course tree.
- [Data conventions](docs/data-conventions.md): data-root layout, course and
  submission contracts, privacy boundaries, and immutability.
- [Design](docs/design.md): implemented decisions, task contracts, identities,
  results, statistics, and CLI behavior.
- [Gurobi WLS setup](docs/gurobi.md): licensed optimization runs.
- [Roadmap](docs/roadmap.md): work deferred until a concrete need appears.

## Development

`make check` runs formatting verification, linting, strict type checking, and
the deterministic offline test suite. Useful individual targets are
`make format`, `make lint`, `make typecheck`, `make test`, and `make package`.

Live Harbor runs, Docker builds, and model-provider calls are intentionally not
part of repository validation.

## License

Agentic Assessment Toolkit is licensed under the Apache License 2.0. See
[`LICENSE`](LICENSE).
