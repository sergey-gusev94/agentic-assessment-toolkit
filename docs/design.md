# Agentic Assessment Toolkit Design

This is the decision and design layer between the vision in
[brief.md](brief.md) and implementation. [research.md](research.md) is a
frozen research snapshot (2026-07-30) that surveys the landscape; this
document records what was actually decided and what is being built. When the
two disagree, this document wins.

## Revisions

- **2026-07-31 (b).** Deliverables are source-format: the solver output
  contract requires executed notebooks and report/document *source*
  (Markdown or LaTeX source); compiled PDFs are never required, so the
  `latex` environment flavor is deferred and no flavor needs TeX. Grading
  tasks always use one dedicated `grading` environment flavor with
  document-reading tools (PDF text extraction, spreadsheet reading) —
  handouts, reference solutions, and student submissions contain PDFs
  that the grader must read as data. Solve flavors include the same
  reading tools because handouts are PDFs. The optimization flavor ships
  HiGHS as the license-free default solver and installs `gurobipy`
  with no license baked in: Gurobi runs are enabled at run time by
  injecting academic WLS credentials from outside the repository
  (credentials are never committed, per
  [data-conventions.md](data-conventions.md)). The config identity is
  broadened to fold in the stage's verifier bytes and its rendered task
  skeleton bytes, while each item's resolved environment template bytes
  (and rubric bytes, for grading) fold into a per-item identity; the run
  record gains the toolkit's own version. Small contract clarifications:
  at least one criterion must be non-bonus (`raw_max > 0`);
  `grading_output/` may contain extra scratch files, but the two
  required deliverables must be present and valid; for `--from-solve`,
  each completed solve trial with a non-empty submission artifact is one
  gradable item (trials without one are skipped and stay visible as
  explicit outcomes in the solve job); materialized task files are
  byte-deterministic (no timestamps or absolute paths), so
  golden-fixture tests compare byte-exact.
- **2026-07-31.** Grading moved out of RewardKit verifiers and standalone
  host runs into ordinary Harbor grading jobs, so solving and grading share
  one orchestration path. RewardKit is descoped: its rubric machinery is
  replaced by a grader prompt template and an output schema. `harbor job
  regrade` is descoped: regrading is re-running the grading job over
  unchanged artifacts. Per-assignment deterministic tests are descoped: each
  stage keeps one generic contract verifier, and grades come from the LLM
  grader's artifacts. Grading justifications are Markdown initially;
  LaTeX/PDF output is a later prompt change.
- **2026-07-31.** Full agent capability is the default in both stages:
  public network access and the agents' normal toolset, for solving and
  grading alike. The provider-only egress restriction on grading jobs is
  descoped to a later-on-demand hardening option; the associated
  credential-exposure risk is accepted explicitly in decision 9.
- **2026-07-31.** The toolkit is a thin wrapper over Harbor at run time:
  `aat solve` and `aat grade` materialize tasks and then invoke
  `harbor run` as a subprocess, writing a run record (exact Harbor
  version, agent and model configuration, effective command line, input
  hashes) beside the job output. Harbor becomes the single version-bounded
  runtime dependency. Repository tests still never invoke Harbor or
  Docker; they assert on materialized files and constructed command lines.
- **2026-07-31.** Clean-slate specification. The manual pilots and the
  reference repositories are demoted to evidence: nothing is ported from
  them, and every contract — grading output schema, reward semantics,
  prompt templates, verifiers, environment templates, config identity,
  run records — is specified fresh in this document and
  [data-conventions.md](data-conventions.md). Rewards are `score_pct` on
  a 0–100 scale over required criteria; the pilot's course-style bonus
  reward (e.g. 110.0) is superseded. Environment templates are
  per-course flavors (data-science and optimization first) selected via
  course defaults and per-assignment overrides. Experiment configs are
  TOML files committed under `configs/`.

## Decisions

Each entry is a commitment, with a one-line rationale. Alternatives and full
analysis are in research.md.

1. **Harbor is the foundation for solving and grading, used natively.**
   Harbor's task directories, datasets, job outputs, ATIF trajectories, and
   viewer are the formats of this project, for solve jobs and grading jobs
   alike. No abstraction seam or backend interface is built around Harbor:
   durable assets are data (plain directories, files, recorded hashes), so
   portability comes from data conventions, not code. If a migration is ever
   needed, it is a one-time conversion script written then.
