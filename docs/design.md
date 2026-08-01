# Agentic Assessment Toolkit Design

This is the decision and design layer between the vision in
[brief.md](brief.md) and implementation. [research.md](research.md) is a
frozen research snapshot (2026-07-30) that surveys the landscape; this
document records what was actually decided and what is being built. When the
two disagree, this document wins. This document is maintained in place, not
as a changelog: superseded decisions are rewritten, not preserved as
history.

## Decisions

Each entry is a commitment, with a one-line rationale. Alternatives and full
analysis are in research.md.

1. **Harbor is the foundation for solving and grading, used natively.**
   Harbor's task directories, job outputs, ATIF trajectories, and
   viewer are the formats of this project, for solve jobs and grading jobs
   alike. No abstraction seam or backend interface is built around Harbor:
   durable assets are data (plain directories, files, recorded hashes), so
   portability comes from data conventions, not code. If a migration is ever
   needed, it is a one-time conversion script written then.
2. **The toolkit stays thin.** It contains only domain code no framework can
   provide: the task materializers (assignments into solve tasks,
   submissions into grading tasks), solver and grader prompt templates,
   environment (Dockerfile) templates, the two generic contract verifiers,
   the grading output schema, results
   loading and statistics, the course record and assessment registry
   conventions with their intake brief and checker, and the thin
   `aat solve` / `aat grade` / `aat report` / `aat check-course`
   commands. It does not
   implement an agent runner, sandbox framework, run orchestrator, model
   abstraction, transcript schema, experiment database, or results viewer:
   Harbor does all orchestration; the toolkit constructs one command line
   and records what it ran. Simplicity is a standing preference, not a hard
   budget.
3. **Subscription-backed execution.** Agents and graders run through existing
   Codex CLI / Claude Code / Gemini CLI subscriptions, not per-token API
   billing. Harbor supports this natively, and the same cached-authentication
   mechanism serves both solve jobs and grading jobs.
4. **Codex-first implementation.** The first complete benchmark and
   grading-assistant pipelines use Codex for both assignment solving and
   grading. Benchmark core, grading, validation, reporting, and hardening
   are completed for the Codex stack before Claude Code, Gemini CLI, or
   other agents are integrated. Solver and grader remain separate,
   independently configured jobs. Multi-agent comparison remains a long-term
   goal, not an acceptance criterion for the initial vertical slices.
5. **One grading pipeline, two submission sources.** Grading is a Harbor job
   whose tasks are "grade this submission directory against this reference
   solution and rubric, with the assignment handout alongside." A submission directory can be a Harbor solve
   artifact or a real student folder; the machinery is identical and the
   only difference is which directory is materialized into the grading
   task. Repeated grading for variance estimates is Harbor's native
   repeated attempts (`n_attempts`). Every graded assignment has a
   rubric: a Markdown file that enumerates the criteria — each with a
   stable id, a title, its max points, and an explicit bonus marking —
   and is the authority on the point split. Criteria lines follow the
   fixed format in [data-conventions.md](data-conventions.md), parsed
   and enforced at materialization. Grading never starts
   without one: a missing rubric fails at materialization, and the fix
   is to author the rubric first (the manual procedure in
   [data-conventions.md](data-conventions.md)). Stable criterion ids
   are what make per-criterion statistics well defined across repeated
   gradings.
6. **Grades come from the grader's artifacts, not from verifier scoring.**
   Each stage has exactly one generic contract verifier, reused across all
   tasks; per-assignment test code is never written. The solve verifier is
   a 0/1 output-contract check, agnostic to what the deliverable is;
   evidence-completeness expectations live in the solver prompt's output
   contract and are judged by the grader, not checked mechanically. The
   grading verifier validates the grading output structurally and derives
   all scores in code from the criteria. The full rules — the output
   contract, the sums self-check, and the derived percentages — are
   specified once, in the contracts sections below (solve task layout,
   grading output schema, reward semantics).
7. **Regrading is re-running the grader.** Solving and grading are separable
   in time because grading consumes only stored artifacts; a revised rubric
   or grader prompt means a new grading job over the same submission
   directories. No dependency on `harbor job regrade`, no post-release
   Harbor pinning, no separate verifier environment.
8. **Judge validity without deterministic tests.** Benchmark and
   grading-assistant scores are LLM-grader scores under one frozen grader
   configuration (prompt, model, effort, rubric), versioned with the
   results, not proxies for professor grades. Validity comes from three
   grader checks — the reference solution grades at or near full marks,
   an irrelevant submission grades near zero, and repeated gradings of
   one submission agree — plus repeated grading for distributions and
   explicit failure accounting. The checks are a documented manual
   procedure over the ordinary grading pipeline, not toolkit machinery
   (see the results contract). Calibration against trusted human
   grading is deferred until a trustworthy human-graded corpus exists.
9. **Full agent capability by default; minimal trust model, accepted and
   documented.** Agents run with their normal toolset and public network
   access in both stages, because assignments and grading may legitimately
   require web research, downloads, or checking cited sources. The
   effective network policy is an experiment condition recorded with every
   job; any restriction (offline solving, provider-only egress) is a
   deliberate, recorded choice made later, never a default. The grader is
   instructed never to execute submission code; grading is static
   inspection by prompt rule, which is soft enforcement, accepted
   deliberately. Grading containers are disposable and mount nothing beyond
   the materialized task. The residual prompt-injection risk — including a
   prompt-injected grader leaking the in-container subscription credential
   — is accepted at current scale (personal research over historical data)
   and must be revisited before any adversarial or institutional
   deployment. No injection scanners, credential brokers, or sanitized
   evidence bundles are in scope.
10. **Strict code–data separation.** The repository is always publishable;
    all real data lives in an external data root. Specified in
    [data-conventions.md](data-conventions.md).
11. **Grading assistance is policy-agnostic.** The toolkit produces grades,
    per-criterion scores, and evidence. How they are used relative to human
    grading is university policy and out of scope.
12. **No second MVP.** The reference repositories are the proof of value; the
    durable system is built directly — no intermediate throwaway.
13. **Live validation is outside repository work.** All live execution —
    Harbor runs, Docker, subscription-authenticated agent or grader calls,
    network access to model providers — is performed by the maintainer
    outside this repository. Repository work never attempts live runs and
    never depends on their results.
