# Agentic Assessment Toolkit Design

This is the decision and design layer between the vision in
[brief.md](brief.md) and implementation. [research.md](research.md) is a
frozen research snapshot (2026-07-30) that surveys the landscape; this
document records what was decided and what is built: everything
described here is implemented, and planned work lives in
[roadmap.md](roadmap.md). When the two disagree, this document wins. This document is maintained in place, not
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
   conventions with their intake brief and checker, the
   submission-ingest adapters and identity tables, and the thin
   `aat solve` / `aat grade` / `aat report` / `aat check-course` /
   `aat intake` / `aat ingest-submissions` / `aat init-data`
   commands. It does not
   implement an agent runner, sandbox framework, run orchestrator, model
   abstraction, transcript schema, experiment database, or results viewer:
   Harbor does all orchestration; the toolkit constructs one command line
   and records what it ran. Simplicity is a standing preference, not a hard
   budget.
3. **Subscription-backed execution.** Agents and graders run through existing
   Codex CLI / Claude Code / Gemini CLI subscriptions, not per-token API
   billing. Harbor supports this natively. Before a live Codex solve or grading
   job is materialized, the toolkit finds and validates the same file-based
   cached login used by the local Codex CLI and explicitly passes it to Harbor;
   the mechanism is identical for both stages. API-key authentication remains
   available as an explicit override or a fallback when no cached login exists.
4. **Codex-first implementation.** The first complete benchmark and
   grading-assistant pipelines use Codex for both assignment solving and
   grading. Benchmark core, grading, validation, reporting, and hardening
   are completed for the Codex stack before Claude Code, Gemini CLI, or
   other agents are integrated. Solver and grader remain separate,
   independently configured jobs. Multi-agent comparison is a long-term
   goal ([roadmap.md](roadmap.md)).
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
   is to author the rubric first (course intake does this for every
   material-backed assignment, taking the point split from the
   highest-precedence source the materials offer and authoring one
   where no source states any; the procedure in
   [data-conventions.md](data-conventions.md) covers the rest). Stable criterion ids
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
   grading waits on a trustworthy human-graded corpus
   ([roadmap.md](roadmap.md)).
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
14. **Course intake is an agent task the toolkit launches directly,
    with a deterministic checker.** Raw course materials are dumped
    into `raw/<course_id>/` in the data root; `aat intake` runs the
    Codex CLI once per unprocessed course — plain `codex exec` in a
    workspace-write sandbox, never Harbor: intake is trusted authoring
    with a human review gate, not a measured experiment — with the
    rendered `intake` prompt template, producing `courses/<course_id>/`
    (course record, assessment registry, rubric drafts). This is the
    same thin pattern as the Harbor commands: construct one command
    line, run it as a subprocess, record what ran. After each
    successful run the command writes an `intake-record.json` receipt
    and prints the `aat check-course` report — contract violations,
    completeness gaps, intake notes — until the course is clean.
    Intake doneness is the receipt's hash of the raw dump: new raw
    material makes a course unprocessed again (an incremental pass
    that never modifies existing artifacts), while the prompt template
    and model are provenance only — intake output is human-reviewed,
    so changing them never invalidates a processed course. Facts the
    materials do not state are left absent — never sentinel values —
    and surfaced by the checker. Student-submission ingest is out of
    intake's scope (decision 15). The procedure is
    [course-intake.md](course-intake.md).
15. **Submission ingest is deterministic code with a human review
    gate.** LMS submission exports are dumped verbatim into
    `raw-submissions/<course_id>/`; `aat ingest-submissions` converts
    them into `submissions/<course_id>/<student_id>/<assignment_id>/`
    and the identity tables under `tables/<course_id>/` with plain
    code, never an agent — the exports are uniformly structured
    (adapters for Brightspace upload folders and Gradescope graded-copy
    PDFs, auto-detected per zip), and pseudonymization must happen
    before anything reaches an LLM. Multiple uploads merge by union of
    relative paths with exact-path supersession — nothing else is ever
    discarded, and every supersession, ambiguity, and skip is flagged
    for the human to review; anything the code cannot settle
    (unmatched zip names, unresolvable identities) is a loud error or
    a skipped-and-flagged row fixed via a small per-course manifest,
    never a guess. Gradescope grade-summary pages are split off before
    the submission enters the tree (the grader must never see the
    professor's scores) and stored for the planned professor-grade
    comparison. Doneness is a receipt hash of the raw dump, the same
    incremental pattern as intake; a submission directory referenced
    by a grading run record is frozen and never rewritten. The full
    contract is in [data-conventions.md](data-conventions.md),
    "Submission ingest".
16. **Final grades come from a final-judge grading round.** The
    deliverable grade and the student-facing feedback document for a
    submission are produced by a *final judge*: a grading config
    (`judge = true`) whose tasks additionally present the stored
    initial gradings of the same submission as context. The judge is
    prompted as a reconciler, not an averager — every prior-grading
    claim is verified against the submission before it influences a
    score or the feedback — and its output is the same grading result
    schema plus a third required file, the feedback document, so every
    downstream layer (verifier, results, statistics, report) consumes
    judge gradings unchanged. The prior gradings are judge inputs, so
    their bytes fold into the judge item's per-item identity, and the
    run record carries explicit lineage to the trials they came from.
    By default the judge skips — loudly, with the exact top-up command
    — any item with fewer usable prior gradings than the required
    `--min-gradings`; judging on fewer is always an explicit choice.
    The judge's feedback document is written for the student and never
    mentions the grading process; collecting and re-identifying the
    documents for distribution is a planned export step
    ([roadmap.md](roadmap.md)).

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
  rubric bytes, the assignment directory hash, and any rubric source
  hash); paired with the item id, it is the doneness and pooling key.
- **Verified trial** — a trial whose verifier recorded a reward,
  whatever the reward's value (see denominator policy).
