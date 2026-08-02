# Agentic Assessment Toolkit Brief

## Vision

Agentic Assessment Toolkit is a Python toolkit built around one central idea:

> A single assignment-and-grading pipeline in which a "submission" can come
> either from an autonomous coding agent or from a real student.

The same immutable assignment, the same rubric, and the same grading machinery
serve two workflows:

1. **Benchmarking (primary goal).** Measure how well complete agent stacks —
   Codex CLI, Claude Code, Gemini CLI, and others, running on existing user
   subscriptions — solve real chemical engineering coursework: coding labs,
   Jupyter notebooks, data analysis, reports, and derivations.
2. **Grading assistance (supported side effect).** Reuse the grading machinery
   on real student submissions to produce an independent grade with
   per-criterion explanations, as an information tool for professors and
   teaching assistants.

The benchmark is the design driver. The grading assistant falls out of the same
pipeline because grading an agent's solution and grading a student's solution
are the same operation over a different submission source.

## Use case 1: benchmarking agents on coursework

The experimental object is a complete agent stack (scaffold + model + prompt +
effort + tools + limits), not a bare language model. Each trial takes an
immutable assignment directory, materializes it into an isolated workspace,
lets the agent solve it autonomously, preserves all artifacts and transcripts,
and grades the result later with a separate LLM grading run over the stored
artifacts.

Success for the benchmark is **validity**, not any particular score:

- Results are reproducible: pinned agent/CLI/framework versions, recorded
  model identifiers, hashed assignments, prompts, and rubrics.
- Solving and grading are separable in time: grading consumes only stored
  artifacts, so recorded solutions can be regraded under a revised rubric by
  re-running the grading job, never the solver.
- Failures (timeouts, refusals, parse errors, infrastructure errors) are
  explicit outcomes, never silently dropped.
- Repeated attempts yield distributions and confidence intervals, not single
  numbers.
- Scores are honest about what they are: rubric-based judge scores, not
  proxies for professor grades. Comparisons between agents are made within one
  frozen judge configuration (prompt, model, effort, rubric), which is
  versioned with the results.
- Per-assignment deterministic tests are never written. Each stage has one
  generic contract check, agnostic to the deliverable format (the solve
  stage: a submission was produced under the output contract; the grading
  stage: the grading result is structurally valid); the
  grade itself is the LLM grader's output.
- The judge passes the grader checks, which require no human grades: the
  reference solution scores at or near full marks, an irrelevant submission
  scores near zero, and repeated judgments of the same submission agree.

Calibration of the judge against trusted human grading is **explicitly
deferred**: no corpus of professor grades established as trustworthy exists
today, and calibrating against a noisy or biased reference would give false
confidence. If such a corpus is built later, human-agreement measurement can
be reintroduced.

## Use case 2: grading assistant for professors and TAs

For student submissions, the toolkit produces an independent grade,
per-criterion scores, and a written Markdown justification against the
reference solution and rubric. Its value is informational:

- Where the LLM grade and a professor's grade disagree, the disagreement
  itself is the signal — it can surface grading inconsistencies, rubric
  ambiguities, or biases.
- Agreement with the human grader is explicitly **not** the goal in this mode;
  auditable, well-evidenced reasoning is.

The toolkit is deliberately **policy-agnostic**: whether LLM grades are used
before human grading, after it as a cross-check, or (by institutional
decision) as the grade of record is university policy and outside this
project's scope. The toolkit only produces grades and evidence.

## Scope

Initial scope is the existing corpus: Purdue chemical engineering courses
(CHE 456, CHE 597CO, CHE 597DS, CHE 610) with Python, Jupyter notebook, data
analysis, LaTeX, and report assignments, as already exercised in
`reference_repos/`.

The initial implementation is **Codex-first**. Codex is the only required
solver and rubric-judge stack for the benchmark core, grading assistant, and
initial benchmark hardening. Solving and judging remain separate,
independently configured stages even when both use Codex. Claude Code, Gemini
CLI, other agent stacks, and cross-agent comparisons are explicitly deferred
until the complete Codex pipeline has been implemented and exercised across
the target corpus.

Deferred: MATLAB and proprietary desktop tools (Aspen Plus, HYSYS, COMSOL),
handwritten or scanned submissions, and web/GUI assignment tracks.

### Non-goals

The toolkit is a thin domain-specific layer over Harbor (see the
frozen research snapshot, [research.md](research.md)). It does not
implement:

- a new agent runner, sandbox framework, or model-provider abstraction;
- a new transcript schema, experiment database, or results viewer;
- course management, LMS features, or grade-of-record workflows;
- policy decisions about how grades are used.

It owns assignment import conventions, solver prompt/experiment
configurations, reusable scientific environments, grader prompt
templates, the generic contract verifiers, and
statistical reporting.

## Constraints and design rules

- **Subscription-backed execution.** Agents and judges run through existing
  Codex/Claude/Gemini subscriptions, not per-token API billing.
- **Assignments are immutable and prompt-free.** Solver prompts are experiment
  configuration, so one assignment can be tested across agents, models,
  prompts, and efforts.
- **Solve and grade are separate stages.** Everything needed for grading is
  captured as artifacts at solve time; regrading never reruns the solver.
- **Full agent capability by default; minimal trust model, accepted and
  documented.** Agents run with their normal toolset and public network
  access in both stages: assignments and grading may legitimately require
  web research, downloads, or checking cited sources. Grading reads
  untrusted submission content, so it is static inspection by prompt rule —
  submission code is never intentionally executed — inside disposable
  containers. The residual prompt-injection risk, including possible
  credential exposure from the grading container, is accepted at current
  scale and must be revisited before any adversarial or institutional
  deployment; any network restriction is a deliberate later choice, never a
  default.
- **Strict code–data separation.** The repository contains only code,
  documentation, templates, and synthetic test fixtures, and must always be
  safe to publish. All real data — assignments, reference solutions, student
  submissions, rosters, grades, run artifacts, transcripts, and trajectories —
  lives in a data root outside the repository, defaulting to `~/aat-data` with
  explicit path and environment-variable overrides, and is never committed.
  The separation is structural (data is not in the
  working tree at all), not a convention to remember, because autonomous
  agents operate in this repository and cannot be trusted to honor
  conventions. The data root may itself be versioned as a separate private or
  local-only repository; benchmark results reference assignments, prompts,
  and rubrics by hash, so published results never need to contain them.
  Nothing leaves the machine except calls to the model providers required for
  solving and grading. Concrete conventions are specified in
  [data-conventions.md](data-conventions.md).

## Status and positioning

Today this is a personal research tool. It should be built so it can later
become a tool for professors and TAs at the author's institution, and
potentially an open-source package for others — clean separation of code from
data, no hardcoded local paths in the package, documented conventions — but
those audiences are not current requirements.

Decisions and architecture are recorded in [design.md](design.md), and
planned work in [roadmap.md](roadmap.md). The underlying research — alternatives considered,
methodology, and security model — is documented in [research.md](research.md).
