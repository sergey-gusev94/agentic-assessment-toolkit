# Code–Data Separation Conventions

This document specifies the concrete conventions behind the "strict code–data
separation" constraint in [brief.md](brief.md). The repository must always be
safe to publish; all real data lives outside it. Both the repository-side
guards and the data-root resolution and refusal rules below are implemented
(`src/agentic_assessment_toolkit/data_root.py`).

## Data root

All real data lives under a single directory outside the repository working
tree, called the **data root**.

Resolution order:

1. An explicit path passed programmatically or on the command line.
2. The `AAT_DATA_DIR` environment variable.
3. Otherwise: fail with a clear error. There is no default.

The toolkit refuses a data root inside its own repository. Precisely:
the resolved data root must not lie inside a git working tree whose
`pyproject.toml` declares `name = "agentic-assessment-toolkit"`; the
check is applied to the git root of the installed package location
(which catches editable installs), to the git root of the current
working directory, and to the git root of the resolved data root
itself (which catches pointing `AAT_DATA_DIR` into any other checkout
of the toolkit). A data root may itself be a separate private git
repository — only the toolkit's own tree is refused.

## Data root layout

```text
$AAT_DATA_DIR/
├── courses/                    # immutable source-of-truth course content
│   └── <course_id>/            #   e.g. PU_CHE456_F2025
│       ├── course.toml         #   course defaults (e.g. environment)
│       ├── assignments/
│       │   ├── <assignment_id>/      # as-received handout, exactly as given
│       │   └── <assignment_id>.toml  # optional per-assignment overrides
│       ├── reference_solutions/
│       │   └── <assignment_id>/      # instructor/oracle solution
│       └── rubrics/
│           └── <assignment_id>/
│               └── <name>.md         # default.md; variants are new files
├── submissions/                # real student submissions, as received
│   └── <course_id>/<student_id>/<assignment_id>/
├── tables/                     # rosters, grade exports, identity mappings
├── tasks/                      # materialized Harbor task inputs, per job
│   └── <utc>__<config>__<hash8>/
├── runs/                       # solve jobs (Harbor job dirs + aat-run.json)
│   └── <utc>__<config>__<hash8>/
├── grading/                    # grading jobs, any submission source
│   └── <utc>__<config>__<hash8>/
├── analysis/                   # derived tables and reports, regenerable
└── scratch/                    # disposable working space
```

Notes:

- `courses/` content is immutable once registered; benchmark results
  reference assignments, rubrics, and prompts by hash.
- Transcripts and trajectories under `runs/` and `grading/` are data, not
  logs: they embed full assignment content and possibly student text.
- `tables/` holds the only mapping between real identities and anonymized
  IDs; it never leaves the data root.
- `tasks/` holds the byte-deterministic materialized task directories,
  one subdirectory per job, named like the job directory. Tasks live
  outside the job directories because Harbor's resume deletes any
  job-directory subdirectory without a per-trial result file as a
  stale trial; `harbor-job.json` references them by absolute path.
- `analysis/` holds only derived outputs: statistics tables and
  reports. Everything in it is regenerable from `runs/`, `grading/`,
  and `tables/`; each report invocation writes one timestamped
  subdirectory containing its tables, Markdown report, and provenance
  (toolkit version, config identities, job directories consumed, and
  the bootstrap seed). It is a cache, never a source of truth.
- Solver licenses (e.g. a Gurobi WLS license file or its
  `GRB_WLSACCESSID`/`GRB_WLSSECRET`/`GRB_LICENSEID` values) are
  credentials: they live outside the repository — in the data root or
  the maintainer's environment — and are injected into containers at
  run time, never baked into images or committed.

## Course content contract

- `assignments/<assignment_id>/` is the handout exactly as a student
  would receive it: no injected metadata, no prompt, no statement file
  singled out. The solve-task materializer copies the whole directory
  into the task at `/app/assignment`.
- `<assignment_id>.toml`, beside the assignment directory, is an
  optional sidecar for per-assignment settings (e.g. `environment`,
  resource or time overrides). Absent means course defaults apply.