14. **Course intake is an agent-assisted manual procedure with a
    deterministic checker.** Raw course materials are dumped into
    `raw/<course_id>/` in the data root; a maintainer-run agent session
    (plain `codex exec` or similar — never Harbor: intake is trusted,
    interactive, one-time authoring, not a measured experiment) follows
    the checked-in brief in [course-intake.md](course-intake.md) to
    produce `courses/<course_id>/`, including the course record, the
    assessment registry, and rubric drafts; the read-only
    `aat check-course` command then reports contract violations,
    completeness gaps, and the intake notes until the course is clean.
    The toolkit ships the brief and the checker and never launches the
    agent (decision 13). Facts the materials do not state are left
    absent — never sentinel values — and surfaced by the checker.
    Student-submission ingest is out of intake's scope and deferred.

## Vocabulary

One concept, one name (AGENTS.md). These are the toolkit's terms, used
identically in code, documentation, and output.

- **Data root** — the single directory outside the repository holding
  all real course data, submissions, and results
  ([data-conventions.md](data-conventions.md)).
- **Item** — one unit of work with its own doneness: a
  (course, assignment) pair to solve, or one submission to grade.
- **Task** — the Harbor task directory produced from an item:
  instruction, environment, inputs, and verifier.
- **Materialize** — write the concrete, byte-deterministic Harbor task
  directory derived from data-root inputs and package templates.
- **Trial** — one Harbor attempt at a task; `--repeats N` draws N
  trials.
- **Job** — one `aat` invocation's Harbor run over its materialized
  tasks; its directory holds Harbor's output plus the run record.
- **Run record** — `aat-run.json`: what a job ran — versions, config,
  command, items, identities, input hashes.
- **Stage** — which pipeline something belongs to: `solve` or `grade`.
- **Experiment config** — a TOML file under `configs/` saying how to
  run (agent, model, effort, prompt, rubric), never what to run on.
- **Config identity** — the hash of the experiment config plus
  everything it references (prompt, verifier, rendered task.toml);
  labels and segregates results.
- **Per-item identity** — the config identity folded with an item's
  resolved inputs (the environment template; for grading also the
  rubric bytes and the assignment directory hash); paired with the
  item id, it is the doneness and pooling key.
- **Verified trial** — a trial whose verifier recorded a reward,
  whatever the reward's value (see denominator policy).
- **Done** — an item needing no further work under a config: a verified
  trial exists, and for grading items it holds a valid grading result.
- **Environment flavor** — a capability-named Dockerfile template:
  `data-science`, `optimization`, `scientific-python`, or `grading`.
- **Base criteria** — the non-bonus rubric criteria; `base_points` and
  `base_max` are their sums, the denominator of every percentage.
- **Sidecar** — the optional `<assignment_id>.toml` beside an
  assignment directory carrying per-assignment settings.
- **Assessment registry** — the `[[assessments]]` array in
  `course.toml`: one entry per syllabus assessment (weights, type,
  AI policy), including assessments that never get materials, so
  grade coverage is computable
  ([data-conventions.md](data-conventions.md)).
- **Intake** — the manual, agent-assisted procedure that converts
  `raw/<course_id>/` into `courses/<course_id>/`
  ([course-intake.md](course-intake.md)); `aat check-course` is its
  deterministic reviewer.
- **Golden fixture** — a committed byte-exact expected task tree under
  `tests/fixtures/golden/`, regenerated only deliberately.

## What goes where

```text
Harbor owns                          Toolkit owns
-----------                          ------------
sandboxed execution (solve + grade)  assignment/dataset import conventions
agent adapters (Codex, Claude, ...)  grading-task materializer
trials, jobs, -k repeats, retries    solver + grader prompt templates
concurrency management               environment (Dockerfile) templates
artifact + transcript capture        generic contract verifiers (2)
ATIF trajectories                    grading output schema
results viewer                       results loading into tidy tables
                                     statistical metrics.py
```

### Benchmark pipeline

```text
data root: courses/<id>/assignments/<n>/     (frozen at first use, hashed)
        ↓  solve-task materializer + solver prompt (experiment config)
Harbor solve job: agent solves in isolated container
  (public network, credential inside, trusted professor-authored task)
        ↓  generic solve verifier: 0/1 contract check
artifacts, raw transcript, ATIF trajectory,
status, duration, token usage
        ↓  grading-task materializer (same path as student grading)
Harbor grading job                            → see grading pipeline below
        ↓
results.py + metrics.py: score distributions, repeat variance, bootstrap CIs
clustered by assignment, explicit failure accounting
```

### Grading pipeline

```text
submission directory
  (Harbor solve artifact OR submissions/<course>/<student>/<assignment>/)
        ↓  grading-task materializer: assignment + submission
           + reference solution + rubric + grader instruction (static
           inspection; every input is read as data, never executed)
Harbor grading job: grader agent in isolated container
  (public network, -k repeats for variance)
        ↓  generic grading verifier: validate grading_result.json,
           derive and surface score_pct as the reward
grading_result.json + per-problem Markdown justification
        ↓
results.py + metrics.py: grade distributions, repeat stability,
explicit failure accounting
(professor-grade comparison: deferred, see the roadmap)
```

## Contracts

Concrete specifications. Nothing here is ported
from the pilots or the reference repositories; both informed these
choices as evidence only.

### Grading output schema

The grader writes two required files to `/app/grading_output/`:

- `grading_result.json` — the machine-readable judgment.
- `justification.md` — the per-problem written justification
  (non-empty).

Extra scratch files in `grading_output/` are tolerated and preserved as
artifacts; the contract is that the two required files are present and
valid.

`grading_result.json` fields, all authored by the grader:

- `schema_version` — integer, currently `1`.
- `criteria` — non-empty list; each entry has `id` (unique string),
  `title`, `max_points` (> 0), `points` (`0 <= points <= max_points`),
  `evidence` (non-empty string citing what in the submission justifies
  the score), and optional `bonus` (boolean, default false). Criteria
  without the bonus flag are the **base criteria** — the ordinary,
  non-optional part of the grade that bonus points are added on top of.
  At least one criterion must be base, so `base_max > 0` and the
  derived percentages are always well defined.
- `base_points`, `base_max` — sums over base criteria (self-check;
  see below).
- `bonus_points`, `bonus_max` — sums over bonus criteria, 0 when none
  (self-check).
- `overall_comment` — short free-text summary.

