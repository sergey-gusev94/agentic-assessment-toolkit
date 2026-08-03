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
3. The per-user default `~/aat-data`.

Resolution never creates anything. If `~/aat-data` does not exist,
resolution fails with a clear error naming the attempted path, the two
override mechanisms, and `aat init-data`. This keeps the usual
single-user setup free of repeated path arguments while alternate
corpora and installations remain explicit.

Creation is a separate, explicit command: `aat init-data` makes the
resolved data root — the directory, the top-level layout below, and a
short README — and is idempotent, so it also fills in missing top-level
directories of an existing root. With `--git` it additionally runs
`git init` and writes a `.gitignore` covering the regenerable
directories (`tasks/`, `analysis/`, `scratch/`); versioning the data
root is optional and always private — see the refusal rule below for
the only git constraint the toolkit enforces.

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
├── raw/                        # as-collected course material dumps
│   └── <course_id>/            #   intake's input; kept verbatim for provenance
├── raw-submissions/            # as-downloaded LMS submission exports
│   └── <course_id>/            #   ingest's input; export zips kept verbatim,
│       ├── *.zip               #   plus an optional manifest.toml for overrides
│       └── manifest.toml
├── courses/                    # source-of-truth course content (frozen at first use)
│   └── <course_id>/            #   e.g. PU_CHE456_F2025
│       ├── course.toml         #   course record + assessment registry
│       ├── intake-notes.md     #   intake's review aid: judgment calls, open items
│       ├── intake-record.json  #   receipt written by `aat intake` (doneness)
│       ├── syllabus/           #   syllabus file(s), copied verbatim
│       ├── assignments/
│       │   ├── <assignment_id>/      # as-received handout, exactly as given
│       │   └── <assignment_id>.toml  # optional per-assignment overrides
│       ├── reference_solutions/
│       │   └── <assignment_id>/      # instructor/oracle solution
│       └── rubrics/
│           └── <assignment_id>/
│               ├── <name>.md         # default.md; variants are new files
│               └── source/           # professor's standalone rubric, verbatim
├── submissions/                # real student submissions, normalized by ingest
│   └── <course_id>/            #   (see "Submission ingest" below)
│       ├── ingest-record.json  #   receipt written by `aat ingest-submissions`
│       └── <student_id>/<assignment_id>/
├── tables/                     # rosters, grade exports, identity mappings
│   └── <course_id>/
│       ├── students.csv        #   pseudonym table (the identity mapping)
│       ├── submissions.csv     #   per-submission ingest bookkeeping
│       └── gradescope-summaries/  # grade pages split off graded-copy PDFs
├── tasks/                      # materialized Harbor task inputs, per job
│   └── <utc>__<config>__<hash8>/
├── solving/                    # solve jobs (Harbor job dirs + aat-run.json)
│   └── <utc>__<config>__<hash8>/
├── grading/                    # grading jobs, any submission source
│   └── <utc>__<config>__<hash8>/
├── analysis/                   # derived tables and reports, regenerable
└── scratch/                    # disposable working space
```

Notes:

- `raw/<course_id>/` is the course's materials exactly as collected —
  any shape, any format. It is read-only from the moment it is dumped:
  intake reads it and writes `courses/<course_id>/`, and keeping the
  dump means every extracted fact has a checkable source and intake can
  be re-run. The intake procedure is
  [course-intake.md](course-intake.md).
- `intake-record.json` is the receipt `aat intake` writes after a
  successful agent run — never written by the agent itself. Its
  `raw_sha256` (the hash of the raw dump at processing time) is
  intake's doneness: a dump whose current hash differs is unprocessed
  again, so new material triggers an incremental pass. The rest —
  prompt hash, model, effort, command, log path — is provenance only:
  intake output is human-reviewed, so a prompt or model change never
  invalidates a processed course. A course tree without a receipt is
  treated as hand-built and skipped unless forced; a receipt that
  exists but does not parse counts as unprocessed, so the next run
  performs an incremental pass and rewrites it. Intake run logs are
  teed to `scratch/intake/` (disposable like everything in
  `scratch/`).
- `courses/` content is **frozen at first use**: an artifact (an
  assignment directory, rubric file, rubric `source/` directory, or
  reference solution) becomes
  immutable once its hash is recorded in any job's run record, because
  results reference it by that hash. Until then it is a draft and may
  be edited freely — intake output is reviewed and corrected before
  anything runs against it. `course.toml`, `intake-notes.md`, and
  `syllabus/` are never hashed into any identity, so they may be
  amended at any time; amending the `environment` default changes
  per-item identities only through the resolved Dockerfile template it
  selects.
- `raw-submissions/<course_id>/` holds the LMS submission export zips
  exactly as downloaded — read-only from the moment they are dumped,
  like `raw/`. `aat ingest-submissions` reads them and writes the
  normalized `submissions/<course_id>/` tree and the
  `tables/<course_id>/` bookkeeping (the "Submission ingest" section
  below). Keeping the zips verbatim means every ingested file has a
  checkable source, superseded uploads stay recoverable, and ingest can
  be re-run.
- Transcripts and trajectories under `solving/` and `grading/` are data, not
  logs: they embed full assignment content and possibly student text.
- `tables/` holds the only mapping between real identities and anonymized
  IDs; it never leaves the data root.
- Student ids beginning with an underscore (e.g. `_reference`,
  `_irrelevant`) are reserved for the grader checks' known submissions
  (docs/design.md, results contract); grade statistics exclude them and
  report them separately.
- `tasks/` holds the byte-deterministic materialized task directories,
  one subdirectory per job, named like the job directory. Tasks live
  outside the job directories because Harbor's resume deletes any
  job-directory subdirectory without a per-trial result file as a
  stale trial; `harbor-job.json` references them by absolute path.
- `analysis/` holds only derived outputs: statistics tables and
  reports. Everything in it is regenerable from `solving/`, `grading/`,
  and `tables/`; each report invocation writes one timestamped
  subdirectory containing its tables, Markdown report, and provenance
  (toolkit version, config identities, job directories consumed, and
  the bootstrap seed). It is a cache, never a source of truth.
- Solver licenses (e.g. a Gurobi WLS license file) are credentials:
  they live in a maintainer-controlled path outside both repositories
  and are injected into containers at run time, never baked into
  images or committed. `aat solve` accepts a
  host license path through `--gurobi-license-file` or
  `AAT_GUROBI_LICENSE_FILE`, only when every selected assignment uses
  the `optimization` environment. Harbor mounts that file read-only at
  `/opt/gurobi/gurobi.lic`. The generated job config records the host
  path and mount properties, never the file bytes or individual WLS
  values.

## Course content contract

- `assignments/<assignment_id>/` is the handout exactly as a student
  would receive it: no injected metadata, no prompt, no statement file
  singled out. The solve-task materializer copies the whole directory
  into the task at `/app/assignment`.
- `<assignment_id>.toml`, beside the assignment directory, is an
  optional sidecar for per-assignment settings; its only key today is
  `environment`, the flavor override. Absent means course defaults
  apply.
- `course.toml` holds the course record and the assessment registry,
  parsed by `src/agentic_assessment_toolkit/course.py` and checked by
  `aat check-course`. The rule for what goes where: settings that
  change how tasks run (the environment flavor, future resource
  overrides) live in `course.toml`'s `[course]` table or the
  per-assignment sidecar; facts that describe the course (weights,
  policies, dates) live in the registry. The pipeline consumes only the
  environment default; every other field is informational until a
  consumer is deliberately added. The pipeline still loads
  `course.toml` through the same strict parser, so a schema-invalid
  file fails `aat solve` and `aat grade` planning immediately — one
  parser, one set of rules; `aat check-course` gives the detailed
  report. A fact the materials do not state is
  left **absent** — never a sentinel value — and noted in
  `intake-notes.md`.

  The `[course]` table: `title`, `institution`, `term`, and
  `environment` — the default environment template flavor (e.g.
  `data-science`, `optimization`). The sidecar overrides the
  environment per assignment; if neither names one, materialization
  fails with a clear error. The course id is the directory name alone
  (recommended shape `<institution>_<course>_<term>`, e.g.
  `PU_CHE456_F2025`); no consumer ever parses it, which is why
  `institution` and `term` are explicit fields.

  The `[[assessments]]` registry: one entry per syllabus assessment —
  including exams, presentations, and attendance that never get an
  assignment directory — so weights sum to 100 and the coverage of the
  final grade is computable. Fields, all optional except `id`:

  - `id` — unique, no whitespace or `/`; for assessments with
    materials it equals the assignment directory name. Recommended
    style: uppercase with zero-padded numbers (`HW01`, `PSO03`,
    `EXAM1`, `MIDTERM`, `FINAL`, `PROJECT`), so listings sort
    naturally.
  - `title` — the human label from the source materials (`"HW 4 —
    Regression"`).
  - `type` — `homework | exam | practice | project | attendance |
    other`.
  - `scope` — `take_home | online_exam | in_person_exam | presentation
    | in_person`.
  - `weight_pct` — this assessment's percent of the final grade
    (>= 0). When every entry has one, the sum must be 100 (checked
    with tolerance 0.01); how a category rule was split into
    per-assessment numbers is recorded in `intake-notes.md`, and the
    syllabus under `syllabus/` remains the authority.
  - `category` — free label tying entries to the syllabus's grading
    category (`"homework"`).
  - `ai_policy` — what the course permits: `allowed | not_allowed |
    not_applicable`.
  - `ai_use_possible` — boolean: whether AI use was physically
    feasible, independent of permission (an online exam may forbid AI
    without preventing it; an in-person exam prevents it).
  - `due` — TOML date.
  - `rubric_provenance` — `transcribed | authored`: how
    `rubrics/<id>/default.md` got its point split — transcribed from a
    split the materials state, or authored by the intake agent when no
    materials state one. Present exactly when the rubric exists: set
    without a rubric is a contract violation, as is `authored`
    alongside a `rubrics/<id>/source/` professor rubric; a rubric
    without the field is a completeness gap.
  - `excluded` — non-empty reason why this assessment has no
    assignment directory and never will (not codeable, materials
    lost). An entry with both an `excluded` reason and a directory is
    a contract violation; an entry with neither is a completeness gap.