- `course.toml` holds course-wide defaults, most importantly the
  default environment template flavor (e.g. `data-science`,
  `optimization`). The sidecar overrides it per assignment; if neither
  names an environment, materialization fails with a clear error.
- `reference_solutions/<assignment_id>/` and
  `rubrics/<assignment_id>/` share the assignment's id. Keeping them in
  separate top-level trees — never inside the assignment directory —
  makes it structurally impossible for the solve-task materializer to
  leak grading material to the solver, which only ever reads
  `assignments/`.
- Rubrics are Markdown files named within
  `rubrics/<assignment_id>/`; the default is `default.md`. A revised
  rubric is a new file (e.g. `strict-v2.md`) selected by name in a
  grading config — existing files are never edited, preserving
  immutability and hash-based provenance.

## Job directories and run records

Each `aat solve` or `aat grade` invocation creates one job directory —
`<utc-timestamp>__<config-name>__<identity-prefix8>/` (with a rare `-N`
suffix on same-second collisions) — under `runs/` (solve jobs) or
`grading/` (grading jobs, whether the submissions are benchmark
artifacts or real student folders). The AAT job directory is itself the
Harbor job directory: Harbor's `config.json`, `lock.json`,
`result.json`, `job.log`, and per-trial directories live directly
inside it, beside exactly two AAT files — `aat-run.json` (the run
record) and `harbor-job.json` (the generated Harbor job config).
Materialized tasks live under `tasks/<job-name>/` (see above), so
re-running the recorded command safely resumes an interrupted job. The
run record holds the Harbor version (read from the binary for executed
runs, from package metadata for materialize-only runs), the toolkit's
own version, agent and model configuration, effective command line,
repeats, maximum concurrent trials, and executed flag,
requested items with their per-item identities and explicit course and
assignment ids, config identity, and
input hashes (assignment, prompt, environment template, verifier,
rubric, submission, reference solution, grading schema — as
applicable). Doneness of an item under a config is derived from these
directories and Harbor's per-trial result files; there is no separate
bookkeeping state. A solve item is done when a completed, non-error
trial exists (a 0-reward contract failure is a countable outcome); a
grading item is done only when a completed trial produced a valid
grading result — failed gradings are regraded by the next incremental
run.

Because the layout is flat, `harbor view` works on the shared `runs/`
or `grading/` parent to browse a stage's jobs, and the exact
`harbor view <specific-job-directory>` command printed by `aat` opens
one job. Harbor may also print
an `upload` suggestion after a run; do not upload real-course jobs unless
all assignment, reference, submission, grading, and transcript content is
authorized for disclosure or has been sanitized.

## What is committable and what is not

| Committable (repository) | Never committed (data root) |
|---|---|
| Package code and tests | Real assignments and handouts |
| Documentation | Reference/oracle solutions |
| Grader prompt templates | Student submissions |
| Solver prompt templates | Rosters, grade exports, identity maps |
| Experiment configs (`configs/`) | Harbor runs, transcripts, trajectories |
| Environment (Dockerfile) templates | Grading outputs for real submissions |
| Small fully synthetic example assignments and fixtures | Credentials and auth files |

The research document's sketch layout places `benchmarks/.../tasks/` inside
the repository; this convention supersedes that detail. Real course
assignments are course-owned content and belong in the data root. The
repository may contain only synthetic example tasks created for tests,
documentation, and demos.

## Repository-side guards (defense in depth)

The structural rule above is the primary defense. In addition, the repository
keeps:

- `.gitignore` patterns for data-shaped directories (`data/`, `runs/`,
  `jobs/`, `grading/`, `submissions/`, `reference_repos/`), so accidental
  in-repo copies stay untracked.
- Pre-commit guards: `check-added-large-files` (500 KB threshold) and a
  `forbid-data-files` hook that rejects notebooks, CSV/TSV, spreadsheets,
  Parquet, PDFs, archives, pickles, model weights, and database files
  anywhere outside the `tests/fixtures/` allowlist.

## Tests and examples

Tests and documentation examples use only small, fully synthetic fixtures
authored for this repository. They must never be derived from real course
content or student work, consistent with the AGENTS.md rule that tests are
deterministic, local, offline, and credential-free.