- **Done** — an item with at least one verified trial under a config —
  for grading items, one holding a valid grading result. Whether a run
  skips a done item additionally keys on the `--repeats` target: an
  item below its target still receives its missing trials.
- **Final judge** — a grading config with `judge = true`, run with
  `--context-from`: its tasks present the context config's stored
  gradings of the same submission as numbered rounds, and its output
  adds the student-facing feedback document (decision 16).
- **Prior gradings** — the stored valid gradings a final-judge task
  presents under `/app/prior_gradings/`: the context config's gradings
  of the same submission, matched by that config's own pooling key.
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
- **Intake** — the `aat intake` procedure that converts
  `raw/<course_id>/` into `courses/<course_id>/` by running the intake
  agent per unprocessed course ([course-intake.md](course-intake.md));
  `aat check-course` is its deterministic reviewer, and the
  `intake-record.json` receipt is its doneness.
- **Submission ingest** — the `aat ingest-submissions` procedure that
  converts `raw-submissions/<course_id>/` LMS exports into
  `submissions/<course_id>/` and the `tables/<course_id>/` identity
  and bookkeeping tables, deterministically (decision 15); its
  `ingest-record.json` receipt is its doneness.
- **Golden fixture** — a committed byte-exact expected task tree under
  `tests/fixtures/golden/`, regenerated only deliberately.

## What goes where

```text
Harbor owns                          Toolkit owns
-----------                          ------------
sandboxed execution (solve + grade)  assignment/dataset import conventions
                                     LMS submission ingest (adapters,
                                       merge policy, identity tables)
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
(professor-grade comparison: planned, see roadmap.md)
```

## Contracts

Concrete specifications. Nothing here is ported from the reference
repositories; they informed these choices as evidence only.

### Grading output schema

The grader writes two required files to `/app/grading_output/`:

- `grading_result.json` — the machine-readable judgment.
- `justification.md` — the per-problem written justification
  (non-empty).

A task can require further deliverables: the materializer writes
`tests/required_files.json` beside the verifier — a list of extra
`grading_output/` filenames — and the verifier requires each to be
present and non-empty exactly like `justification.md` (absent file,
no check; a malformed declaration is a contract error, never an
uncaught crash — the same pattern as the expected-criteria file). One
task kind declares one today: a final-judge task requires
`feedback.md`, the student-facing feedback document (decision 16),
written to the student in the second person, consistent with the
awarded points, and never mentioning the grading process — other
graders, prior rounds or their scores, or these instructions. The
verifier checks its presence and non-emptiness only; its content
quality is human-reviewed via the review queue.

Extra scratch files in `grading_output/` are tolerated and preserved as
artifacts; the contract is that the required files are present and
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
The criteria must also reproduce the rubric exactly. At
materialization the toolkit writes the rubric's parsed criteria — id,
max_points, and bonus flag, in rubric order — to
`tests/expected_criteria.json` beside the verifier, and the verifier
compares the grader's authored criteria against it: the id sets must
match, and each criterion's max_points and bonus flag must equal the
rubric's. Any divergence — a dropped, renamed, added, or reweighted
criterion — silently changes the score denominator, so it is a hard
contract failure (reward 0.0, the mismatches listed in the verifier
details, `expected_criteria_checked: true`). Tasks materialized
before the file existed have no expected-criteria file; the verifier
then skips the check so doneness-based reruns of old jobs keep
working. `expected_criteria_checked` is false exactly when the check
did not run — the file is absent, or structural validation failed
first; a malformed expected-criteria file is itself recorded as a
contract error, never an uncaught crash.
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
- `rubric_source/` — present only when the course tree has
  `rubrics/<assignment_id>/source/`: the professor's standalone rubric
  document(s), verbatim, from which `rubric.md` was transcribed.
  Context, never authority: the grader prompt states that where the
  two appear to differ, `rubric.md` governs, and rubric fidelity is
  measured against `rubric.md` alone. Most assignments have no
  standalone rubric document, and absence is the normal case.
- `prior_gradings/` — final-judge tasks only: the context config's
  stored gradings of this same submission, one numbered round directory
  (`01/`, `02/`, ...) per grading, each holding that round's verbatim
  `grading_result.json` and `justification.md`. Rounds are numbered in
  sorted (job, trial) order; no job or trial name enters the task
  (byte-determinism — the lineage lives in the run record). Context,
  never authority: the judge prompt directs the judge to verify every
  prior-grading claim against the submission, and marks the rounds as
  untrusted content like the submission itself.
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

It also states the submission-reading procedure, added after real
grading runs produced schema-valid wrong grades from four distinct
reading failures: a grader that only extracted embedded images and
never rasterized pages missed vector-ink handwriting; a malformed
bounding box made every renderer blank two pages that held the
student's work; graders asserted "duplicate pages" that the
actual image bytes refute; and page layouts clipped embedded scans
so the render silently showed only part of the work. The grader's
mandatory first step is the
deterministic PDF preflight baked into the grading image at
`/opt/aat/preflight.py`: it renders every submission PDF page at a
fixed 150 DPI, extracts every embedded image beside the renders, and
writes a manifest recording, per page, the rendered
ink fraction, the extractable-text count, and the embedded-image
inventory with content hashes and extracted-file paths. PDFs are
graded from these canonical
renders; extraction tools are supplementary evidence, never the sole
reading path. The manifest flags `DISCREPANCY` (a page renders nearly
blank while holding substantial drawable content — the
hidden-content signature; a scan-sized image whose pixels no
installed reader can measure, such as a raw CCITT extraction, counts
as content, never as blank), `PARTIAL_RENDER` (a page renders with
ink, yet several times less than a scan-sized embedded image on it
holds — the clipped-scan signature; transparency-mask rows and small
images are ignored, and the grader reads the extracted image
beside the render), `DAMAGED` (qpdf reports structural
errors), and `NO_TEXT_LAYER` (informational). The flags are advisory
measurements: the grader prompt states that a flag is reliable
evidence while the absence of one is not, and its final self-check
requires the render of every page viewed, every flag addressed in the
written evidence, and every measured render-versus-inventory
disagreement reconciled against the extracted images. A flagged page
is never
scored as blank or missing: the grader repairs a temporary copy
(`qpdf`/`mutool clean` rewrites, sanitizing malformed values such as
an overflowed Form-XObject bounding box, exposing form streams) and
re-renders; claims that pages are blank, duplicated, or missing must
cite the manifest, and a duplicate-page claim requires matching
image hashes. Run-time tool installation is no longer a recovery
step: the image carries every reader the procedure names, and a
network-dependent grade is not reproducible. When a submission holds
overlapping or duplicate files, the grader grades the most complete
version and names it in the overall comment; readable content with
no gradable academic work scores zero on each criterion with that
stated as evidence. Content still unreadable after repair must never
receive a fabricated grade: the grader is directed to withhold
`grading_result.json` and explain the problem in `justification.md`,
making the trial a contract violation — a failed measurement that is
rerun (see reward semantics), never a silent zero.