- `syllabus/` holds the syllabus file(s) copied verbatim from the raw
  dump — the stored source for every registry fact, at a fixed
  location so no pointer field is needed. The solve materializer reads
  only `assignments/`, so syllabus content never reaches a solver or
  grader.
- `intake-notes.md` is intake's review aid, surfaced by
  `aat check-course`: the sources used, every judgment call (id
  assignment, file association, weight arithmetic, environment choice
  and any environment gap — packages the course needs that no flavor
  carries, left for the maintainer to fold into the templates, rubric
  transcription), everything intake looked for and could not find, and
  open questions. It is read by the maintainer, never by the pipeline,
  and is not a source of truth.
- `reference_solutions/<assignment_id>/` and
  `rubrics/<assignment_id>/` share the assignment's id. Keeping them in
  separate top-level trees — never inside the assignment directory —
  makes it structurally impossible for the solve-task materializer to
  leak grading material to the solver, which only ever reads
  `assignments/`.
- When the instructor materials contain no worked solution for a
  material-backed assignment, `reference_solutions/<assignment_id>/`
  holds a guidance note (`README.md`) saying so and directing the
  grader to establish correctness from the rubric, the submission's
  own derivations, and internal consistency checks. A copy of the
  assignment statement is never a reference solution, and nobody
  authors a worked solution to fill the gap: an invented oracle is
  worse than an absent one.
