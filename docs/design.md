# Agentic Assessment Toolkit Design

This is the decision and design layer between the vision in
[brief.md](brief.md) and implementation. [research.md](research.md) is a
frozen research snapshot (2026-07-30) that surveys the landscape; this
document records what was actually decided and what is being built. When the
two disagree, this document wins.

## Decisions

Each entry is a commitment, with a one-line rationale. Alternatives and full
analysis are in research.md.

1. **Harbor + RewardKit is the foundation, used natively.** Harbor's task
   directories, datasets, job outputs, ATIF trajectories, and viewer are the
   formats of this project. No abstraction seam or backend interface is built
   around Harbor: durable assets are data (plain directories, files, recorded
   hashes), so portability comes from data conventions, not code. If a
   migration is ever needed, it is a one-time conversion script written then.
2. **The toolkit stays thin.** It contains only domain code no framework can
   provide: importers, rubric and environment templates, oracle validation,
   statistics, and the grading-assistant pipeline. It does not implement an
   agent runner, sandbox framework, model abstraction, transcript schema,
   experiment database, or results viewer. Simplicity is a standing
   preference, not a hard budget.
3. **Subscription-backed execution.** Agents and judges run through existing
   Codex CLI / Claude Code / Gemini CLI subscriptions, not per-token API
   billing. Harbor supports this natively for solving; grading runs on the
   host as the authenticated user.
4. **Codex-first implementation.** The first complete benchmark and
   grading-assistant pipelines use Codex for both assignment solving and
   rubric-based judging. Benchmark core, static grading, validation,
   reporting, and hardening are completed for the Codex stack before Claude
   Code, Gemini CLI, or other agents are integrated. Solver and judge remain
   separate, independently configured runs. Multi-agent comparison remains a
   long-term goal, not an acceptance criterion for the initial vertical
   slices.
5. **Two pipelines, one grading core.** Benchmarking (agents solve
   assignments inside Harbor sandboxes) and grading assistance (static
   grading of real student work) share rubrics, judge configuration, and
   reporting. The grading assistant does not use Harbor: RewardKit runs
   standalone, and student code is never executed.
6. **Strict code–data separation.** The repository is always publishable; all
   real data lives in an external data root. Specified in
   [data-conventions.md](data-conventions.md).
7. **Judge validity without human calibration.** Benchmark scores are
   rubric-based judge scores under one frozen judge configuration, not
   proxies for professor grades. Deterministic checks carry as much of the
   grade as possible; the judge must pass sanity checks (oracle near full
   marks, garbage near zero, stable repeats). Calibration against trusted
   human grading is deferred until a trustworthy human-graded corpus exists.
8. **Grading assistance is policy-agnostic.** The toolkit produces grades,
   per-criterion scores, and evidence. How they are used relative to human
   grading is university policy and out of scope.
9. **No second MVP.** The reference repositories are the proof of value; the
   durable system is built directly — no intermediate throwaway.
10. **Live validation is outside repository work.** All live execution —
   Harbor runs, Docker, subscription-authenticated agent or judge calls,
   network access to model providers — is performed by the maintainer
   outside this repository. Repository work never attempts live runs and
   never depends on their results.

## What goes where

```text
Harbor owns                          Toolkit owns
-----------                          ------------
sandboxed execution                  assignment/dataset import conventions
agent adapters (Codex, Claude, ...)  materialized instruction generation
artifact + transcript capture        environment (Dockerfile) templates
trials, jobs, regrade                rubric templates (RewardKit format)
results viewer                       task + oracle validation
                                     pinned job configurations
RewardKit owns                       statistical metric.py
--------------                       grading-assistant pipeline
deterministic checks                 discrepancy report for professors
rubric items + aggregation           anonymization helpers for student data
LLM / agent judges
```

### Benchmark pipeline

```text
data root: courses/<id>/assignments/<n>/     (immutable, hashed)
        ↓  importer + solver prompt template (experiment config)
Harbor task (instruction.md generated; assignment + prompt hashes recorded)
        ↓  harbor run: agent solves in isolated container, public network
artifacts, raw transcript, ATIF trajectory
        ↓  separate verifier container
RewardKit: deterministic checks + rubric judge → multi-dimension scores
        ↓  harbor job regrade (rubric revisions, no re-solving)
metric.py: distributions, pass-at-k, bootstrap CIs clustered by assignment,
explicit failure accounting
```

### Grading-assistant pipeline

```text
data root: submissions/<course>/<student>/<assignment>/   (read-only)
        ↓  static inspection only — student code is never executed;
           no subscription credential in any environment touching it
RewardKit standalone: deterministic checks + rubric judge on the host
        ↓
per-criterion grades, written justification, evidence references
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
   shared task environment, writes structured reward details, and returns an
   aggregate reward without verifier errors.
5. **Pending:** a verifier in a separate environment scores a solution with
   mixed deterministic and rubric criteria and multiple score dimensions.
6. **Pending:** `harbor job regrade` rescores a recorded solution under a
   modified rubric without rerunning the solver, on a pinned Harbor version
   that contains regrade.
7. **Pending:** RewardKit runs standalone on the host against a copied
   student-style solution using cached Codex authentication while
   `OPENAI_API_KEY` is unset.
8. **Deferred:** Claude Code, Gemini CLI, and other agent stacks are validated
   only after the Codex pipeline and its hardening are complete.

## Roadmap

Sequence and scope only; no dates. Completion of each stage is judged by
what the toolkit provides, not by live runs.

1. **Benchmark core, Codex-first** — data-root and task conventions
   implemented; importer converts existing course assignment folders into
   Harbor tasks; environment and rubric templates; oracle and task
   validation; pinned Codex job configurations; statistical `metric.py`.
   Complete when the toolkit can materialize a full course from the reference
   corpus into runnable Harbor tasks, datasets, and Codex job configs.
2. **Grading assistant, Codex-first** — standalone static-grading pipeline
   using Codex as the initial rubric judge, with anonymization,
   per-criterion output, and the discrepancy report against a professor's
   grade export, covering what the reference-repo scripts did ad hoc.
3. **Codex benchmark hardening and corpus evaluation** — repeated-attempt
   configurations, failure accounting, confidence intervals, judge
   sanity-check fixtures, prompt-injection cases for the grader, version
   pinning for reportable runs, and evaluation across the target corpus.
4. **Additional agent stacks** — integrate and validate Claude Code, Gemini
   CLI, and other agents; add cross-agent configurations and comparisons only
   after the complete Codex pipeline is hardened.
5. **Later, on demand** — judge calibration if a trusted human-graded corpus
   emerges; MLflow if Harbor's viewer becomes insufficient; institutional or
   open-source packaging.