### Experiment configs and config identity

Experiment configs are TOML files committed under `configs/` at the
repository root (e.g. `configs/codex-high.toml`). They contain no
course content: agent, model, reasoning effort, solver or grader prompt
template name, rubric name, agent-argument passthrough, and — for
grading configs — the `judge` flag marking a final-judge config
(decision 16). Selection
and mechanics never appear in configs (see CLI design).

Materialized solve and grading tasks each give the agent 7,200 seconds.
They give environment startup 1,800 seconds and verification 600
seconds. Harbor applies these as separate phase limits, not as one
whole-trial deadline.

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
bytes, the resolved rubric file bytes, the hash of the assignment
directory presented in the task, and — when the assignment has one —
the hash of the rubric source directory. A final-judge item further
folds in the hash of the prior gradings presented in the task (their
exact bytes, in presentation order), so a judgment over three initial
gradings and one over the topped-up five are distinct items: the
earlier judgment keeps its own identity and results, and doneness
correctly re-judges after a top-up. Rubric source directories and
assignment directories freeze at first use; a rubric *version* is
likewise immutable, but the name selecting it may advance to a
corrected version once the superseded bytes are archived (see
[data-conventions.md](data-conventions.md)). Folding the rubric bytes
into the per-item identity is what makes that safe: a revised rubric
changes doneness for exactly the assignments it applies to, and the
prior results keep their own identity. Mechanics such as `--repeats` and
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
  installed but unlicensed: Gurobi is enabled at run time with a
  license file from outside the repository. `aat solve` resolves the
  host path from `--gurobi-license-file` or
  `AAT_GUROBI_LICENSE_FILE`, rejects a licensed selection containing
  any non-optimization task, and has Harbor mount the file read-only at
  `/opt/gurobi/gurobi.lic`, a standard Gurobi discovery path. Plus
  numpy, scipy, pandas, matplotlib, openpyxl, scikit-learn and sympy
  (the course's clustering and symbolic-algebra assignments needed
  them), and the notebook toolchain.
- `scientific-python` — the general flavor: numpy, scipy, pandas,
  matplotlib, sympy, python-control (used by the control-systems
  course), openpyxl, and the notebook toolchain.
- `grading` — the single flavor used by every grading task: a minimal
  Python image with the document-reading baseline below (openpyxl and
  pandas for tabular data, nbformat for notebooks) plus the tools
  observed gradings reached for. For the grader's own independent
  check calculations — its code on its own inputs, which the grader
  prompt sanctions (decision 9) — it carries scipy (observed gradings
  reimplemented linear programming by hand without it), scikit-learn,
  and sympy (which brings mpmath). For visual inspection it carries
  ImageMagick 6 (`identify`, `convert`, `montage` — graders check
  image dimensions, crop submitted figures, and build page contact
  sheets) together with fonts-urw-base35 — ImageMagick resolves its
  default font through its own type map, which lists only the URW
  base-35 fonts, and on an empty map `montage` and `convert -annotate`
  abort outright — plus fonts-dejavu-core for explicit `-font` use,
  keeping Debian's default security policy that disables
  ImageMagick's Ghostscript-based PDF conversion: rasterizing
  untrusted PDFs stays with poppler's `pdftoppm`. It also carries
  `qpdf` for PDF structure inspection, pymupdf and pikepdf as
  alternative engines for malformed PDFs that defeat poppler and
  pypdf (observed gradings installed both at run time to recover
  unreadable files), mupdf-tools (`mutool clean`/`mutool draw`, a
  second independent renderer and rewriter for the repair path),
  `xxd` for hex dumps, and binutils' `strings` for reading text out
  of binary files such as saved model checkpoints without loading
  them. The image carries the PDF preflight script at
  `/opt/aat/preflight.py`, embedded in the Dockerfile as a heredoc
  because environment templates build from an empty context; a
  repository test keeps the embedded copy byte-identical to
  `templates/environments/preflight.py`. The template ends with a
  build-time smoke test exercising the advertised inspection tools —
  contact sheets, annotation, OCR, PDF rasterization and structure
  inspection, the Python readers, and the preflight's self-test,
  which authors four synthetic PDFs (an overflowed-bounding-box
  form that must flag `DISCREPANCY`, vector ink over a background
  image that must render with ink, a genuinely blank scan that must
  not flag, a scan placed partly outside its page that must flag
  `PARTIAL_RENDER`) — so a tool that installs cleanly but cannot run fails
  the build instead of a grading run. Nothing from the submission,
  reference, or assignment is ever executed during grading, and the
  grading prompt forbids run-time package installation outright. The
  image creates `/app/grading_output/`.