The sums are the grader's self-check, never the source of truth: the
generic grading verifier always recomputes every sum from the criteria,
and the computed sums are authoritative everywhere — score derivation,
the reward file, and downstream statistics. An authored sum that is
missing, non-numeric, or disagrees with the computed value is recorded
as a sums-consistency flag (with the authored and computed values) in
the verifier details, not failed: an arithmetic slip does not void an
otherwise sound judgment, and the flag's rate per grader configuration
is itself a judge-quality signal consumed by the statistics layer.
Structural violations remain hard contract failures: missing or
malformed required fields, duplicate criterion ids, out-of-range or
non-finite points, missing or all-bonus criteria, and empty evidence.
Percentages are never authored by the grader — all summation and
division policy lives in code (`derive_scores` in `grading_schema.py`,
computing from the criteria), so a scoring-convention change never
invalidates stored grading results. Provenance (submission, reference, rubric, and
config hashes) is recorded by the materializer and the run record,
never authored by the LLM: the grader's required output stays minimal
to reduce parse failures.

### Reward semantics

From a validated grading result the verifier derives two percentages,
using the sums it computed from the criteria:

- `score_pct` — `100 * (base_points + bonus_points) / base_max`, the
  gradebook score: base and bonus criteria use the same point scale,
  and earned bonus counts above the base maximum. It can exceed 100;
  for example, 10/10 base plus 1 bonus point is 110, while 90/90 base
  plus 10 bonus points is approximately 111.11.
- `base_pct` — `100 * base_points / base_max`, a 0–100 scale over
  base criteria only, comparable across assignments regardless of
  bonus availability.

`score_pct` is surfaced as the primary Harbor reward so grades appear
in the viewer; `base_pct` is written beside it in the verifier's
reward file and details. Concretely, the reward file is a flat JSON
object of numbers: `{"reward": <score_pct>, "base_pct":
<base_pct>}` on a valid result, and `{"reward": 0.0}` alone on a
contract violation — so the presence of the `base_pct` key in a
trial's recorded rewards is the machine-readable mark of a valid
grading result, and is exactly what grading doneness reads (see run
records and idempotence). Any contract violation yields reward 0.0 —
but such a trial is a failed measurement, not a harsh grade: it never
counts as graded for doneness (see run records and idempotence), and
the statistics layer counts it as a failure category, never as a zero
score. Downstream aggregation (per-student or per-agent totals, final course
scores, bonus caps) reads the per-criterion judgments in
`grading_result.json`, so it never depends on the reward convention.
The solve verifier's reward remains the 0/1 output-contract check.

### Solve task layout and verifier

A materialized solve task presents the as-received assignment directory
at `/app/assignment`, whole and unmodified; the solver prompt directs
the agent to read it and produce its complete solution in
`/app/submission`. No statement file is singled out and no
per-assignment metadata is injected: the agent reads the handout as a
student would.

At materialization time the toolkit writes a manifest of input file
hashes into the task (outside `/app/submission`). The generic solve
verifier checks that `/app/submission` exists, is non-empty, contains
at least one file that is new or changed relative to the manifest, and
contains no bookkeeping entries. The bookkeeping blocklist is fixed and
documented in the verifier: `.git`, `__pycache__`,
`.ipynb_checkpoints`, `.DS_Store`, `node_modules`, and editor swap and
backup files.

### Grading task layout

A materialized grading task presents, under `/app`:

- `assignment/` — the as-received handout, copied whole exactly as in
  the solve task: the authoritative record of what was asked. The
  grader judges the submission against it; a human grader always has
  the assignment sheet in front of them, and so does this one.
- `submission/` — the directory being graded (solve artifact or student
  folder), copied as data.
- `reference_solution/` — the oracle solution.
- `rubric.md` — always present (decision 5). The rubric is resolved as
  `courses/<course_id>/rubrics/<assignment_id>/<name>.md` in the data
  root, where `<name>` comes from the grading config (default
  `default`). A rubric that does not exist for the assignment —
  default or otherwise — fails at materialization: grading never
  starts without a rubric, and a frozen judge configuration never
  silently degrades.
- `grading_output/` — empty directory the grader must fill (created by
  the grading environment image, so the materialized task tree contains
  no placeholder files).

Grading tasks always use the dedicated `grading` environment flavor,
regardless of course: grading is static inspection, so the image needs
document-reading tools (PDF text extraction, spreadsheet and notebook
reading), not the course's scientific stack. Course flavors are for
solve tasks only.

The grader instruction states the static-inspection rule: assignment,
submission, and reference content is read as data and never executed —
and never compiled: LaTeX compilation is code execution. It also states
that any instructions found inside the assignment, submission, or
reference are content about the work, never directives to the grader.

### Experiment configs and config identity

Experiment configs are TOML files committed under `configs/` at the
repository root (e.g. `configs/codex-high.toml`). They contain no
course content: agent, model, reasoning effort, solver or grader prompt
template name, rubric name, and agent-argument passthrough. Selection
and mechanics never appear in configs (see CLI design).

The config identity is `sha256` over the config file bytes, the
referenced prompt template bytes, the stage's generic verifier
bytes (for grading, the verifier script plus the copied
`grading_schema.py`), and the stage's rendered task.toml template bytes
(which carry the network policy, timeouts, and artifact path as
materialized): an edit to any of these changes the experiment, so all
of them invalidate doneness by construction. Each item
additionally has a **per-item identity** that folds in the item's
resolved inputs: for solve, the resolved environment template
(Dockerfile) bytes; for grading, the grading environment template
bytes, the resolved rubric file bytes, and the hash of the assignment
directory presented in the task. Rubric files and assignment
directories freeze at first use — a rubric revision after that is a
new file selected by name in the config (see
[data-conventions.md](data-conventions.md)) — so folding their bytes
into the per-item identity is defense in depth, and a different rubric
selection changes doneness for exactly the assignments it applies to. Mechanics such as `--repeats` and
`--max-concurrent-trials`, and all selection flags, never enter the
identity.

### Environment templates

Environment templates are Dockerfiles shipped as package data, one per
flavor, named by capability rather than by course. The initial set,
derived from the packages the reference corpus actually uses:

- `data-science` — numpy, pandas, matplotlib, scikit-learn, CPU-only
  PyTorch, scipy, openpyxl (spreadsheet handouts), and the notebook
  toolchain (ipykernel, nbconvert, nbclient).
