# Roadmap

Planned work only. Everything implemented is described in
[design.md](design.md) and the other contract documents; per AGENTS.md,
when an item here lands, its entry is deleted and the built behavior is
documented there, rewritten as a description of what exists, not a
relocation of this planning text. Each entry states what would be built
and the condition that triggers building it. No dates, no history.

## Next

- **A third agent stack**, Gemini CLI, or any other agent Harbor
  supports, as solver and grader. What it takes: an agent fragment under
  `tools/environments/` so every flavor gets a rendered template with
  that agent's CLI baked in, an entry in the agent-to-suffix mapping,
  launch-time authentication for its subscription, and committed solve,
  grader, and judge configs. Until then, such an agent still runs. It
  resolves the plain `<flavor>.Dockerfile`, and Harbor installs the CLI
  per trial, on Harbor's own authentication. Built when a comparison
  needs that agent.

## Later, on demand

- **Initial-grader feedback enrichment**, extending the initial
  grader prompt to also author learning-opportunity notes; if
  judge-produced feedback proves thin in practice because issues the
  initial rounds noticed never reached their written justifications.
  Not before: it multiplies every initial round's cost for material
  the judge's synthesis mostly supersedes, and any prompt edit moves
  the config identity.
- **Fail-fast on repeated authentication failures**, aborting a job
  after several consecutive auth-category trial failures, instead of
  letting an expired token fail every remaining trial; if mid-run
  token expiry recurs. Recovery already exists (target-count
  `--repeats` re-runs launch exactly the missing trials), so this is
  purely about not burning time on a doomed job.
- **Professor-grade comparison**, grade-export ingestion under
  `tables/`, the discrepancy report (matched pairs with explicit
  de-duplication, bias, MAE, RMSE, correlation and concordance,
  cluster-bootstrap intervals), and the anonymization helpers; when
  comparing LLM grades to professor grades becomes a real need. Every
  grade and its provenance are stored, including the per-question
  Gradescope grade summaries submission ingest splits off into
  `tables/<course_id>/gradescope-summaries/` and any LMS grade-export
  CSVs kept beside the raw dumps, so this is retroactively computable
  at any time.

  Two constraints the design must respect when it is built. Human
  grades are a **diagnostic**, never a target: a systematic gap points
  at a rubric defect to investigate, and closing it is never the goal,
  because human application of a rubric carries mistakes, one-off
  regrade adjustments, and inconsistent strictness that the rubric must
  not inherit. And the applied scheme's administrative items must be
  separated out, not summed into the comparison. Real exports carry
  "late submission" deductions and discretionary "point adjustment"
  awards inside the academic questions, which the toolkit's rubrics
  deliberately exclude.

  A cheap first slice exists: per-question score exports (Gradescope's
  `<assignment>_scores.csv`, whose headers carry each question's
  maximum) give both the applied point structure and every human
  per-question score without parsing a PDF. That structure is also the
  evidence a rubric's `applied_scheme` provenance rests on, so
  extracting it mechanically would let `aat check-course` compare a
  rubric's maxima against the scheme the course graded by. That
  comparison is made by hand at intake review today.
- **Rubric content lints**, flagging administrative language
  (signatures, names on pages, boxed answers, lateness), scaling or
  normalization instructions, and prose disclaiming coverage of part of
  an assignment; if a rubric ships with these defects despite the
  intake rules and the grader prompt's administrative override. As
  regular-expression heuristics over prose these would fire on ordinary
  course vocabulary, so the bar is evidence that review is not catching
  them. A declared total checked against the criterion sum is not on
  this list at all: a rubric can state its total correctly and still
  take it from the wrong authority, which is what provenance and
  coverage review address.
- **Machine-enforced score levels**, an `allowed_scores` set per
  criterion, validated by the grading verifier; if graders are observed
  awarding values off the levels a rubric lists often enough to matter.
  The rubric states its levels and the grader prompt requires awarding
  exactly one of them, so the first step is measuring how often that is
  violated rather than constraining the schema.
- **Deadline-cutoff selection for submission ingest**, filtering or
  flagging uploads after a per-assignment due date, computable from
  the `submitted_at` timestamps ingest records for Brightspace uploads
  (Gradescope exports carry none; their times would come from a
  grades CSV); when late-submission policy enforcement is actually
  needed.
- **Model-assisted merge review**, an LLM pass over flagged
  multi-upload merges (`possible_stale_solution` and friends) to
  propose which files to keep; if human review shows the deterministic
  union-with-supersession policy keeping wrong content in practice.
- **Judge calibration against human grading**; if a trustworthy
  human-graded corpus emerges.
- **LaTeX/PDF grading justifications**, a grader prompt change; if
  formatted justifications are ever wanted.
- **Compiled-document deliverables**, a `latex`-capable environment
  flavor plus an output-contract line; if the "does it compile" signal
  is ever wanted.
- **Format-aware submission lints** (e.g. notebook executed, document
  compiled); if failure accounting shows the need.
- **Mechanical detection of grading-time input modification**, a hash
  manifest over the materialized submission and reference, reported by
  the grading verifier as data, not enforcement; if the
  static-inspection prompt rule is ever observed being violated.
- **Structured unreadable-submission outcome**, a first-class grading
  result for artifacts that defeat the grader's full reading
  escalation, replacing the prompt's last resort (withhold
  `grading_result.json` and explain) with a schema field the verifier
  and statistics understand; if a grading trial ever actually
  exercises that last resort.
- **An `aat rubric` command** that drafts rubrics for review; until
  then, drafting stays manual per
  [data-conventions.md](data-conventions.md).
- **Pass@k**, when a pass threshold on `score_pct` is actually needed
  and chosen.
- **Intraclass correlation for repeat agreement, and config-vs-config
  significance tests** (bootstrap difference intervals); when
  multi-agent comparison arrives.
- **Restricted network egress, host-side hardening, or a credential
  broker**; before any adversarial or institutional deployment (the
  revisit condition of design decision 9).
- **Base-image digest pinning** for the environment templates; when exact
  container reconstruction across machines and dates is required.
- **A combined solve-then-grade command, and rich selection syntax**
  (globs, exclusions); when a real run needs them. Manual chaining is
  fine, and the solve/grade separation is load-bearing.
- **MLflow or another cross-experiment UI**, retroactively
  backfillable from the data root, because files are the source of
  truth; when multi-agent scale or non-Python consumers need one.