Every environment includes `file` and `jq` for basic file-type and JSON
inspection, plus a pinned Node and a pinned Codex CLI: Harbor's
agent-install step checks for `codex` on PATH and skips its own
network install (nvm, a remote Node-version lookup, `npm install
@latest`) when it is present, so preinstalling turns a per-trial
network dependency — one transient lookup failure cost a trial
mid-run — into a build-time one, and pins the agent version into the
image bytes, and therefore into item identity, instead of letting each
trial resolve `@latest`.

Every environment also carries one document-reading baseline, because
course material routinely arrives as more than PDFs (the corpus holds
Word and PowerPoint documents, archives, and scanned PDFs with no
extractable text): PDF text extraction (poppler-utils with
poppler-data for non-Latin CMaps, pypdf), OCR for scans (tesseract,
rasterizing via poppler's `pdftoppm`), Word and PowerPoint reading
(pandoc, python-docx, python-pptx), and `unzip`. Preinstalling the
baseline keeps the solver flavors' run-time install allowance the
rare exception rather than a per-trial network dependency; the
grading prompt forbids run-time installs outright. The output contract requires document
source, never compiled PDFs, so no image needs TeX and there is no
`latex` flavor ([roadmap.md](roadmap.md)).

A task's Dockerfile does not embed its flavor template. Instead the
template is built once per launch as a shared local base image named
`aat-env-<flavor>:<template-content-hash>`, and each task's generated
Dockerfile starts `FROM` that name with only the task's `COPY` lines
on top. The name exists in no registry namespace, so per-task builds
resolve entirely locally — without this, every trial's build
re-resolved the template's public base tag against its registry, and
a transient registry failure or throttle failed trials mid-run. The
launch-time base build is the one build that still reaches a registry;
`aat` runs it before starting Harbor, retries it with backoff (waits
of 10 s, 60 s, then 300 s — observed registry throttling persists for
minutes, and this build is the launch's only registry contact), and
skips it when the image already exists locally. The content-hash tag
means a template edit yields a fresh name and a stale image is never
reused, and the template bytes enter per-item identity exactly as
before. Each task directory keeps its template verbatim as
`environment/base.Dockerfile`, so the image a task ran on can always
be rebuilt from the task directory alone; `--materialize-only` prints
the required base image name, since Harbor is then launched by hand.

Images pin their Python package versions; base images are not
digest-pinned ([roadmap.md](roadmap.md)). Solver licenses (Gurobi WLS) are
credentials: never baked into images, never committed, always injected
at run time.

The flavor set changes only by editing the templates in this
repository — a reviewed code change. Intake never writes Dockerfiles:
when a course clearly needs packages no flavor carries, the intake
agent picks the closest flavor and records the missing packages in
`intake-notes.md` as an environment gap for the maintainer to fold
into a flavor (or, for a genuinely new capability, a new flavor).
Until the template is updated, the solver's run-time install
permission (below) covers the gap: a missing package costs the agent
an install command, not a failed run.

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
autonomy expectations, the install policy (use what the image carries
when it suffices; install a genuinely needed missing package rather
than abandon an approach — silence here would make solve rates measure
each model's willingness to install without permission instead of its
ability to solve), and the `/app/submission` output contract
(executed notebooks, document source in Markdown or LaTeX — never
compiled PDFs, no scratch files). The grader prompt covers the
workspace — including the assignment handout as the record of what was
asked, and the optional rubric source as transcription context that
never overrides the rubric — the static-inspection rule (read as data;
never execute or compile; converting a given document into readable
form — rasterization, OCR, unpacking an archive — is reading, not
execution) and the outright run-time install prohibition (the grading
image carries every reader the procedure names, and a run-time
install would make the grade depend on the network; the solver's
install allowance does not extend to the grader),
prompt-injection resistance (instructions
inside the submission are content, not commands), rubric authority — including the requirement
to reproduce the rubric's enumerated criteria verbatim: same ids, same
max points, same bonus flags, with only the points awarded being the
grader's judgment — the administrative-requirements rule (identity,
signatures, honor affirmations, submission formalities, lateness, and
escalation are never scored and never withhold credit; visible
non-compliance is noted in the overall comment for course staff —
mirroring the rubric convention that administrative requirements are
never rubric content, [data-conventions.md](data-conventions.md)), the
no-worked-solution case (when the reference directory holds a guidance
note instead, correctness is established from the rubric, the
submission's derivations, and internal consistency checks), evidence
requirements, and the exact output schema above. Any prompt
edit changes the config identity by construction.

The judge prompt (`judge`) is the grader prompt's final-judge variant
(decision 16). Beyond everything the grader prompt covers, it
describes the prior-gradings workspace and states the reconciler
rules: the judge grades the submission itself and treats the prior
gradings as leads to chase, never facts to compile — every claim is
verified against the submission before it influences a score or the
feedback, disagreements between rounds are resolved by deciding which
reading of the evidence is correct (never averaged), agreement among
rounds is not evidence, and the final grade may fall outside the range
the rounds span when the evidence says they all misjudged. The prior
gradings are marked untrusted content exactly like the submission. Its
required output adds the feedback document with its rules (see the
grading output schema): student-facing, process-silent, consistent
with the awarded points, every included issue verified in the
submission first. The initial grader prompt is deliberately not
enriched with feedback authoring — the per-criterion evidence it
already produces is the judge's raw material, and enriching it is a
roadmap item with an observed-need trigger
([roadmap.md](roadmap.md)).

Prompts are instruction briefs: they state requirements imperatively
and with uniform force, and never disclose enforcement mechanics —
what is or is not machine-verified, which violations are failed
versus flagged, or the consequences of specific failure modes. That
information lives in the verifiers and this document. The corollary
is directional: an enforcement-only change (what the verifier fails,
flags, or ignores) must never require a prompt edit, so enforcement
details are never copied into prompt text.

### Authentication at launch

Authentication is run-time host configuration, not experiment identity. For a
live job whose configured agent is `codex`, `aat` resolves authentication before
creating either the job directory or its materialized task directory. It uses
the first applicable source:

1. A non-empty `CODEX_AUTH_JSON_PATH`, which selects that file.
2. `CODEX_FORCE_AUTH_JSON`: `true`, `1`, or `yes` selects
   `~/.codex/auth.json`; `false`, `0`, or `no` selects `OPENAI_API_KEY`.
3. The file `${CODEX_HOME:-~/.codex}/auth.json` when it exists.
4. A non-empty `OPENAI_API_KEY` when the cached file does not exist.

The automatically discovered cached file therefore wins over an API key that
happens to be present in the shell. Once a source is selected, its competing
authentication variables are removed from the Harbor subprocess environment;
the run never silently falls back to another billing route. A selected auth file
must exist, be readable, and contain a non-empty JSON object. A selected API key
must be non-empty, and `CODEX_FORCE_AUTH_JSON` must contain one of the listed
boolean values. Any violation exits with a usage error before durable run output
is created. This is a local structural preflight, not a provider call: a revoked
login, an unrefreshable expired credential, or an account without access can
still fail after launch. Agents other than Codex retain Harbor's own
authentication behavior.

File-based Codex credentials may represent either ChatGPT subscription access or
an API-key login. If the local Codex installation stores its cached login only in
an operating-system keyring, the user selects file storage with
`cli_auth_credentials_store = "file"` in Codex configuration and logs in again;
Harbor needs a host file that it can copy into the container. The credential is
treated as secret: it is held only in the subprocess environment or selected
file, and neither its value nor its path enters the run record. The run record
contains only the resolved method (`codex-auth-json` or `openai-api-key`) and
selection source.

`--dry-run` and `--materialize-only` do not resolve authentication and remain
credential-free. A user who manually runs the Harbor command printed by
`--materialize-only` is bypassing the AAT launch boundary and must provide one of
Harbor's authentication variables in that shell. `aat intake` runs the host
Codex CLI directly, so the CLI itself reuses its cached login.

### Run records and idempotence

Each `aat` invocation creates one job directory per deficit group —
usually one; see the CLI design for how target-count `--repeats`
groups items by how many trials each still needs, because Harbor's
`n_attempts` is job-wide. Job directories are named
`<utc>__<config>__<hash8>/`, where the hash is the first eight
characters of the config identity, under `solving/`
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
rubric, rubric source, submission, reference solution, grading
schema — as applicable). Item ids take three shapes: `<course>/<assignment>` for
solve items, `<course>/<student>/<assignment>` for student grading
items, and `<solve-job>/<trial>` for solve-derived grading items; the
explicit lineage fields exist so no consumer ever parses an item id.
On a rare same-second collision the job directory name gains a `-N`
suffix; a job's identity lives in `aat-run.json`, never in the
directory name.

Run-record schema version 2 adds `authentication`. It is `null` for an offline
materialization or an agent whose authentication AAT does not manage; otherwise
it contains only `method` and `source`. Credential values and auth-file paths are
never recorded.

Run-record schema version 3 adds the target-repeats accounting and the
final-judge lineage. `repeats` is what the job's Harbor config ran (its
`n_attempts` — the group's shared deficit); `repeats_target` is the
target count the invocation ensured. `sample` is the requested
`--sample` value or `null`; each record lists the items its own job
launched — items already at target appear in no record — and the full
sampled frame is re-derivable at any time because the sample rule is
deterministic over the submissions tree. The `executed` flag is
per job: records are written `false` at materialization and set `true`
just before that job's launch, so a job an aborted invocation never
launched keeps `executed: false`. Final-judge items carry
`context_config_name`, `context_config_identity`, and `prior_trials` —
the exact (job, trial) pairs whose gradings the task presented — plus a
`prior_gradings` input hash, so no consumer ever reconstructs judge
inputs from task bytes.

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

Harbor-level retries are enabled for failures that hold no
measurement. The generated job config sets a retry policy — up to
three retries per trial, waiting 10 s, 60 s, then 300 s — whose
include list names exactly three exception types: `RuntimeError`
(Docker command failures, which is how transient image-registry
errors surfaced in practice), `EnvironmentStartTimeoutError` (the
environment build timed out), and `NonZeroAgentExitCodeError` (the
agent command exited nonzero before verification — observed when the
provider rejects a run with a transient capacity error the agent
surfaces only as exit code 1). Harbor matches these names exactly,
without inheritance, which is why the `RuntimeError` subclass is
listed by its own name. Such an attempt holds no measurement, so
nothing is lost when Harbor erases the failed attempt's directory
and reruns it in place. Every outcome-bearing failure — agent
timeouts, refusals, an invalid grading result — is outside the
include list and is never retried, because Harbor's in-place retry
would erase the per-attempt history that explicit failure accounting
depends on. For those, re-running the `aat` command is the retry
mechanism, and it accumulates trials rather than overwriting them.
`ApiUsageLimitError` is deliberately not retried either: it means an
exhausted subscription quota, which backoff cannot fix; re-running
the `aat` command recovers those trials once quota returns.

### Results, statistics, and reporting

The read side of the data conventions. Everything
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
  agent execution, verification), and timestamps. Each trial also
  carries `agent_timeout_sec`, the agent timeout it ran under: the
  `[agent] timeout_sec` of its materialized task.toml under the data
  root's `tasks/` tree, times Harbor's per-trial timeout multiplier
  when the trial result records one — so a job launched before the
  timeout constant changed keeps its own value. A timeout that is
  missing, unreadable, or non-positive loads as missing. Token and
  cost values a run did not report load as missing, never as zero —
  zero never means unknown. Token counts keep Harbor's semantics verbatim —
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
  grading config identity. Final-judge rows carry the context config
  name and identity, the prior-grading count, and the prior trial
  names ("job/trial; ..."), read from the run record's judge lineage —
  a non-missing context config identity is what marks a grading row as
  a final judgment. The loader also computes the `superseded` flag:
  within one (grading config identity, item id, context config
  identity), a valid judgment whose prior-trial set is a strict subset
  of another valid judgment's was replaced by a re-judge over more
  evidence — the designed outcome of topping up initial gradings and
  re-judging. Superseded judgments are excluded from every score
  aggregate (a current and an outdated final grade must never average)
  and counted per student as `n_superseded`, never silently dropped;
  judgments with equal or non-comparable prior sets all stay current. A graded trial whose stored
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
regardless of how many trials it accumulated. Every grading table also
keys on the **rubric hash**, so an assignment whose rubric was revised
appears once per version and grades measured against different point
splits are never averaged; such an assignment has no single
per-assignment score, so it is left out of the course macro-mean and
counted in `n_assignments_mixed_rubric` rather than silently dropped.
An assignment with no
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
resolves the rubric version by the hash recorded in the run record —
across the assignment's selectable rubrics and its archived versions,
so a trial graded before the rubric advanced still resolves to the
bytes it used — and a trial whose rubric version is on disk nowhere,
or does not parse, is counted as unresolved rather than rated. Failure
rates per outcome category complete the set.