- Rubrics are Markdown files named within
  `rubrics/<assignment_id>/`; the default is `default.md`. A rubric no
  job has used yet is a draft and may be edited in place; once used it
  is frozen like every other artifact, and a revision is a new file
  (e.g. `strict-v2.md`) selected by name in a grading config —
  preserving immutability and hash-based provenance. A rubric is a
  **detailed grading document**, not a bare criterion list: each
  criterion line is followed by prose stating what earns full,
  partial, and zero credit, carried from the professor's materials
  when they say and drafted when they do not. Only the criterion
  lines are parsed; the prose is read by the grader.
- `rubrics/<assignment_id>/source/`, when present, holds the
  professor's standalone rubric document(s) verbatim (a rubric PDF or
  grading-scheme handout — distinct from schemes embedded in the
  assignment or reference files, which already reach the grader
  through those directories). The grading task presents it at
  `/app/rubric_source` as transcription context; `rubric.md` remains
  the sole authority on criteria and points, and rubric fidelity is
  measured against it alone. Most assignments have no standalone
  rubric document.
- Every assignment that will be graded must have a rubric; grading
  fails at materialization without one (docs/design.md, decision 5).
  A rubric enumerates its criteria, each with a stable id, a title,
  its max points, and an explicit bonus marking — ordinary
  human-readable Markdown, but each criterion is one bullet line in a
  fixed format, parsed and enforced at materialization
  (`src/agentic_assessment_toolkit/rubric.py`):

  ```text
  - `<id>` (<points> point[s][, bonus]): <title>
  ```

  For example:

  ```markdown
  - `slope` (8 points): the reported slope equals 2.
  - `plot` (1 point, bonus): a plot of the fit is included.
  ```

  Any line whose stripped form starts with a dash, a space, and a
  backtick must match this format; every other line is free prose and
  is ignored. The id contains no whitespace and is unique within the
  rubric; the points are a number greater than zero; at least one
  criterion is not a bonus. Stable criterion ids are what make
  per-criterion statistics comparable across repeated gradings.