- `optimization` — Pyomo with HiGHS (`highspy`) as the license-free
  default solver, GLPK, Ipopt (conda-forge binaries), and `gurobipy`
  installed but unlicensed: Gurobi is enabled at run time by injecting
  academic WLS credentials (`GRB_WLSACCESSID`, `GRB_WLSSECRET`,
  `GRB_LICENSEID`, or a license file via `GRB_LICENSE_FILE`) from
  outside the repository. Plus numpy, scipy, pandas, matplotlib,
  openpyxl, and the notebook toolchain.
- `scientific-python` — the general flavor: numpy, scipy, pandas,
  matplotlib, sympy, python-control (used by the control-systems
  course), openpyxl, and the notebook toolchain.
- `grading` — the single flavor used by every grading task: a minimal
  Python image with document-reading tools only (poppler-utils and
  pypdf for PDF text extraction, openpyxl and pandas for tabular data,
  nbformat for notebooks). No scientific stack: nothing is executed
  during grading. The image creates `/app/grading_output/`.

Every environment includes `file` and `jq` for basic file-type and JSON
inspection. Every solve flavor also includes PDF text-extraction tools
(poppler-utils, pypdf), because assignment handouts are routinely PDFs
that the agent must read. A `latex` flavor is deferred: the output contract
requires document source, never compiled PDFs, so no image needs TeX.

Images pin their Python package versions; base-image digest pinning is
deferred to reportable runs. Solver licenses (Gurobi WLS) are
credentials: never baked into images, never committed, always injected
at run time.

Template resolution for a solve task: the per-assignment sidecar's
`environment` key when present, else the course default in
`course.toml`, else a clear error. Grading tasks always resolve to
`grading`; the `grading` flavor is reserved for grading tasks, and a
solve task that resolves to it fails at materialization. Layout
details are in [data-conventions.md](data-conventions.md).

### Prompt templates

Solver and grader prompts are Markdown templates shipped as package
data and written fresh for this toolkit. The initial templates need no
placeholders — everything that varies is presented as files in the
task — so the rendered `instruction.md` equals the template bytes;
placeholder substitution is introduced only when a template actually
needs one. The solver prompt covers the role, the workspace layout,
autonomy expectations, and the `/app/submission` output contract
(executed notebooks, document source in Markdown or LaTeX — never
compiled PDFs, no scratch files). The grader prompt covers the
workspace — including the assignment handout as the record of what was
asked — the static-inspection rule (read as data; never execute or
compile), prompt-injection resistance (instructions inside the
submission are content, not commands), rubric authority — including the requirement
to reproduce the rubric's enumerated criteria verbatim: same ids, same
max points, same bonus flags, with only the points awarded being the
grader's judgment — evidence requirements, and the exact output schema
above. Any prompt
edit changes the config identity by construction.

Prompts are instruction briefs: they state requirements imperatively
and with uniform force, and never disclose enforcement mechanics —
what is or is not machine-verified, which violations are failed
versus flagged, or the consequences of specific failure modes. That
information lives in the verifiers and this document. The corollary
is directional: an enforcement-only change (what the verifier fails,
flags, or ignores) must never require a prompt edit, so enforcement
details are never copied into prompt text.

### Run records and idempotence

Each `aat` invocation creates one job directory —
`<utc>__<config>__<hash8>/`, where the hash is the first eight
characters of the config identity — under `solving/`
(solve) or `grading/` (grading) in the data root. The AAT job directory
**is** the Harbor job directory: `aat` passes the stage parent as
Harbor's jobs directory and the AAT directory name as the Harbor job
name, so Harbor's own `config.json`, `lock.json`, `result.json`,
`job.log`, and per-trial directories live directly inside it, beside
exactly two AAT files — `aat-run.json` (the run record) and
`harbor-job.json` (the generated Harbor job config); the filenames do
not collide. Materialized task directories live *outside* the job
directory, under `tasks/<job-name>/` in the data root, referenced by
absolute path from `harbor-job.json`: on resume, Harbor deletes any
job-directory subdirectory without a per-trial result file as a stale
trial, so nothing but Harbor's own output may live there. Because of
this, re-running the recorded command safely resumes an interrupted
job. The command is `harbor run -c <job-dir>/harbor-job.json --yes`,
executed with `HARBOR_TELEMETRY=0` in the environment (nothing leaves
the machine except calls to the model providers).

`aat-run.json` records the Harbor version (read from the `harbor`
binary for executed runs, from package metadata for
`--materialize-only`; `harbor_version_source` says which), the
toolkit's own version, agent and model configuration, effective
command line, requested items — each with its per-item identity,
explicit `course_id` and `assignment_id`, and explicit lineage: the
submission source (`student` or `solve-trial`) for grading items, the
`student_id` for student grading items, and the solve job and trial
names for solve-derived grading items — the config identity, and
input hashes (assignment, prompt, environment template, verifier,
rubric, submission, reference solution, grading schema — as
applicable). Item ids take three shapes: `<course>/<assignment>` for
solve items, `<course>/<student>/<assignment>` for student grading
items, and `<solve-job>/<trial>` for solve-derived grading items; the
explicit lineage fields exist so no consumer ever parses an item id.
On a rare same-second collision the job directory name gains a `-N`
suffix; a job's identity lives in `aat-run.json`, never in the
directory name.

Because the layout is flat, Harbor's viewer works at both levels:
`harbor view` on the shared `solving/` or `grading/` parent browses all
jobs of a stage, and the exact per-job `harbor view <job-directory>`
command printed by `aat` opens one job.

Doneness is stage-specific, read from Harbor's per-trial result file —
the same file Harbor's viewer consumes. That file is parsed as plain
JSON: this is the one deliberate coupling to Harbor's on-disk output
format and is accepted as such, while Harbor's internal Python API
(including its result models) stays unused. Doneness is keyed on
(item id, per-item identity); the config identity is embedded in the
per-item identity, which additionally folds in the item's environment
and rubric bytes. A **solve** item is done when some solve job records
a verified trial for it — one whose verifier recorded a reward,
including reward 0, because a solver that produced no acceptable
submission is a legitimate, countable outcome of the experiment; a late
exception recorded after the reward does not un-verify the trial, and the
statistics layer surfaces it as a flag. A **grading** item is done
only when such a trial additionally produced a *valid* grading result,
detected via the `base_pct` reward key (see reward semantics): an
invalid or missing `grading_result.json` is a failed measurement, not
a grade, so the item stays not-done and the next incremental run
regrades it automatically — no dedicated retry flag is needed. Failed
grading trials remain on disk as explicit outcomes. The doneness
check fails closed: a job whose recorded stage does not match the
requested stage contributes nothing.