Cross-run consistency is a deterministic flag over repeated gradings:
valid gradings group by grading config and pooling key — the same
submission graded more than once under one config and rubric version —
and each group reports its sorted `base_pct` values, median, and
range. A group is flagged when the range exceeds 20 percentage
points, an individual grading when it deviates more than 10 points
from the group median; the thresholds are recorded in the report
provenance. The flags are advisory pointers for human review — a
schema-valid but wrong grade shows up as a wide repeat range — and
never exclude a grading from any aggregate.

The review queue is the navigation companion to those flags: one row
per graded submission under one initial grading config and pooling key
— single gradings included — sorted by score disagreement, biggest
first, with the exact trial names and per-trial justification paths so
a row opens in one step. Current (non-superseded) final-judge gradings
are matched to their
initial group through the recorded prior-trial lineage and appear on
the same row: the final score (the median when the judge graded the
item more than once), the prior-grading count, and
`final_outside_range` — true when the final score falls outside the
span of the initial scores the judge actually saw (its recorded prior
trials, so a later top-up never widens the span and hides an outlier).
The flag is not evidence the judge is wrong but
exactly the row a human should read before releasing feedback. A judge
grading whose initial group is not among the loaded trials appears as
its own row rather than being dropped, and two judge configs over one
submission yield one row each. Repeat consistency diagnoses
the judge configuration; the review queue prioritizes the human pass —
both advisory, excluding nothing. Near-timeout accounting
makes duration creep visible before it becomes timeout failures: per
job, trials whose agent-execution duration exceeds 60% of their own
agent timeout are counted, beside the trials whose duration or
timeout is unavailable (skipped and counted, never flagged) and the
job's maximum duration and timeout.