- Every material-backed assignment leaves course intake with a
  `default.md` ([course-intake.md](course-intake.md)): **transcribed**
  where the materials state a point split, **authored** by the intake
  agent from the assignment and reference solution where neither the
  student-facing nor the instructor materials state one. Authored
  rubrics total exactly 100 integer points, contain no bonus
  criteria, and follow the handout's own problem structure. The
  registry records which mode produced each rubric
  (`rubric_provenance`, above), and the intake audit table carries the
  same label with the evidence that no point split exists, so the
  reviewer knows the split is the
  agent's judgment. A rubric for anything intake did not cover is
  drafted the same way — with an agent (any interface) from the
  assignment and reference solution — then
  reviewed. A rubric freezes at first grading use, and the grader
  checks (reference near full marks, irrelevant near zero) double as a
  sanity check on the rubric itself.
- Rubrics enumerate academic content only. Administrative
  requirements — a name or identifier on the work, signatures, honor
  affirmations or integrity statements, submission formalities such as
  boxing final answers, lateness penalties, escalation to the
  instructor — become neither criteria nor rubric prose, even when the
  professor's materials assign them points or withhold grading over
  them ("an unnamed page is not graded"). The professor's statement
  stays available verbatim in the handout and in `source/`, and each
  exclusion is recorded with its citation in `intake-notes.md`. The
  grader mirrors this split: it never scores administrative
  compliance, and notes visible non-compliance in its overall comment
  so course staff can apply course policy. Requirements about the
  academic work itself — shown work, stated assumptions, required
  derivations — are not administrative and stay in the rubric.

## Submission ingest

`aat ingest-submissions` converts `raw-submissions/<course_id>/` into
the normalized submissions tree and identity tables. It is
deterministic code, never an agent: LMS exports are uniformly
structured, and real student identities must be pseudonymized before
anything reaches an LLM — grading tasks and their stored transcripts
must only ever see pseudonym ids. The command is naturally incremental:
its receipt (`submissions/<course_id>/ingest-record.json`) records the
hash of the whole raw dump directory — export zips, the optional
`manifest.toml`, and any other files kept beside them — and a course
whose current hash differs is unprocessed again, so new exports or a
manifest fix trigger a re-run that recomputes the course from the raw
zips.

**Zip-to-assignment matching.** Each export zip maps to one assignment
id. The zip filename is parsed for a known series word — `homework`
(or `hw`), `pso` (or `problem set`), `exam` — plus a number, and
matched against the course's known assignment ids (assignment
directories and registry entries), ignoring zero padding — `hw5.zip`
matches `HW05`. Anything unparseable or ambiguous, including series
words the parser does not know (`Lab 3.zip`), is a hard error naming
the zip; the fix is one line in `manifest.toml`. Non-zip files in the
dump (a grade-export CSV kept beside the zips, say) are ignored by
processing but still count toward the dump hash, so adding one makes
the course pending again:

```toml
[zips]
"oddly named export.zip" = "HW05"

[identities]
"366491495" = "Ada Lovelace"   # Gradescope submission id → display name
"366856372" = "S014"           # or an existing student id
```

**Adapters.** The zip's internal layout selects the adapter; an
unrecognized layout is a hard error, never a guess.

