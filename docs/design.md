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
4. **Two pipelines, one grading core.** Benchmarking (agents solve
   assignments inside Harbor sandboxes) and grading assistance (static
   grading of real student work) share rubrics, judge configuration, and
   reporting. The grading assistant does not use Harbor: RewardKit runs
   standalone, and student code is never executed.
5. **Strict code–data separation.** The repository is always publishable; all
   real data lives in an external data root. Specified in
   [data-conventions.md](data-conventions.md).
6. **Judge validity without human calibration.** Benchmark scores are
   rubric-based judge scores under one frozen judge configuration, not
   proxies for professor grades. Deterministic checks carry as much of the
   grade as possible; the judge must pass sanity checks (oracle near full
   marks, garbage near zero, stable repeats). Calibration against trusted
   human grading is deferred until a trustworthy human-graded corpus exists.
7. **Grading assistance is policy-agnostic.** The toolkit produces grades,
   per-criterion scores, and evidence. How they are used relative to human
   grading is university policy and out of scope.
8. **No second MVP.** The reference repositories are the proof of value; the
   durable system is built directly — no intermediate throwaway.
9. **Live validation is outside repository work.** All live execution —
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

For the maintainer's own external checklist, the assumptions the foundation
choice rests on are:

1. Codex CLI solves a Harbor task using ChatGPT-subscription auth
   (`CODEX_FORCE_AUTH_JSON=1`), with no API billing.
2. Claude Code solves the same task using a subscription OAuth token
   (`CLAUDE_FORCE_OAUTH=1`), with no API billing.
3. The full submission directory survives as a declared artifact, and raw
   transcripts plus ATIF trajectories are captured.
4. A verifier in a separate container scores the solution with mixed
   deterministic + rubric criteria and multiple score dimensions.
5. `harbor job regrade` rescores the recorded solution under a modified
   rubric without rerunning the solver, on a pinned Harbor version that
   contains regrade.
6. RewardKit runs standalone on the host against a copied student-style
   solution using cached subscription auth (no `OPENAI_API_KEY` set).

## Roadmap

Sequence and scope only; no dates. Completion of each stage is judged by
what the toolkit provides, not by live runs.

1. **Benchmark core** — data-root and task conventions implemented; importer
   converts existing course assignment folders into Harbor tasks;
   environment and rubric templates; oracle and task validation; pinned job
   configurations; statistical `metric.py`. Complete when the toolkit can
   materialize a full course from the reference corpus into runnable Harbor
   tasks, datasets, and job configs.
2. **Grading assistant** — standalone static-grading pipeline with
   anonymization, per-criterion output, and the discrepancy report against a
   professor's grade export, covering what the reference-repo scripts did ad
   hoc.
3. **Benchmark hardening** — repeated-attempt configurations, failure
   accounting, confidence intervals, judge sanity-check fixtures,
   prompt-injection cases for the grader, version pinning for reportable
   runs.
4. **Later, on demand** — judge calibration if a trusted human-graded corpus
   emerges; MLflow if Harbor's viewer becomes insufficient; institutional or
   open-source packaging.
