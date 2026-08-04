# Roadmap

Planned work only. Everything implemented is described in
[design.md](design.md) and the other contract documents; per AGENTS.md,
when an item here lands, its entry is deleted and the built behavior is
documented there — rewritten as a description of what exists, not a
relocation of this planning text. Each entry states what would be built
and the condition that triggers building it. No dates, no history.

## Next

- **Additional agent stacks** — integrate and validate Claude Code,
  Gemini CLI, and other agents as solvers and graders, then add
  cross-agent configurations and comparisons; after the complete Codex
  pipeline is hardened (design decision 4).

## Later, on demand

- **Professor-grade comparison** — grade-export ingestion under
  `tables/`, the discrepancy report (matched pairs with explicit
  de-duplication, bias, MAE, RMSE, correlation and concordance,
  cluster-bootstrap intervals), and the anonymization helpers; when
  comparing LLM grades to professor grades becomes a real need. Every
  grade and its provenance are stored — including the per-question
  Gradescope grade summaries submission ingest splits off into
  `tables/<course_id>/gradescope-summaries/` and any LMS grade-export
  CSVs kept beside the raw dumps — so this is retroactively computable
  at any time.
- **Deadline-cutoff selection for submission ingest** — filtering or
  flagging uploads after a per-assignment due date, computable from
  the `submitted_at` timestamps ingest records for Brightspace uploads
  (Gradescope exports carry none; their times would come from a
  grades CSV); when late-submission policy enforcement is actually
  needed.
- **Model-assisted merge review** — an LLM pass over flagged
  multi-upload merges (`possible_stale_solution` and friends) to
  propose which files to keep; if human review shows the deterministic
  union-with-supersession policy keeping wrong content in practice.
- **Judge calibration against human grading**; if a trustworthy
  human-graded corpus emerges.
- **LaTeX/PDF grading justifications** — a grader prompt change; if
  formatted justifications are ever wanted.
- **Compiled-document deliverables** — a `latex`-capable environment
  flavor plus an output-contract line; if the "does it compile" signal
  is ever wanted.
- **Format-aware submission lints** (e.g. notebook executed, document
  compiled); if failure accounting shows the need.
- **Mechanical detection of grading-time input modification** — a hash
  manifest over the materialized submission and reference, reported by
  the grading verifier as data, not enforcement; if the
  static-inspection prompt rule is ever observed being violated.
- **Structured unreadable-submission outcome** — a first-class grading
  result for artifacts that defeat the grader's full reading
  escalation, replacing the prompt's last resort (withhold
  `grading_result.json` and explain) with a schema field the verifier
  and statistics understand; if a grading trial ever actually
  exercises that last resort.
- **PDF render-mismatch preflight at ingest** — a deterministic
  per-page check flagging PDFs whose pages render blank while
  containing substantial images or content streams (the
  hidden-content signature found in real submissions), reported as an
  ingest warning; if the grader prompt's blank-page rule proves
  insufficient or new corpora keep arriving with this signature.
- **Machine-readable rubric criteria manifest** — emitted by the
  materializer and checked by the grading verifier; if the
  rubric-fidelity rate proves materially below 100%.
- **An `aat rubric` command** that drafts rubrics for review; until
  then, drafting stays manual per
  [data-conventions.md](data-conventions.md).
- **Pass@k** — when a pass threshold on `score_pct` is actually needed
  and chosen.
- **Intraclass correlation for repeat agreement, and config-vs-config
  significance tests** (bootstrap difference intervals); when
  multi-agent comparison arrives.
- **Restricted network egress, host-side hardening, or a credential
  broker**; before any adversarial or institutional deployment (the
  revisit condition of design decision 9).
- **Base-image digest pinning** for the environment templates; for
  reportable runs.
- **A combined solve-then-grade command, and rich selection syntax**
  (globs, exclusions); when a real run needs them — manual chaining is
  fine, and the solve/grade separation is load-bearing.
- **MLflow or another cross-experiment UI** — retroactively
  backfillable from the data root, because files are the source of
  truth; when multi-agent scale or non-Python consumers need one.
- **Institutional or open-source packaging**; when there is an
  audience beyond personal research.