- **Brightspace** — top-level folders named
  `<person>-<assignment> - <username> <Display Name> - <date time>`,
  one per upload. All of a student's uploads for an assignment merge
  into one effective submission: the union of all uploads keyed by
  relative path, where a later upload's file with exactly the same
  path supersedes the earlier version. Nothing else is ever discarded
  — same file type never means same role, so a later supplementary PDF
  must never erase an earlier solution PDF. Any multi-upload merge is
  flagged `merged_uploads`; every supersession is additionally
  recorded (`replaced_files`, with the superseded upload's timestamp),
  byte-identical re-uploads deduplicate (`duplicate_reupload`), and a
  merged submission holding same-type solution files (`.pdf`,
  `.ipynb`) from different uploads gets the advisory
  `possible_stale_solution` flag — a renamed re-upload a human should
  eyeball. A student whose latest upload carries a different username
  or display name than the students table is flagged
  `identity_changed` (the table keeps the first-seen identity).
  Root-level export bookkeeping files (e.g. `index.html`) are
  ignored.
- **Gradescope** — `assignment_<n>_export/` holding one graded-copy
  PDF per submission, named by numeric submission id. Each PDF is
  grade-summary pages followed by submission pages, every submission
  page headed by a question-assignment banner line; the PDF is split
  at the first banner page, and only the submission pages are written
  (as `submission.pdf`) into the submissions tree — the grader must
  never see the professor's scores. The summary pages are stored under
  `tables/<course_id>/gradescope-summaries/<assignment_id>/<student_id>.pdf`:
  they carry the professor's per-question grading and feed the planned
  professor-grade comparison ([roadmap](roadmap.md)). A PDF with no
  banner page is skipped and flagged (`no_split_marker`), never cut by
  guesswork; a PDF that fails to parse at all is skipped as
  `unreadable_pdf` rather than aborting the course; and when two PDFs
  of one assignment resolve to the same student, the second is skipped
  as `duplicate_student`. Gradescope exports carry no submission
  timestamp, so their `submitted_at` stays empty (Brightspace upload
  folders provide one).

**Identity.** `tables/<course_id>/students.csv` is the pseudonym
table: the mapping between real identities and pseudonym ids (real
names also remain inside the raw export zips and the stored Gradescope
grade summaries — which is part of why `tables/` and
`raw-submissions/` never leave the data root). Brightspace students
are keyed by their LMS person id; Gradescope PDFs carry only a display
name on the first summary page, matched case-insensitively against
the table, with `manifest.toml` `[identities]` as the explicit
override (also the fix for the occasional PDF with no extractable
name, flagged `identity_unresolved`, and for ambiguous names, flagged
`ambiguous_identity`). An S-id override resolves to that student
directly — it works even when several students share a name, which is
exactly the ambiguity it exists to settle — and is flagged
`identity_from_manifest`; an override naming a nonexistent S-id is
skipped as `unknown_student_id`. Unmatched names become new students.
Ids are `S001`-style, assigned deterministically and never renumbered:
re-running ingest extends the table, it never rewrites existing rows.
Unresolved submissions are skipped — listed for review, never guessed.
One accepted limitation of name matching: two different people who
share a display name and appear only in Gradescope exports are merged
into one pseudonym when they never collide on the same assignment
(a same-assignment collision is caught as `duplicate_student`). The
S-id override is the remedy when a roster or grades CSV reveals such
a pair.

**Freeze rule.** A submission directory referenced by any grading run
record is frozen: the grading per-item identity deliberately excludes
the submission bytes (the submission is the measured object, not part
of the judge), so a silent rewrite would never trigger a regrade.
When re-ingest computes different content for a frozen directory, the
directory is left untouched and the row is flagged
(`frozen_submission_changed`, status `frozen`); the human decides — to
accept the new content, delete the submission directory and re-run
with `--force` (the raw dump is unchanged, so a plain re-run would
skip the course), then regrade: the item still counts as done, so
regrading the new bytes takes `aat grade --force`.

**Review.** `tables/<course_id>/submissions.csv` records every
(assignment, student) pair — status `ready`, `skipped`, `frozen`, or
`missing` (a known student with no submission for an ingested
assignment) — with upload counts, timestamps where the source provides
them, flags, and superseded files. The command prints the flagged rows
after each course and exits nonzero while any submission is skipped or
frozen — including on later runs that skip an unchanged course: the
receipt records the outcome counts, and unresolved rows are reported
(and keep the exit nonzero) until a fix changes the dump or `--force`
reprocesses it. The review loop is: read the summary, fix
`manifest.toml` or resolve the frozen conflict, re-run until clean.
Flagged multi-upload merges are drafts like everything else
pre-freeze: hand-prune a submission directory before grading if the
merge kept a stale version.

## Job directories and run records

Each `aat solve` or `aat grade` invocation creates one job directory —
`<utc>__<config>__<hash8>/` (with a rare `-N`
suffix on same-second collisions) — under `solving/` (solve jobs) or
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
repeats, maximum concurrent trials, executed flag, requested items with
their per-item identities, explicit course and assignment ids, explicit
lineage (submission source, student id for student grading items, solve
job and trial for solve-derived grading items), config identity, and
input hashes (assignment, prompt, environment template, verifier,
rubric, rubric source, submission, reference solution, grading schema —
as applicable). When present, `harbor-job.json` records the Gurobi
license's host path and read-only container mount. Doneness of an item
under a config is derived from these
directories and Harbor's per-trial result files; there is no separate
bookkeeping state. A solve item is done when a verified trial — one
whose verifier recorded a reward — exists (a 0-reward contract failure
is a countable outcome); a grading item is done only when such a trial produced a valid
grading result — failed gradings are regraded by the next incremental
run.

Because the layout is flat, `harbor view` works on the shared `solving/`
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

- `.gitignore` patterns for data-shaped directories (`data/`, `solving/`, `runs/`,
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