Harbor-level retries stay at Harbor's default of zero: Harbor retries
overwrite the failed attempt in place, which would erase the
per-attempt history that explicit failure accounting depends on.
Re-running an `aat` command is the retry mechanism, and it accumulates
trials rather than overwriting them.

### Results, statistics, and reporting

The read side of the data conventions (roadmap stage 3). Everything
below is read-only over the data root, derives entirely from the files
specified above, and can be regenerated at any time. Files are the
source of truth; there is no results database. Derived outputs land
under `analysis/` in the data root (see
[data-conventions.md](data-conventions.md)) and are stamped with their
provenance: toolkit version, the config identities and job directories
consumed, and the parameters of the computation.

**Results loading (`results.py`).** One loader walks `solving/` and
`grading/`, joins each job's `aat-run.json` with Harbor's per-trial
`result.json` files and, for grading trials, the `grading_result.json`
artifact — Harbor mirrors each declared artifact's absolute container
path under the trial's `artifacts/` directory, so it is read from
`artifacts/app/grading_output/grading_result.json` — and returns two
tidy pandas tables:

- **Trials** — one row per trial: stage, course, assignment (from the
  record's explicit lineage fields), item id, config name and
  identity, item identity, model, reasoning effort, job and trial
  names, outcome category, the late-exception flag (an exception
  recorded after the verifier's reward), reward, `score_pct`,
  `base_pct`, the sums-consistency flag (recomputed from the
  artifact via `sums_report`, the same shared validation module the
  verifier uses), token counts (input, cached, output), reported cost
  when present, per-phase durations (environment setup, agent setup,
  agent execution, verification), and timestamps. Token and cost
  values a run did not report load as missing, never as zero — zero
  never means unknown. Token counts keep Harbor's semantics verbatim —
  the input count includes cached tokens, and multi-step trials sum
  their per-step agent contexts. Harbor's timestamps are not
  guaranteed timezone-aware and load as informational only; no
  statistic derives from them. Grading rows
  additionally carry the submission source (student folder or solve
  artifact) and student id, read from the run record's explicit
  lineage fields, plus the rubric name and hash; solve-derived rows
  also carry the solve job/trial lineage and the solver's config name,
  identity, and model, joined from the originating solve job's run
  record via the recorded solve job name — benchmark statistics group
  by the solver config identity, judge-quality statistics by the
  grading config identity. A graded trial whose stored
  `grading_result.json` is missing or unreadable at load time stays
  `completed` (and done) but gains a load-error flag and contributes
  no criteria rows; flagged counts appear in the failure accounting.
- **Criteria** — one row per (grading trial, criterion): id, title,
  points, max points, bonus flag. Per-criterion analysis aggregates
  over this table; totals are always recomputed from it, never read
  from the grader's authored sums.

**Outcome taxonomy.** Every trial maps to exactly one category:
`completed` (a valid measurement), `contract_failed` (solve: output
contract violated; grading: invalid or missing grading result),
`agent_error` (provider and API errors, authentication, refusals,
context or output limits), `infra_error` (environment build,
healthcheck, and sandbox failures), `timeout`, `cancelled`, and
`unknown` for unrecognized exception types. The mapping from Harbor's
exception class names to categories is a fixed table in code,
versioned with the toolkit — Harbor records only flat leaf class
names, so the grouping into families lives here. A trial whose
verifier recorded a reward is a measurement: it is categorized by its
verification outcome (`completed` or `contract_failed`) even when a
late exception was also recorded — the exception becomes the
late-exception flag, never the category. This matches doneness, so an
item can never be done while contributing zero measurements.

**Denominator policy.** A trial is *verified* when its verifier
recorded a reward (categories `completed` and `contract_failed`).
Solve contract pass rates use verified solve trials as the
denominator; grade statistics are computed over trials with a valid
grading result only. Every aggregate is reported alongside explicit
per-category counts and rates over all trials, so failures are never
silently dropped and a contract-violation reward of 0.0 never enters a
score mean.

**Statistics (`metrics.py`).** Trials pool by (item id, per-item
identity) across jobs — the same key as doneness, so pooling can never
merge trials whose rubric or environment differed. `base_pct` is the
primary comparison metric (0–100, comparable across assignments
regardless of bonus availability); `score_pct` is reported beside it
as the bonus-inclusive gradebook score.

Benchmark aggregation follows one fixed ladder, grouped per (solver
config identity, grading config identity) pair — one row per pair per
assignment and per course, so comparisons never mix solvers or judges:
grading repeats of a submission average to a per-solve-trial score,
solve trials average to a per-assignment score, and assignments
macro-average to the course score — each assignment weighs equally
regardless of how many trials it accumulated. An assignment with no
valid gradings under a config is excluded from the macro-mean, and
the report states coverage explicitly (e.g. "7 of 9 assignments"). Course-level confidence
intervals come from a percentile bootstrap that resamples assignments
(the cluster unit) with replacement and recomputes the ladder per
resample: 10,000 resamples, 95% level, default seed 42 (overridable
with `--seed`; the effective seed is always recorded in the report
provenance). An interval is emitted only when at least five
assignments have data; below that the report prints the
per-assignment means with an explicit too-few-clusters note. The
per-assignment means are always printed beside any interval: at
corpus scale they are the primary result and the interval is a
summary.

Judge quality is measured per grading configuration: the within-item
standard deviation and range of `base_pct` over repeated gradings,
aggregated as the mean within-item SD and the worst-case range;
per-criterion agreement, computed pairwise over each repeated item's
grading pairs and only over the criterion ids both gradings of a pair
share (exact-agreement rate and mean absolute points difference;
unshared ids surface through rubric fidelity, not agreement); and
three flag rates — sums consistency, late exception, and **rubric
fidelity**. A trial is rubric-faithful when its criterion id set,
per-id max points, and per-id bonus flags all match the rubric's
enumerated criteria; the points awarded are the grader's judgment and
never enter fidelity. The rubric's criteria are parsed per the fixed
grammar in [data-conventions.md](data-conventions.md): `metrics.py`
resolves the rubric from the course tree and verifies its bytes
against the hash recorded in the run record, and a trial whose rubric
is missing, changed, or unparseable is counted as unresolved rather
than rated. Failure rates per outcome category complete the set.

Grading-assistant statistics are descriptive only — per student and
assignment: the mean over valid gradings, the repeat SD (the
per-student uncertainty statement), the grading count, and flags;
per assignment: the class distribution over the per-student mean
grades — one value per student — as count, mean, median, SD, and
quartiles. The bootstrap belongs to benchmark aggregation, never to
individual grades. Harbor's built-in aggregation (means, binary-reward
pass@k) is not used: it counts errored trials in score means and
cannot express graded rewards. Pass@k is deferred until a pass
threshold on `score_pct` is actually needed and chosen.

**Grader checks (a manual procedure, not machinery).** Before trusting
a frozen grader configuration, run it through the ordinary pipeline on
known submissions: a copy of the reference solution placed in the
submissions tree as a pseudo-student must grade at or near full marks,
and a submission containing only an unrelated placeholder file must
grade near zero; `--repeats` on the same items measures whether
repeated gradings agree. Pseudo-student ids begin with an underscore
and carry their role in the prefix: ids starting with `_reference` are
the reference role, ids starting with `_irrelevant` the irrelevant
role, and any other underscore id is reported as role `other`. Grade
statistics exclude them all and report them separately as the
grader-check summary. Because the rubric is part of the frozen judge,
these checks also exercise the rubric itself — including a freshly
authored one. The summary presents raw
numbers with advisory thresholds stated in the report text (reference
at or above 95, irrelevant at or below 5), never a machine pass/fail.
No generation code exists or is needed: the checks reuse `aat grade`
and `metrics.py` unchanged.

**Reporting (`aat report`).** The third, read-only command renders the
benchmark statistics — score distributions and confidence intervals by
assignment, course, and config, failure accounting, and the
grader-check summary — as CSV tables plus a Markdown report under
`analysis/`. Each invocation writes one timestamped subdirectory
containing the two tidy tables (`trials.csv`, `criteria.csv`), the
derived tables (`solve_summary.csv`, `grades_by_assignment.csv`,
`grades_by_course.csv`, `students.csv`, `judge_quality.csv`,
`grader_checks.csv`, `failures.csv`), `report.md`, and
`provenance.json`. The tidy tables are always emitted so any further
question is answerable from the report directory without re-running
the loader.
Professor-grade comparison — grade-export ingestion under `tables/`,
the discrepancy report (matched pairs with explicit de-duplication,
bias, MAE, RMSE, correlation and concordance, cluster-bootstrap
intervals), and the anonymization helpers — is **deferred** to the
later-on-demand list: the benchmark and the grading assistant do not
need it, and because every grade and its provenance are stored, the
comparison is retroactively computable whenever a real need appears.
MLflow (or any tracking UI) is likewise not adopted: it would
duplicate this layer without providing the statistics, and because
files are the source of truth it remains retroactively adoptable via a
backfill script if stage 4 multi-agent scale or non-Python consumers
ever require a browsable cross-experiment UI.

## Live validation (maintainer-only, outside repository scope)

No repository work — human or agent — performs or depends on live
validation. Consistent with AGENTS.md, tests are deterministic, local,
offline, and credential-free.

Detailed evidence and exact limits of completed manual checks are recorded in
[live-validation.md](live-validation.md). The current external checklist is:

1. **Validated:** Codex CLI solves a synthetic Harbor task using cached
   ChatGPT authentication while `OPENAI_API_KEY` is unset.
2. **Validated:** a declared artifact, raw agent output, and an ATIF trajectory
   survive the synthetic task run.
3. **Validated:** Codex completes a manually materialized real notebook
   assignment in Harbor; the executed notebook, plots, transcript, trajectory,
   and source-preserving artifacts survive the run.
4. **Validated:** RewardKit runs seven programmatic dimensions in Harbor's
   shared task environment. (RewardKit has since been descoped; the result
   stands as recorded history.)
5. **Validated:** a separate Harbor grading job statically grades a
   Harbor-produced submission using cached authentication, produces a valid
   `grading_result.json` and Markdown justification covering all rubric
   criteria, and has the generic grading verifier surface the score as the
   reward.
6. **Validated:** the implemented `aat solve` and `aat grade --from-solve`
   commands automatically materialize and complete the real HW5 solve-to-grade
   path. That run also exposed an older reward contract that excluded bonus
   points and two missing inspection utilities; the corrected image and reward
   need one post-change smoke run. The pending list in
   [live-validation.md](live-validation.md) also covers the revised
   job layout and grading semantics.
7. **Descoped (validated by construction):** regrade-by-rerun is another
   grading job over the same stored artifacts — the mechanism validated in
   item 5; no separate check is required.
8. **Moved to roadmap stage 3:** the grader checks — reference solution
   near full marks, irrelevant submission near zero, repeated gradings
   agree — are grader-prompt calibration, not infrastructure
   validation, and run through the ordinary grading pipeline on corpus
   data.
9. **Deferred:** Claude Code, Gemini CLI, and other agent stacks are validated
   only after the Codex pipeline and its hardening are complete.

## Roadmap

Sequence and scope only; no dates. Completion of each stage is judged by
what the toolkit provides, not by live runs.

1. **Solve core, Codex-first** — data-root and task conventions implemented;
   the materializer converts existing course assignment folders into Harbor
   solve tasks; environment templates; generic solve contract verifier;
   pinned Codex job configurations. Complete when the toolkit can
   materialize a full course from the reference corpus into runnable
   Harbor tasks and Codex job configs.
2. **Grading core, Codex-first** — grading-task materializer; grader prompt
   template written to the contracts section; grading output schema;
   generic grading verifier; grading environment template. Complete when an
   agent solution and a real student folder both grade through the
   identical path — the vertical-slice acceptance criterion below.
3. **Validity and statistics** — the results, statistics, and reporting
   contract above: results loading (`results.py`), outcome taxonomy and
   denominator policy, `metrics.py` with clustered bootstrap confidence
   intervals, and the read-only `aat report` command; the
   rubric-required enforcement of decision 5 in the grading
   materializer, CLI, and grader prompt, with test fixtures
   regenerated accordingly; the run record's explicit lineage fields
   (submission source, student id, solve job and trial); a full-shape
   synthetic Harbor `result.json` test fixture pinning the fields the
   loader consumes; plus authoring rubrics for every corpus assignment
   (drafted by course intake, [course-intake.md](course-intake.md),
   and reviewed per
   [data-conventions.md](data-conventions.md)), repeat configurations
   and frozen grader-config versioning for reportable runs (both
   already provided by the config identity — no new machinery), the
   grader checks run on the target corpus (the manual procedure in
   the results contract), and evaluation across the corpus.
   Professor-grade comparison is deferred (stage 5).
4. **Additional agent stacks** — integrate and validate Claude Code, Gemini
   CLI, and other agents as solvers and graders; add cross-agent
   configurations and comparisons only after the complete Codex pipeline is
   hardened.
5. **Later, on demand** — LaTeX/PDF grading justifications (a grader prompt
   change); compiled-document deliverables (a `latex`-capable flavor plus
   an output-contract line) if the "does it compile" signal is ever
   wanted; optional format-aware submission lints (e.g. notebook executed,
   document compiled) if failure accounting shows the need; mechanical
   detection of grading-time input modification (a hash manifest over
   the materialized submission and reference, reported by the grading
   verifier as data, not enforcement) if the static-inspection prompt
   rule is ever observed being violated; a machine-readable rubric
   criteria manifest, emitted by the materializer and checked by the
   grading verifier, if the rubric-fidelity rate proves materially
   below 100%; an `aat rubric` command that drafts rubrics for review
   (drafting stays manual until then); intraclass correlation for
   repeat agreement and config-vs-config significance tests (bootstrap
   difference intervals) when multi-agent comparison arrives;
   professor grade-export
   ingestion, the discrepancy report, and the anonymization helpers,
   when comparing LLM grades to professor grades becomes a real need —
   retroactively computable from stored grades and provenance; judge
   calibration if a trusted human-graded corpus emerges;
   restricted network egress, host-side hardening, or a credential broker
   if grading ever faces adversarial submissions; MLflow (retroactively
   backfillable from the data root) when stage 4 multi-agent scale or
   non-Python consumers need a browsable cross-experiment UI;
   institutional or open-source packaging.

### First vertical slice (stages 1–2)

Stages 1 and 2 are implemented as one vertical slice with a single
acceptance criterion: from a
synthetic golden course under `tests/fixtures/`, the toolkit
materializes a runnable Harbor solve task and, from a synthetic
submission, a runnable Harbor grading task, both byte-exact against
committed golden fixtures; the maintainer then live-validates the same
paths on one real assignment from the data root. The pilots recorded in
[live-validation.md](live-validation.md) are evidence that this shape
works, not templates to reproduce. Build order:

1. **Data-root resolution** — explicit path, then `AAT_DATA_DIR`, then a
   clear error; a data root inside the toolkit's own working tree is
   refused (precise rule in
   [data-conventions.md](data-conventions.md)).
2. **Grading output schema** — the `grading_result.json` fields and
   internal-consistency rules as a schema plus a validation function,
   shared by the grading verifier and later statistics.
3. **Prompt templates** — solver and grader prompts written fresh per
   the contracts above, placeholder-free, including the
   `/app/submission` output contract.
4. **Solve-task materializer** — assignment directory → Harbor task
   directory (generated `instruction.md`, `task.toml`, environment from
   template, recorded assignment and prompt hashes).
5. **Generic solve verifier** — the 0/1 output-contract script included in
   every materialized solve task.
6. **Grading-task materializer** — submission directory + reference
   solution + rubric → Harbor grading task; one code path for solve
   artifacts and student folders.
7. **Generic grading verifier** — enforces the grading output schema
   through the shared validation module.
8. **Environment templates** — pinned per-flavor Dockerfiles:
   `data-science`, `optimization`, `scientific-python`, and the
   dedicated `grading` flavor.
9. **Job-config generation** — pinned Codex job configurations (agent,
   model, effort, repeats, concurrency) emitted beside the materialized
   tasks.
10. **Thin CLI wrapping Harbor** — `aat solve` and `aat grade`
    (installed as the `aat` console script): materialize, then invoke
    `harbor run` as a subprocess; `--materialize-only` exposes the
    file-writing layer alone. Each run writes a run record (exact
    `harbor --version`, toolkit version, agent and model configuration,
    effective command line, input hashes) beside the job output.

Each step lands with deterministic offline tests over small synthetic
fixtures (a fake course and a fake submission under `tests/fixtures/`),
consistent with AGENTS.md. Stage 3 work (results loading, statistics,
reporting) starts after the slice passes its
golden-fixture acceptance tests and the maintainer live-validates one
real assignment end to end.

Module layout, mapping one-to-one onto the build order:

```text
src/agentic_assessment_toolkit/
├── data_root.py           # step 1: resolution + refusal rules
├── hashing.py             # shared: file/dir sha256 for provenance
├── grading_schema.py      # step 2: fields + consistency validation
├── config.py              # experiment-config loading + identity
├── course.py              # course record + assessment registry loading
├── check_course.py        # `aat check-course`: violations/gaps/notes
├── materialize/
│   ├── _common.py         # shared materializer helpers (naming,
│   │                      #   Dockerfile/test-runner emission)
│   ├── solve.py           # step 4: assignment → Harbor solve task
│   └── grading.py         # step 6: submission → Harbor grading task
├── jobs.py                # step 9: pinned job-config emission
├── harbor.py              # step 10: harbor command construction,
│                          #   subprocess invocation, run record
├── results.py             # stage 3: trials + criteria tables
├── metrics.py             # stage 3: pooled statistics, bootstrap CIs
├── report.py              # stage 3: report rendering behind `aat report`
├── cli.py                 # step 10: argparse, `aat solve` / `aat grade`;
│                          #   stage 3 adds `aat report`
└── templates/             # package data (importlib.resources)
    ├── prompts/           # step 3: solver.md, grader.md
    ├── verifiers/         # steps 5 + 7: two standalone scripts
    ├── environments/      # step 8: one Dockerfile per flavor
    └── task/              # task.toml template
```

Experiment configs live at the repository root under `configs/`,
committed and versioned with the code (see the contracts section).

Implementation rules:

- **Verifiers are shipped files, not imported code.** They execute inside
  containers where this package is not installed, so they are standalone
  stdlib-only scripts copied into each materialized task.
  `grading_schema.py` is itself written stdlib-only and self-contained so
  the same file works both as a package import and copied verbatim into a
  grading task beside its verifier — one source of truth for validation.
- **Golden fixtures are regenerated deliberately.** `python
  tests/update_goldens.py` rewrites the byte-exact task fixtures under
  `tests/fixtures/golden/`; regeneration is a contract change and is
  reviewed as such.
- **Materialized tasks are byte-deterministic.** Task files contain no
  timestamps, absolute paths, or machine-specific content; time- and
  host-dependent provenance lives only in `aat-run.json` and the job
  directory name. This is what makes the golden-fixture acceptance tests
  byte-exact.
- **Repository tests never invoke Harbor or Docker.** The CLI wraps
  `harbor run` in a subprocess for the user, but the materialization layer
  stays paths-in, files-out; tests exercise materialization fully and
  assert on the constructed Harbor command line without executing it
  (decision 13, AGENTS.md).
- **Harbor is the single runtime-orchestration dependency**,
  version-bounded in `pyproject.toml` and invoked through its CLI — its
  stable interface and what the pilots validated — never through its
  internal Python API. Docker and the agent CLIs remain documented
  external requirements that packaging cannot provide.
- **Prefer good dependencies over hand-rolled code.** When a
  well-maintained library replaces nontrivial logic,
  use it rather than reimplementing: `numpy` and `pandas` are
  the toolkit's statistics stack (`results.py`, `metrics.py`: loading
  and pooling trials; the percentile bootstrap is a few lines of
  numpy). Hand-roll only when the code must run standalone
  inside task containers, or when a dependency would be heavier than the
  code it replaces. The container exception is load-bearing: the shipped
  verifiers and `grading_schema.py` stay stdlib-only and self-contained,
  so `grading_schema.py` remains the single source of truth for grading
  result validation both as a package import and copied into grading
  tasks. Deterministic tests are unaffected: library RNGs are explicitly
  seeded, and floating-point aggregates are asserted with tolerances.