2. **The toolkit stays thin.** It contains only domain code no framework can
   provide: importers and task materializers (assignments into solve tasks,
   submissions into grading tasks), solver and grader prompt templates,
   environment (Dockerfile) templates, the two generic contract verifiers,
   the grading output schema, sanity-check task generation, statistics, the
   discrepancy report, anonymization helpers, and the thin `aat solve` /
   `aat grade` commands that construct and launch Harbor runs. It does not
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
   solution and rubric." A submission directory can be a Harbor solve
   artifact or a real student folder; the machinery is identical and the
   only difference is which directory is materialized into the grading
   task. Repeated grading for variance estimates is Harbor's native `-k`
   repeated attempts. A rubric is an optional Markdown file: when present
   it is the authority on point splits; when absent the grader defines a
   reasonable split and must state it in the justification.
6. **Grades come from the grader's artifacts, not from verifier scoring.**
   Each stage has exactly one generic contract verifier, reused across all
   tasks; per-assignment test code is never written. The solve verifier is a
   0/1 output-contract check, agnostic to what the deliverable is: the
   submission directory exists, is non-empty, differs from the materialized
   inputs, and contains no bookkeeping files. Evidence-completeness
   expectations — executed notebooks, report/document source files
   (Markdown or LaTeX source; compilation is never required), saved
   outputs, where applicable — live in the solver prompt's output
   contract and are judged by the grader, not checked mechanically. The grading verifier
   validates that `grading_result.json`
   exists, parses, and satisfies its internal-consistency rules, and
   surfaces its `score_pct` as the Harbor reward so grades appear in the
   viewer. `score_pct` is computed over required criteria only on a
   0–100 scale; bonus points are recorded in the grading result but
   never enter the reward (see the contracts section). The deliverables
   are the JSON plus a written per-problem Markdown justification.
7. **Regrading is re-running the grader.** Solving and grading are separable
   in time because grading consumes only stored artifacts; a revised rubric
   or grader prompt means a new grading job over the same submission
   directories. No dependency on `harbor job regrade`, no post-release
   Harbor pinning, no separate verifier environment.
8. **Judge validity without deterministic tests.** Benchmark and
   grading-assistant scores are LLM-grader scores under one frozen grader
   configuration (prompt, model, effort, rubric), versioned with the
   results, not proxies for professor grades. Validity comes from the
   sanity trio — the reference solution grades at or near full marks, an
   empty or irrelevant submission grades near zero, and repeated gradings of
   one submission are stable — plus repeated grading for distributions and
   explicit failure accounting. Calibration against trusted human grading is
   deferred until a trustworthy human-graded corpus exists.
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
results viewer                       sanity-check task generation
                                     statistical metric.py
                                     discrepancy report for professors
                                     anonymization helpers for student data
```

### Benchmark pipeline

```text
data root: courses/<id>/assignments/<n>/     (immutable, hashed)
        ↓  importer + solver prompt template (experiment config)
Harbor solve job: agent solves in isolated container
  (public network, credential inside, trusted professor-authored task)
        ↓  generic solve verifier: 0/1 contract check
artifacts, raw transcript, ATIF trajectory,
status, duration, token usage
        ↓  grading-task materializer (same path as student grading)
Harbor grading job                            → see grading pipeline below
        ↓
metric.py: score distributions, repeat variance, pass-at-k, bootstrap CIs
clustered by assignment, explicit failure accounting
```

### Grading pipeline

```text
submission directory
  (Harbor solve artifact OR submissions/<course>/<student>/<assignment>/)
        ↓  grading-task materializer: submission + reference solution
           + rubric + grader instruction (static inspection; submission
           and reference are read as data, never executed)
Harbor grading job: grader agent in isolated container
  (public network, -k repeats for variance)
        ↓  generic grading verifier: validate grading_result.json,
           surface score_pct as the reward
grading_result.json + per-problem Markdown justification
        ↓  where a professor grade exists