Grading-assistant statistics are descriptive only — per student and
assignment: the mean over valid gradings, the repeat SD (the
per-student uncertainty statement), the grading count, and flags;
per assignment: the class distribution over the per-student mean
grades — one value per student — as count, mean, median, SD, and
quartiles. Final grades are simply these tables filtered to the
final-judge config: judge gradings are ordinary grading trials under
their own config identity, so no separate aggregation exists for them —
with one refinement, the superseded flag (see the trials table), which
keeps a re-judged submission's final grade current-only. The bootstrap belongs to benchmark aggregation, never to
individual grades. Harbor's built-in aggregation (means, binary-reward
pass@k) is not used: it counts errored trials in score means and
cannot express graded rewards. Pass@k is not computed: no pass
threshold on `score_pct` has been chosen ([roadmap.md](roadmap.md)).

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
`grader_checks.csv`, `repeat_consistency.csv` — one row per repeated
item with its score list, median, range, and flags,
`review_queue.csv` — the disagreement-sorted review table with
final-judge joins,
`near_timeouts.csv` — per-job near-timeout counts,
`failures.csv`, `ungraded_solves.csv` — one named
row per solve trial with no valid grading and its outcome, the direct
answer to what is missing from grading and why), `report.md`, and
`provenance.json`. The tidy tables are always emitted so any further
question is answerable from the report directory without re-running
the loader.
Professor-grade comparison is not implemented
([roadmap.md](roadmap.md)): the benchmark and the grading assistant do
not need it, and because every grade and its provenance are stored,
the comparison is retroactively computable whenever a real need
appears. MLflow (or any tracking UI) is likewise not adopted: it would
duplicate this layer without providing the statistics, and because
files are the source of truth it remains retroactively adoptable via a
backfill script if a browsable cross-experiment UI is ever needed
([roadmap.md](roadmap.md)).

## Implementation

Deterministic offline tests over small synthetic fixtures (a fake
course and a fake submission under `tests/fixtures/`) cover every
layer, consistent with AGENTS.md.

Module layout:

```text
src/agentic_assessment_toolkit/
├── data_root.py           # data-root resolution + refusal rules
├── hashing.py             # shared: file/dir sha256 for provenance
├── grading_schema.py      # grading-result fields + consistency validation
├── config.py              # experiment-config loading + identity
├── course.py              # course record + assessment registry loading
├── check_course.py        # `aat check-course`: violations/gaps/notes
├── intake.py              # `aat intake`: codex command, receipts, doneness
├── ingest.py              # `aat ingest-submissions`: LMS export adapters,
│                          #   merge policy, identity tables, receipts
├── rubric.py              # rubric criteria grammar parsing
├── materialize/
│   ├── _common.py         # shared materializer helpers (naming,
│   │                      #   Dockerfile/test-runner emission)
│   ├── solve.py           # assignment → Harbor solve task
│   └── grading.py         # submission → Harbor grading task
├── base_images.py         # shared per-flavor base images: naming,
│                          #   launch-time build with retries
├── jobs.py                # pinned job-config emission
├── harbor.py              # harbor command construction,
│                          #   subprocess invocation, run record
├── results.py             # trials + criteria tables
├── metrics.py             # pooled statistics, bootstrap CIs
├── report.py              # report rendering behind `aat report`
├── cli.py                 # argparse: `aat solve` / `aat grade` /
│                          #   `aat report` / `aat check-course` /
│                          #   `aat intake` / `aat ingest-submissions` /
│                          #   `aat init-data`
└── templates/             # package data (importlib.resources)
    ├── prompts/           # solver.md, grader.md, intake.md
    ├── verifiers/         # two standalone scripts
    ├── environments/      # one Dockerfile per flavor, plus
    │                      #   preflight.py (canonical source of the
    │                      #   grading image's embedded copy)
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
- **Task directory names are valid Docker image names.** Harbor derives
  each trial's Docker image name from the task directory name by
  lowercasing it, so the name generator restricts each component to
  alphanumerics and inner hyphens before joining with `__` and appending
  the item-id hash. Logical identifiers are never restricted — a
  grader-check student id like `_irrelevant` stays verbatim in
  submission directories, `aat-run.json`, and reports, and only its
  sanitized form (`irrelevant`) appears in the task name; the hash
  suffix keeps names unique when sanitization collapses distinct ids.
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

## CLI design

Every command-line option belongs to one of three axes, and the axes are
handled differently:

1. **Selection — what to run on** (CLI flags): `--course`, `--assignment`,
   `--submissions PATH`, `--from-solve NAME`, `--all`, and — student
   grading selection only — `--sample N`, which keeps, per assignment,
   the first N submitted students in a deterministic hash order (see
   below). For a final-judge run, `--context-from NAME` and
   `--min-gradings N` select which stored gradings each judge task
   presents (decision 16). Selection is not
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
3. **Mechanics** (CLI flags): `--repeats N` (the target trial count
   per item — ensure N valid trials exist, launching only each item's
   deficit; default 1, see below), `--max-concurrent-trials N`
   (Harbor's job-wide `n_concurrent_trials`, default 8), `--force`,
   `--dry-run` (list what would run, then exit), `--materialize-only`,
   and solve-only `--gurobi-license-file PATH` (falling back to
   `AAT_GUROBI_LICENSE_FILE`). The license path is run-time host
   configuration, not experiment identity. It produces a read-only
   Harbor bind mount and is valid only when every selected assignment
   resolves the `optimization` environment.

Sampling depth is not experiment identity. `--repeats` changes how many
trials are drawn, not the system under test or the judge, so it is
excluded from the config identity hash (though recorded in the run
record). Trials pool by (item id, per-item identity) across any number
of jobs in `metrics.py`: five repeats now and five later under the same
config are one sample of ten. Pooling is valid only while the judge is
truly frozen — and it is, by construction: a prompt or config edit
moves the config identity, and a rubric edit moves the per-item
identity of exactly the assignments it applies to, so trials measured
against different bytes never pool.

`--repeats N` is a target, not a per-job draw: for each selected item
the command counts the valid trials already pooled for its (item id,
per-item identity) and launches only the deficit, so re-running the
same command after any partial failure — an expired token, exhausted
quota, lost trials — finishes exactly what is missing, and running at
full count does nothing. Harbor's `n_attempts` is job-wide, so items
with different deficits cannot share a job: the invocation groups
items by deficit and launches one Harbor job per group (usually one),
each with its own directory and run record. `--sample N` keeps, per
assignment, the first N submitted students ordered by
`sha256(course_id + "/" + student_id)` — deterministic across configs,
machines, and time with no seed or stored state, uncorrelated with the
enrollment order the sequential ingest ids carry, and nested (the
first N are a prefix of the first N+K, so raising the sample later
grades only the new students). Pseudo-students are excluded from the
frame and the selection; grader checks are run deliberately via
`--submissions`. The panel is a function of the current submissions
tree: ingesting a new student whose hash sorts inside the first N
displaces the previous Nth, so new submissions are ingested between
sampled experiments, never during one. `--sample` composes with a
course-level `--submissions` path and is rejected below course level
(a frame narrowed to one student is not a panel) and with
`--from-solve`. The plan output states each assignment's sample
("30 of 817 submitted students"), and the run record stores the
requested sample beside the launched items.

Idempotence: an item is **done** under a config when at least one
verified trial exists for (item id, per-item identity) — for grading
items, one that produced a valid grading result; a failed grading is a
failed measurement and is regraded by the next incremental run —
derived from the data root layout with no separate bookkeeping state.
Items at or above the `--repeats` target are skipped by
default, so re-running a bulk command is naturally incremental ("grade
what was not yet graded", "top up what is short"). `--force` never
overwrites: it launches
another job whose trials accumulate alongside the existing ones.
`--force --repeats N` adds N trials to every item in scope — deliberate
sample-deepening — rather than ensuring a total. A later `grade
--from-solve` selects newly
verified solver trials that are not yet graded; `--force`
on `grade` adds independent grader trials for already-graded submissions.
Regrading under a revised rubric needs no dedicated command: a new rubric
is a new config identity, under which nothing is done yet, and prior
results stay untouched.

Run outcomes are loud. When planned items have done trials under a
different identity, the plan output says so — a configuration change
(config, prompt, environment, rubric, or task inputs) makes a full
re-run look like data loss unless the command explains that prior
results are kept under their old identity. After Harbor finishes,
`solve` and `grade` print a run summary over the invocation's jobs —
items requested, items
verified (solve) or graded (grade), items failed — naming each failed
item with the exact scoped rerun command; because failed items are
exactly the not-done ones, the rerun is incremental and needs no
`--force`. The command exits nonzero when any requested item failed,
even though Harbor exits zero whenever a job itself finishes: a
partially failed run must fail loudly in scripts. An item can also
succeed while ending below its target — graded, but on fewer pooled
valid trials than `--repeats` asked for — so the summary names each
such incomplete item with its pooled count against the target, and the
command exits nonzero for it too. Because `--repeats` is a target, a
plain re-run of the same command launches exactly the missing trials
(after a `--force` run, which adds rather than ensures, the summary
says to re-run with `--force` and the missing count). A final-judge
run additionally reports, at plan time, every item skipped for having
fewer usable prior gradings than `--min-gradings`, each with the exact
initial-grading top-up command. `grade --from-solve`
reports every in-scope solve trial it skips (a failed solve, or a
verified trial with an empty submission artifact) and warns, with the
scoped `aat solve` rerun command, for each assignment left with no
gradable submission at all — grading skips such trials by design, but
never silently.

The command surface (`--data-root PATH` selects the data root
explicitly, falling back to `AAT_DATA_DIR` and then `~/aat-data`; it is
location, not an experiment axis; a bare `--config NAME` resolves to
`configs/NAME.toml` relative to the current working directory, so run
from the repository root or pass an explicit path):

```text
aat solve  (--course ID [--assignment ID] | --all)
           --config NAME [--data-root PATH] [--repeats N]
           [--max-concurrent-trials N] [--force]
           [--gurobi-license-file PATH]
           [--dry-run] [--materialize-only]