### CLI design

Every command-line option belongs to one of three axes, and the axes are
handled differently:

1. **Selection — what to run on** (CLI flags): `--course`, `--assignment`,
   `--submissions PATH`, `--from-solve NAME`, `--all`. Selection is not
   an experimental variable, so convenience wins.
2. **Experiment configuration — how to run** (named config files, never
   flags): agent, model, reasoning effort, solver/grader prompt version,
   rubric version, and any agent-argument passthrough live in versioned
   config files selected with `--config NAME`. The hash of this
   configuration is the **config identity**: it labels results, is the
   frozen judge configuration of decision 8, and is recorded in every run
   record. Comparing models or efforts means separate invocations with
   different named configs, so results are segregated and labeled by
   construction.
3. **Mechanics** (CLI flags): `--repeats N` (Harbor's `n_attempts`;
   sampling depth, default 1, see below), `--max-concurrent-trials N`
   (Harbor's job-wide `n_concurrent_trials`, default 8), `--force`,
   `--dry-run` (list what would run, then exit), `--materialize-only`.

Sampling depth is not experiment identity. `--repeats` changes how many
trials are drawn, not the system under test or the judge, so it is
excluded from the config identity hash (though recorded in the run
record). Trials pool by (item id, per-item identity) across any number
of jobs in `metrics.py`: five repeats now and five later under the same
config are one sample of ten. Pooling is valid only while the config is truly
frozen — any prompt or rubric edit must be a new config version, which
the identity hash enforces automatically.

Idempotence: an item is **done** under a config when at least one
verified trial exists for (item id, per-item identity) — for grading
items, one that produced a valid grading result; a failed grading is a
failed measurement and is regraded by the next incremental run —
derived from the data root layout with no separate bookkeeping state. Done items are skipped by
default, so re-running a bulk command is naturally incremental ("grade
what was not yet graded"). `--force` never overwrites: it launches
another job whose trials accumulate alongside the existing ones.
Consequently, `--force --repeats N` adds N trials rather than bringing
the historical total to N. A later `grade --from-solve` selects newly
verified solver trials that are not yet graded; `--force`
on `grade` adds independent grader trials for already-graded submissions.
Regrading under a revised rubric needs no dedicated command: a new rubric
is a new config identity, under which nothing is done yet, and prior
results stay untouched.

The surface is three commands (`--data-root PATH` selects the data root
explicitly, falling back to `AAT_DATA_DIR`; it is location, not an
experiment axis; a bare `--config NAME` resolves to
`configs/NAME.toml` relative to the current working directory, so run
from the repository root or pass an explicit path):

```text
aat solve  (--course ID [--assignment ID] | --all)
           --config NAME [--data-root PATH] [--repeats N]
           [--max-concurrent-trials N] [--force]
           [--dry-run] [--materialize-only]

aat grade  (--from-solve NAME [--course ID] [--assignment ID]
            | --submissions PATH
            | --course ID [--assignment ID] | --all)
           --config NAME [--data-root PATH] [--repeats N]
           [--max-concurrent-trials N] [--force]
           [--dry-run] [--materialize-only]

aat report [--course ID] [--assignment ID] [--config NAME]...
           [--seed N] [--out PATH] [--data-root PATH]

aat check-course --course ID [--data-root PATH]
```

`aat check-course` is read-only and writes nothing: it renders one
course's contract violations (nonzero exit until fixed), completeness
gaps, intake notes, and grade-coverage summary — the review loop of the
intake procedure (decision 14). `aat report` is read-only: it changes
no experiment and no doneness,
consumes job directories, and writes derived tables and reports under
`analysis/` in the data root. `--out` overrides the destination but
obeys the same refusal rule as the data root — it is never allowed
inside the toolkit's own repository tree, because reports contain
student identifiers and grades. `--config` filters to named configs
and is repeatable; comparisons are always segregated by config
identity. `--seed` overrides the default bootstrap seed (42, see the
statistics contract); the effective seed is recorded in the report
provenance, so outputs are deterministic given the data root and seed. The exact flag set may
grow with the reports it renders; because the command is
derived-output-only, new flags here never enter any identity.

New options must pass the axis test: if it changes the experiment, it
belongs in a config file; if it changes selection or mechanics, a flag is
legitimate. Deliberately deferred: a combined solve-then-grade command
(manual chaining is fine, and the solve/grade separation is load-bearing)
and rich selection syntax (globs, exclusions) until a real run needs
them. Grading selection is settled: `--from-solve NAME` grades
verified, not-yet-graded solve trials produced under the named solve
config, optionally narrowed by `--course`/`--assignment`; without it,
`--submissions PATH` or `--course`/`--assignment` select student
folders from the submissions tree (`--submissions` must point inside
`<data-root>/submissions`, at most three levels deep: course, student,
assignment). Each verified solve trial with a
non-empty submission artifact is one gradable item — a solve config run
with `--repeats 5` yields five submissions per assignment, each graded (and
repeatable-graded) independently; trials whose artifact is missing or
empty are skipped and stay visible as explicit outcomes in the solve
job. One grading materializer underneath, two source resolvers on top,
per decision 5.