discrepancy report: side-by-side scores, flagged disagreements
```

## Contracts

Concrete specifications for the vertical slice. Nothing here is ported
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
  the score), and optional `bonus` (boolean, default false). At least
  one criterion must be non-bonus, so `raw_max > 0` and `score_pct` is
  always well defined.
- `raw_points`, `raw_max` — sums over non-bonus criteria.
- `bonus_points`, `bonus_max` — sums over bonus criteria (0 when none).
- `score_pct` — `100 * raw_points / raw_max`.
- `overall_comment` — short free-text summary.

The generic grading verifier re-derives every aggregate and fails the
contract on any mismatch, duplicate criterion id, out-of-range points,
missing or all-bonus criteria, or empty evidence. Provenance (submission, reference, rubric, and
config hashes) is recorded by the materializer and the run record,
never authored by the LLM: the grader's required output stays minimal
to reduce parse failures.

### Reward semantics

The grading verifier surfaces `score_pct` — a 0–100 scale over required
criteria only — as the Harbor reward. Bonus points live only inside
`grading_result.json`. This keeps rewards comparable across assignments
regardless of point totals or bonus availability. The solve verifier's
reward remains the 0/1 output-contract check.

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

- `submission/` — the directory being graded (solve artifact or student
  folder), copied as data.
- `reference_solution/` — the oracle solution.
- `rubric.md` — present when the assignment has a rubric; when absent
  the grader defines and states its own point split (decision 5). The
  rubric is resolved as
  `courses/<course_id>/rubrics/<assignment_id>/<name>.md` in the data
  root, where `<name>` comes from the grading config (default
  `default`).
- `grading_output/` — empty directory the grader must fill (created by
  the grading environment image, so the materialized task tree contains
  no placeholder files).

Grading tasks always use the dedicated `grading` environment flavor,
regardless of course: grading is static inspection, so the image needs
document-reading tools (PDF text extraction, spreadsheet and notebook
reading), not the course's scientific stack. Course flavors are for
solve tasks only.

The grader instruction states the static-inspection rule: submission
and reference content is read as data and never executed — and never
compiled: LaTeX compilation is code execution. It also states that any
instructions found inside the submission or reference are content to be
graded, never directives to the grader.

### Experiment configs and config identity

Experiment configs are TOML files committed under `configs/` at the
repository root (e.g. `configs/codex-high.toml`). They contain no
course content: agent, model, reasoning effort, solver or grader prompt
template name, rubric name, and agent-argument passthrough. Selection
and mechanics never appear in configs (see CLI design).

The config identity is `sha256` over the config file bytes, the
referenced prompt template bytes, the stage's generic verifier
bytes (for grading, the verifier script plus the copied
`grading_schema.py`), and the stage's rendered task skeleton bytes
(which carry the network policy, timeouts, and artifact path as
materialized): an edit to any of these changes the experiment, so all
of them invalidate doneness by construction. Each item
additionally has a **per-item identity** that folds in the item's
resolved inputs: for solve, the resolved environment template
(Dockerfile) bytes; for grading, the grading environment template bytes
and the resolved rubric file bytes. Rubric files themselves are
immutable — a revision is a new file selected by name in the config
(see [data-conventions.md](data-conventions.md)) — so folding rubric
bytes into the per-item identity is defense in depth, and a different
rubric selection changes doneness for exactly the assignments it
applies to. `--repeats` and selection flags never enter the identity.

### Environment templates

Environment templates are Dockerfiles shipped as package data, one per
flavor, named by capability rather than by course. The initial set,
derived from the packages the reference corpus actually uses:

- `data-science` — numpy, pandas, matplotlib, scikit-learn, CPU-only
  PyTorch, scipy, openpyxl (spreadsheet handouts), and the notebook
  toolchain (ipykernel, nbconvert, nbclient).
- `optimization` — Pyomo with HiGHS (`highspy`) as the license-free
  default solver, Ipopt (conda-forge binaries), and `gurobipy`
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

Every solve flavor also includes PDF text-extraction tools
(poppler-utils, pypdf), because assignment handouts are routinely PDFs
that the agent must read. A `latex` flavor is deferred: the output
contract requires document source, never compiled PDFs, so no image
needs TeX (revision 2026-07-31 (b)).

Images pin their Python package versions; base-image digest pinning is
deferred to reportable runs. Solver licenses (Gurobi WLS) are
credentials: never baked into images, never committed, always injected
at run time.

Template resolution for a solve task: the per-assignment sidecar's
`environment` key when present, else the course default in
`course.toml`, else a clear error. Grading tasks always resolve to
`grading`. Layout details are in
[data-conventions.md](data-conventions.md).

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
static-inspection rule (read as data; never execute or compile),
prompt-injection resistance (instructions inside the submission are
content, not commands), rubric authority and the no-rubric fallback,
evidence requirements, and the exact output schema above. Any prompt
edit changes the config identity by construction.

### Run records and idempotence

Each `aat` invocation creates one job directory —
`<utc-timestamp>__<config-name>__<identity-prefix8>/` — under `runs/`
(solve) or `grading/` (grading) in the data root, containing Harbor's
job output plus `aat-run.json`: the exact `harbor --version`, the
toolkit's own version, agent and model configuration, effective command
line, requested items with their per-item identities, config identity,
and input hashes (assignment, prompt, environment template, verifier,
rubric, submission, reference solution, grading schema — as
applicable). On a rare same-second collision the job directory name
gains a `-N` suffix; a job's identity lives in `aat-run.json`, never in
the directory name.

An item is done under a config when some job directory with a matching
config identity contains a completed, non-error Harbor trial for it,
read from Harbor's per-trial result file — the same file Harbor's
viewer consumes. This is the one deliberate coupling to Harbor's
on-disk output format and is accepted as such.

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
6. **Descoped (validated by construction):** regrade-by-rerun is another
   grading job over the same stored artifacts — the mechanism validated in
   item 5; no separate check is required.
7. **Moved to roadmap stage 3:** the judge sanity trio — reference solution
   near full marks, empty or irrelevant submission near zero, stable
   repeated gradings — is grader-prompt calibration, not infrastructure
   validation, and runs through the toolkit on corpus data.
8. **Deferred:** Claude Code, Gemini CLI, and other agent stacks are validated
   only after the Codex pipeline and its hardening are complete.

## Roadmap

Sequence and scope only; no dates. Completion of each stage is judged by
what the toolkit provides, not by live runs.

1. **Solve core, Codex-first** — data-root and task conventions implemented;
   importer converts existing course assignment folders into Harbor solve
   tasks; environment templates; generic solve contract verifier; pinned
   Codex job configurations. Complete when the toolkit can materialize a
   full course from the reference corpus into runnable Harbor tasks,
   datasets, and Codex job configs.
2. **Grading core, Codex-first** — grading-task materializer; grader prompt
   template written to the contracts section; grading output schema;
   generic grading verifier; grading environment template. Complete when an
   agent solution and a real student folder both grade through the
   identical path — the vertical-slice acceptance criterion below.
3. **Validity and statistics** — sanity-trio task generation (oracle,
   garbage, repeat stability); `-k` repeat configurations; explicit failure
   accounting; bootstrap confidence intervals; frozen grader-config
   versioning for reportable runs; discrepancy report against a professor's
   grade export; anonymization helpers; evaluation across the target
   corpus.
4. **Additional agent stacks** — integrate and validate Claude Code, Gemini
   CLI, and other agents as solvers and graders; add cross-agent
   configurations and comparisons only after the complete Codex pipeline is
   hardened.
5. **Later, on demand** — LaTeX/PDF grading justifications (a grader prompt
   change); compiled-document deliverables (a `latex`-capable flavor plus
   an output-contract line) if the "does it compile" signal is ever
   wanted; optional format-aware submission lints (e.g. notebook executed,
   document compiled) if failure accounting shows the need; judge
   calibration if a trusted human-graded corpus emerges;
   restricted network egress, host-side hardening, or a credential broker
   if grading ever faces adversarial submissions; MLflow if Harbor's viewer
   becomes insufficient;
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
   model, effort, `-k`, concurrency) emitted alongside materialized
   datasets.
10. **Thin CLI wrapping Harbor** — `aat solve` and `aat grade`
    (installed as the `aat` console script): materialize, then invoke
    `harbor run` as a subprocess; `--materialize-only` exposes the
    file-writing layer alone. Each run writes a run record (exact
    `harbor --version`, toolkit version, agent and model configuration,
    effective command line, input hashes) beside the job output.

Each step lands with deterministic offline tests over small synthetic
fixtures (a fake course and a fake submission under `tests/fixtures/`),
consistent with AGENTS.md. Stage 3 work (statistics, discrepancy report,
anonymization, sanity-trio generation) starts after the slice passes its
golden-fixture acceptance tests and the maintainer live-validates one
real assignment end to end.

Module layout, mapping one-to-one onto the build order:

```text
src/agentic_assessment_toolkit/
├── data_root.py           # step 1: resolution + refusal rules
├── hashing.py             # shared: file/dir sha256 for provenance
├── grading_schema.py      # step 2: fields + consistency validation
├── config.py              # experiment-config loading + identity
├── materialize/
│   ├── _common.py         # shared materializer helpers (naming,
│   │                      #   Dockerfile/test-runner emission)
│   ├── solve.py           # step 4: assignment → Harbor solve task
│   └── grading.py         # step 6: submission → Harbor grading task
├── jobs.py                # step 9: pinned job-config emission
├── harbor.py              # step 10: harbor command construction,
│                          #   subprocess invocation, run record
├── cli.py                 # step 10: argparse, `aat solve` / `aat grade`
└── templates/             # package data (importlib.resources)
    ├── prompts/           # step 3: solver.md, grader.md
    ├── verifiers/         # steps 5 + 7: two standalone scripts
    ├── environments/      # step 8: one Dockerfile per flavor
    └── task/              # task.toml skeleton
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
- **Harbor is the single runtime dependency**, version-bounded in
  `pyproject.toml` and invoked through its CLI — its stable interface and
  what the pilots validated — never through its internal Python API.
  Docker and the agent CLIs remain documented external requirements that
  packaging cannot provide. Other Python dependencies stay at zero
  (argparse over click, hand-rolled validation over pydantic or
  jsonschema); additions require a recorded decision.

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
3. **Mechanics** (CLI flags): `--repeats N` (Harbor's `-k`; sampling
   depth, see below), `--force`, `--dry-run` (list what would run, then
   exit), `--materialize-only`.

