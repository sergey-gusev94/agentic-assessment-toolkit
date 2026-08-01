# Agentic Assessment Toolkit Design

This is the decision and design layer between the vision in
[brief.md](brief.md) and implementation. [research.md](research.md) is a
frozen research snapshot (2026-07-30) that surveys the landscape; this
document records what was actually decided and what is being built. When the
two disagree, this document wins.

## Revisions

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
   discrepancy report, and anonymization helpers. It does not implement an
   agent runner, sandbox framework, run orchestrator, model abstraction,
   transcript schema, experiment database, or results viewer. Simplicity is
   a standing preference, not a hard budget.
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
   repeated attempts.
6. **Grades come from the grader's artifacts, not from verifier scoring.**
   Each stage has exactly one generic contract verifier, reused across all
   tasks; per-assignment test code is never written. The solve verifier is a
   0/1 output-contract check, agnostic to what the deliverable is: the
   submission directory exists, is non-empty, differs from the materialized
   inputs, and contains no bookkeeping files. Evidence-completeness
   expectations — executed notebooks, compiled documents, saved outputs,
   where applicable — live in the solver prompt's output contract and are
   judged by the grader, not checked mechanically. The grading verifier
   validates that `grading_result.json`
   exists, parses, and satisfies its internal-consistency rules, and
   surfaces its `score_pct` as the Harbor reward so grades appear in the
   viewer. The deliverables are the JSON plus a written per-problem Markdown
   justification.
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
6. **Pending:** the same grading task re-run with a revised rubric over
   unchanged artifacts (regrade-by-rerun).
7. **Pending:** judge sanity trio — reference solution near full marks,
   empty or irrelevant submission near zero, stable repeated gradings.
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
   template ported from the reference-repo grader; grading output schema;
   generic grading verifier; grading environment template; discrepancy
   report against a professor's grade export; anonymization helpers.
   Complete when an agent solution and a real student folder both grade
   through the identical path.
3. **Validity and statistics** — sanity-trio task generation (oracle,
   garbage, repeat stability); `-k` repeat configurations; explicit failure
   accounting; bootstrap confidence intervals; frozen grader-config
   versioning for reportable runs; evaluation across the target corpus.
4. **Additional agent stacks** — integrate and validate Claude Code, Gemini
   CLI, and other agents as solvers and graders; add cross-agent
   configurations and comparisons only after the complete Codex pipeline is
   hardened.
5. **Later, on demand** — LaTeX/PDF grading justifications (a grader prompt
   change); optional format-aware submission lints (e.g. notebook executed,
   document compiled) if failure accounting shows the need; judge
   calibration if a trusted human-graded corpus emerges;
   restricted network egress, host-side hardening, or a credential broker
   if grading ever faces adversarial submissions; MLflow if Harbor's viewer
   becomes insufficient;
   institutional or open-source packaging.
