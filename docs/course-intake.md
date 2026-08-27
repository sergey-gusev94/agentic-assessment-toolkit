# Course Intake

How raw course materials become a structured course in the data root.
Intake is an agent task the toolkit launches directly, reviewed by a
human and checked by a deterministic command (docs/design.md, decision
14). It is not a Harbor job: the materials are trusted professor
content, and the work is one-time authoring with a review gate, not a
measured experiment.

The agent's instructions live in one place, the
`templates/prompts/intake.md` package template, rendered per course by
`aat intake` (and printable with `--print-prompt`). This document is
the maintainer's procedure around it.

**Scope: course materials only.** Ingesting student submissions (LMS exports
into `submissions/`, rosters and pseudonym tables under `tables/`) is the
separate deterministic `aat ingest-submissions` procedure described in
[data-conventions.md](data-conventions.md). Intake never touches those trees.

## Procedure

1. **Dump.** Create `raw/<course_id>/` in the data root and copy in
   everything collected for the course, syllabus, handouts, solution
   files, schedule pages, in whatever shape it arrived. Pick the
   course id once (`<institution>_<course>_<term>`, e.g.
   `PU_CHE456_F2025`); `raw/` is read-only from here on. Dump several
   courses at once if you have them.
2. **Run.** `aat intake --all` (or `--course ID`) launches the Codex
   CLI once per unprocessed dump, sequentially, in a workspace-write
   sandbox scoped to the data root, with pinned defaults
   (`gpt-5.6-sol`, high reasoning effort; override with `--model` /
   `--reasoning-effort`). Ten dumps means ten top-level runs. Within
   each run, the primary agent delegates one read-only audit per
   material-backed assignment, waits for every audit, writes the
   artifacts itself, and delegates a final coverage review. Output
   streams to the console and to
   `scratch/intake/<stamp>__<course>.log`; after each successful run the
   command writes the course's `intake-record.json` receipt and prints
   its `aat check-course` report. A failed run writes no receipt.
   Re-running `aat intake` is the retry. `--dry-run` lists what would
   run.
3. **Review and fill.** Fix violations, fill what the materials could
   not answer, and review every judgment call in `intake-notes.md`.
   Review rubric drafts most carefully because a rubric is the frozen judge
   of every grade produced under it. Every material-backed assignment
   gets a rubric, its point split taken from the highest-precedence
   source available and its source recorded per assessment
   (`rubric_provenance` in `course.toml`, the precedence order in
   docs/data-conventions.md). Two questions carry the review:

   - **Is this the split the course graded by?** A split transcribed
     faithfully from a handout is still wrong if the course applied a
     different one. Where a graded-copy export or LMS rubric is
     available, it settles the structure; where the handout and the
     applied scheme disagree, the applied scheme wins and the conflict
     goes in the notes.
   - **Does the rubric cover the whole assignment?** Criteria for some
     problems and none for others produce a score for a smaller
     assignment, reported as if it were the whole one. Where part of an
     assignment states no points, that part's split is authored like
     any other.

   Review authored splits strictest of all, since no source backs them.
   Re-run `aat check-course` until clean.
4. **Use.** Everything stays an editable draft until a job first
   records its hash; from that point the artifact is frozen
   (docs/data-conventions.md), except that a rubric found to be wrong
   is corrected in place once its superseded bytes are archived, which
   `aat grade` and `aat check-course` both enforce. Dropping
   later-arriving material into
   `raw/<course_id>/` changes the dump's hash, so the course counts as
   unprocessed again and the next `aat intake` performs an incremental
   pass. The intake prompt forbids the agent from modifying existing
   artifacts, so it only adds.

For a first course, or any course where you expect questions, run
the brief interactively instead: `aat intake --course ID
--print-prompt` renders it; paste it into a normal `codex` session in
the data root and answer the agent as it works. Write no receipt by
hand; either let a later `aat intake --course ID --force` run record
one, or leave the course as hand-built (the checker treats both the
same).

A course tree that exists without a receipt (built before `aat intake`
existed, or interactively) is skipped with a note; `--force` runs
intake over it, which per the incremental rule only adds material.