Sampling depth is not experiment identity. `--repeats` changes how many
trials are drawn, not the system under test or the judge, so it is
excluded from the config identity hash (though recorded in the run
record). Trials pool by (item, config identity) across any number of jobs
in `metric.py`: five repeats now and five later under the same config are
one sample of ten. Pooling is valid only while the config is truly
frozen — any prompt or rubric edit must be a new config version, which
the identity hash enforces automatically.

Idempotence: an item is **done** under a config when at least one
completed trial exists for (item, config identity), derived from the data
root layout — no separate bookkeeping state. Done items are skipped by
default, so re-running a bulk command is naturally incremental ("grade
what was not yet graded"). `--force` never overwrites: it launches
another job whose trials accumulate alongside the existing ones.
Regrading under a revised rubric needs no dedicated command: a new rubric
is a new config identity, under which nothing is done yet, and prior
results stay untouched.

The surface is two commands (`--data-root PATH` selects the data root
explicitly, falling back to `AAT_DATA_DIR`; it is location, not an
experiment axis; a bare `--config NAME` resolves to
`configs/NAME.toml` relative to the current working directory, so run
from the repository root or pass an explicit path):

```text
aat solve  [--course ID] [--assignment ID] [--all]
           --config NAME [--data-root PATH] [--repeats N] [--force]
           [--dry-run] [--materialize-only]

aat grade  (--from-solve NAME [--course ID] [--assignment ID]
            | --submissions PATH
            | --course ID [--assignment ID] | --all)
           --config NAME [--data-root PATH] [--repeats N] [--force]
           [--dry-run] [--materialize-only]
```

New options must pass the axis test: if it changes the experiment, it
belongs in a config file; if it changes selection or mechanics, a flag is
legitimate. Deliberately deferred: a combined solve-then-grade command
(manual chaining is fine, and the solve/grade separation is load-bearing)
and rich selection syntax (globs, exclusions) until a real run needs
them. Grading selection is settled: `--from-solve NAME` grades
completed, not-yet-graded solve trials produced under the named solve
config, optionally narrowed by `--course`/`--assignment`; without it,
`--submissions PATH` or `--course`/`--assignment` select student
folders from the submissions tree. Each completed solve trial with a
non-empty submission artifact is one gradable item — a solve config run
with `-k 5` yields five submissions per assignment, each graded (and
repeatable-graded) independently; trials whose artifact is missing or
empty are skipped and stay visible as explicit outcomes in the solve
job. One grading materializer underneath, two source resolvers on top,
per decision 5.