aat grade  (--from-solve NAME [--course ID] [--assignment ID]
            | --submissions PATH
            | --course ID [--assignment ID] | --all)
           --config NAME [--data-root PATH] [--repeats N]
           [--sample N]
           [--context-from NAME --min-gradings N]
           [--max-concurrent-trials N] [--force]
           [--dry-run] [--materialize-only]

aat report [--course ID] [--assignment ID] [--config NAME]...
           [--seed N] [--out PATH] [--data-root PATH]

aat check-course --course ID [--data-root PATH]

aat intake (--course ID | --all) [--data-root PATH]
           [--model NAME] [--reasoning-effort LEVEL]
           [--force] [--dry-run] [--print-prompt]

aat ingest-submissions (--course ID | --all) [--data-root PATH]
           [--force] [--dry-run]

aat init-data [--data-root PATH] [--git]
```

`aat check-course` is read-only and writes nothing: it renders one
course's contract violations (nonzero exit until fixed), completeness
gaps, intake notes, and grade-coverage summary — the review loop of the
intake procedure (decision 14). `aat intake` runs the intake agent
sequentially over the selected unprocessed dumps (decision 14): model
and effort are flags with pinned defaults, not an experiment config,
because intake has no config identity — the receipt records them as
provenance and doneness is the raw-dump hash alone. `--force` includes
processed and hand-built courses; a failed agent run writes no receipt,
so re-running the command is the retry mechanism; `--print-prompt`
emits the rendered brief for an interactive session instead of
launching anything. Each run's output is teed to
`scratch/intake/<stamp>__<course>.log`. `aat ingest-submissions` runs
submission ingest (decision 15) over the selected unprocessed dumps
under `raw-submissions/`: deterministic code with the same
receipt-hash doneness, printing each course's review summary and
exiting nonzero while any submission is skipped or frozen (the
receipt records the outcome counts, so an unchanged course's
unresolved rows are re-reported rather than silently passing) —
re-running after a `manifest.toml` fix is the retry mechanism, and
`--force` reprocesses courses whose dump is unchanged. `aat init-data` creates the
resolved data root — the directory, its top-level layout, and a short
README — because resolution itself never creates anything
(data-conventions.md); it is idempotent, and `--git` additionally makes
the root a private git repository with a `.gitignore` for the
regenerable directories. `aat report` is read-only: it changes
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
legitimate. There is no combined solve-then-grade command
(manual chaining is fine, and the solve/grade separation is
load-bearing) and no rich selection syntax (globs, exclusions); both
wait on a real run that needs them ([roadmap.md](roadmap.md)). Grading selection is settled: `--from-solve NAME` grades
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
job — and the skip is reported at selection time ("Run outcomes are
loud", above). One grading materializer underneath, two source resolvers on top,
per decision 5.

A final-judge run is `aat grade` with a `judge = true` config plus
`--context-from NAME --min-gradings N`; all three legs are required
together — a judge config without context would grade blind, and
context supplied to an ordinary grader config would change the
experiment without changing its identity. The `judge` key and the
judge prompt template also travel together, enforced at config load: a
mismatch would materialize tasks whose instruction and verifier
disagree about the deliverables, failing every trial after full agent
cost. The context config must be a
non-judge grading config (a judge of judges is rejected) naming the
same rubric as the judge config — prior gradings measured against a
different point split could not be reconciled criterion by criterion.
Item
selection is unchanged — any submission source, `--sample` included —
and for each selected item the prior gradings are looked up by the
*context* config's own per-item identity (its config identity plus the
item's resolved inputs, including the context config's rubric), so the
judge consumes exactly the gradings that pool together under the
frozen initial config; a context rubric that has since advanced
matches nothing, which is correct — the initial rounds under the new
rubric do not exist yet. A valid grading whose stored artifacts are
missing on disk is unusable and counted in the skip report. Items
short of `--min-gradings` are skipped loudly with the exact top-up
command — when unusable gradings exist the command uses `--force` with
the usable shortfall, because the plain target already counts the
artifact-less trials as done — and a run that skipped any item exits
nonzero (except `--dry-run`): a judge run that judged less than it was
asked must fail loudly in scripts. The judge's own doneness behaves
like any grading item under the judge config's identities.
