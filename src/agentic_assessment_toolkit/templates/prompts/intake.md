# Course intake: {course_id}

You are performing **course intake**: converting one course's raw
material dump into the structured course tree of an assessment data
root. Work only inside the data root you are started in. Read
`raw/{course_id}/`; create and populate `courses/{course_id}/`. Touch
nothing else — no other courses, no `submissions/`, no `tables/`, and
never anything inside `raw/`, which is read-only evidence. Ingesting
student submissions is out of scope entirely.

If `courses/{course_id}/` already exists, this is an incremental run:
**never modify an existing artifact** — an assignment directory, a
reference solution, a rubric file, or a rubric `source/` directory —
only add new ones (a newly found professor rubric document for an
assignment that already has a `source/` directory is recorded in the
notes, not added to it).
`course.toml`, `intake-notes.md`, and `syllabus/` are amendable: extend
the registry and the notes in place. If an existing artifact looks
wrong, record that in `intake-notes.md` instead of fixing it.

Produce this layout (authoritative spec: `docs/data-conventions.md` in
the agentic-assessment-toolkit repository):

```text
courses/{course_id}/
├── course.toml          # course record + assessment registry
├── intake-notes.md      # your judgment calls and open items
├── syllabus/            # syllabus file(s), copied verbatim
├── assignments/
│   ├── <assignment_id>/       # the handout, exactly as given
│   └── <assignment_id>.toml   # optional: environment override
├── reference_solutions/
│   └── <assignment_id>/       # instructor solution files
└── rubrics/
    └── <assignment_id>/
        ├── default.md         # the grading rubric you draft
        └── source/            # professor's standalone rubric files, verbatim
```

Do not create or edit `intake-record.json`: the toolkit writes that
receipt itself after you finish.

## Required workflow

Keep the primary agent focused on course-wide inventory, decisions,
writing, and validation. After mapping the material-backed assessments
to assignment and reference-solution sources, **spawn one read-only
subagent per assignment**. Run independent assignment audits in parallel
up to the available capacity, and wait for every audit before drafting
or omitting any rubric. Subagents must not edit files or create
directories; the primary agent is the only writer.

Give each assignment subagent only its bounded audit. It must inspect
both of these sources in full:

1. the student-facing assignment bundle, including every attachment;
2. the complete instructor/reference-solution bundle, including
   alternate or corrected statement copies and grading notes.

It must return a concise, structured report containing:

- the assignment id and every student and instructor source path it
  checked;
- every explicit point allocation, grading scheme, bonus rule,
  normalization rule, and qualitative grading instruction, cited to a
  file and notebook cell, section heading, page, or other precise
  location;
- conflicts between student-facing and instructor materials, including
  requirements added or changed by a corrected statement;
- every administrative requirement found in either bundle — names or
  identifiers on the work, signatures, honor affirmations, submission
  formalities, lateness rules, escalation instructions — cited to its
  location; these are excluded from the rubric under the rubric rules
  below, and the citation is what lets the exclusion be recorded;
- any standalone professor rubric files that belong in `source/`;
- a proposed `default.md` rubric, at the most granular explicit point
  split its source supports, with the source named (`applied_scheme`,
  `professor_rubric`, `handout`, or `authored` — the precedence order
  under **Rubrics** below). Only when no bundle contains a numeric
  point allocation is the rubric **authored** by the subagent under the
  authoring rules below, and the report must then also include the
  negative-search evidence and any qualitative grading guidance found.
  Where two sources state different splits, report both and which one
  wins.

Instructor/reference materials are valid sources of rubric points and
grading rules even when those rules do not appear in the student-facing
handout. Never conclude that an assignment lacks numeric points after
checking only the student-facing bundle. At the same time, a corrected
instructor statement does not silently replace the as-received student
handout: report any mismatch, use only criteria applicable to the
student-facing work, and flag anything that needs human judgment.

After all assignment reports return, make an audit table in
`intake-notes.md` with one row per material-backed assessment and these
columns: assignment id, student sources checked, instructor sources
checked, point evidence, rubric source (`applied_scheme`,
`professor_rubric`, `handout`, or `authored`), assignment coverage
(which problems have criteria), and conflicts. Use the reports to write the course artifacts. Every
assignment must end with a `default.md` rubric; for each authored one,
the notes must also carry the audit's negative-search evidence — that
record is what tells the reviewer the point split is your judgment,
not the professor's.

Then spawn one final read-only reviewer subagent. Give it the assignment
inventory, completed audit table, generated rubrics, and raw source
locations. It must check that every assignment is accounted for, reopen
both source bundles for every authored rubric to confirm no point
evidence was missed, verify that cited point
schemes were not lost during synthesis, and report rubric-total,
bonus-status, normalization, or handout/reference conflicts. Wait for
the review and resolve every finding that the source materials answer;
put genuinely unresolved judgments in `intake-notes.md`. On an
incremental run, obey the immutability rule above: record a finding
against an existing artifact instead of modifying that artifact.

If subagent tools are unavailable, do not silently skip this workflow.
Perform the same bounded audits sequentially, keep each report concise,
record the lack of subagent availability in `intake-notes.md`, and still
complete the audit table and final review.

## Rules

**Assignments.** One assignment = one directory, named by its id.
Copy handout files byte-for-byte — never edit, convert, rename, or
split a handout file — and include every data file, template, or
attachment that belongs to the assignment. Matching scattered files to
the right assignment is your judgment: record every non-obvious
association in the notes. Problem structure *within* an assignment is
expressed in the rubric, never in the file layout.

**Ids.** Uppercase, no whitespace, zero-padded numbers so listings
sort: `HW01`, `PSO03`, `EXAM1`, `MIDTERM`, `FINAL`, `PROJECT`. The
same id names the assignment, reference-solution, and rubric
directories and the registry entry. Keep the original human label
(topic names, "HW 4") in the registry's `title`, not in directory
names.

**Syllabus.** Copy the syllabus and any grading-policy or schedule
documents verbatim into `syllabus/`.

**`course.toml`.** A `[course]` table holding `title`, `institution`,
`term`, and `environment`, the default environment flavor
(`data-science`, `optimization`, or `scientific-python`; judge from
the packages the course actually uses, note the choice). The flavor
set is fixed: never invent a flavor name or write a Dockerfile. When
even the closest flavor lacks packages the course clearly needs, pick
it anyway and record the missing packages in the notes as an
environment gap — updating the environment templates is the
maintainer's job, not yours. After the `[course]` table, one
`[[assessments]]` entry per assessment the syllabus grades, including
exams, presentations, and attendance that have no materials, so
weights sum to 100. Per entry: `id`; `title`; `type` (`homework |
exam | practice | project | attendance | other`); `scope` (`take_home
| online_exam | in_person_exam | presentation | in_person`);
`weight_pct` (percent of the final grade; when the syllabus states a
category rule like "homework 40%, split equally", do the arithmetic
and record the rule in the notes); `category`; `ai_policy` (`allowed |
not_allowed | not_applicable` — what the course *permits*);
`ai_use_possible` (boolean — whether AI use was physically *feasible*,
independent of permission); `due` (plain TOML date);
`rubric_provenance` (`applied_scheme | professor_rubric | handout |
authored` — which source the assessment's `default.md` took its point
split from; set it exactly when you draft the rubric, matching the
audit table's rubric source, and leave it absent for assessments
without a rubric); and `excluded` —
a short reason, only for assessments that have no assignment directory
and never will (not codeable, materials missing from the dump).

**A fact the materials do not state is left absent.** Never write a
guess, a placeholder, or a sentinel into a typed field; list what you
looked for and could not find in the notes instead. The one deliberate
exception is an authored rubric: that is not a guessed fact but your
own grading design, produced under the authoring rules below and
labeled as authored in the notes.

**Reference solutions.** Instructor/oracle solution files go under
`reference_solutions/<assignment_id>/`, verbatim. Never place them in
the assignment directory: the solver reads only `assignments/`, and
that separation is what keeps answers away from it.

A file that merely duplicates the assignment statement is not a
reference solution; leave it out. When the instructor bundle contains
no worked solution for a material-backed assignment, write
`reference_solutions/<assignment_id>/README.md` stating that no worked
reference solution exists and that the grader must establish
correctness from the rubric, the submission's own derivations, and
internal consistency checks — and record the gap in the notes. Never
author a worked solution yourself: an invented oracle is worse than an
absent one.

**Rubrics.** When the professor distributed a standalone rubric
document (a rubric PDF, a grading-scheme handout — as opposed to
points embedded in the assignment or reference files), copy it
verbatim into `rubrics/<assignment_id>/source/`; the grader is shown
it alongside your transcription.

Draft `rubrics/<assignment_id>/default.md` for every material-backed
assignment. Take its point split from the highest-precedence source
the materials offer, and record which one in the registry's
`rubric_provenance` and in the audit table:

1. `applied_scheme` — the scheme the course actually graded by, when
   the dump contains one: an LMS rubric export, a per-question score
   export, or graded-copy summaries. This is the assignment's real
   point structure, because it produced the grades of record.
2. `professor_rubric` — a standalone rubric document, copied verbatim
   into `source/` as above.
3. `handout` — point values printed in the assignment or the reference
   solution: grading schemes in notebook cells, points in section
   headers.
4. `authored` — your own split, under the authoring rules below, when
   no source states one.

When two sources disagree, the higher one wins and the conflict goes
in the notes — a handout that says one thing while the course graded
another is not a judgment call. Only the *structure* transfers from a
source: criterion ids, maxima, bonus flags, and any score levels the
scheme states. What earns each level you draft from the assignment and
reference solution; how individual graders applied a scheme (leniency
on particular submissions, one-off adjustments, administrative
deductions an LMS rubric carries) is never evidence.

The rubric must cover the **whole assignment**. Criteria for some
problems and none for others do not grade the assignment leniently —
they grade a smaller assignment while the score is reported as the
whole one. Where part of an assignment states no points, author that
part's split like any other and say so in the notes.

Either way the rubric is a **detailed grading
document**, not a bare list: ordinary Markdown prose plus one bullet
line per criterion in exactly this format:

```markdown
- `<id>` (<points> point[s][, bonus]): <title>
```

for example:

```markdown
- `slope` (8 points): the reported slope equals 2.
- `plot` (1 point, bonus): a plot of the fit is included.
```

Criterion ids are lowercase, unique, stable, and contain no
whitespace; points are numbers greater than zero; bonus criteria carry
the explicit `, bonus` marking; at least one criterion is not a bonus.
Granularity when transcribing: one criterion per problem or per
explicitly-pointed item, exactly as the source states it — never
invent a finer split than the source supports.

Follow each criterion line with indented prose stating how to grade
it: what earns full credit, what earns partial credit and how much,
what earns zero — carried over from the professor's materials when
they say, drafted by you from the assignment and reference solution
when they do not. For example:

```markdown
- `tree_fit` (20 points): a decision tree is trained on the training split.

  Full credit requires max_depth chosen by validation, not hardcoded.
  Award half if the tree is trained on the full dataset; zero if the
  model is imported but never fit.
```

**Administrative requirements are never rubric content.** The rubric
grades academic work only. Requirements about identity or course
administration — the student's name or identifier on the work,
signatures, honor affirmations or integrity statements, submission
formalities such as boxing final answers, lateness penalties,
escalation to the instructor — become neither criteria nor rubric
prose, even when the professor's materials assign them points or
withhold grading over them (for example "an unnamed page is not
graded"), and whatever their source — an applied grading scheme that
carries a "late submission" deduction or a discretionary "point
adjustment" is no different. The
professor's statement stays available verbatim in the handout and in
`source/`; record each exclusion, with its source citation, in the
audit table and `intake-notes.md`. Requirements about the academic
work itself — shown work, stated assumptions, required derivations —
are not administrative and stay in the rubric.

Transcribe faithfully; where the source is vague (section totals only,
unclear bonus status), still draft the best faithful rubric and flag
the ambiguity in the notes.

Never write a scaling or normalization instruction into a rubric
("multiply the subtotal by 100/90", "divide by 15 for the gradebook").
The rubric states raw points; every percentage is derived in code from
the criterion maxima. A raw total other than 100 is fine on its own —
it is a scale, not an error.

**Authoring rules.** Where no point information exists in either the
student-facing or instructor/reference bundle, author the rubric from
the assignment and reference solution. Authoring invents structure,
never policy:

- The criteria total exactly 100 points, integer points per criterion.
  The split need not be equal: weight criteria by the assignment's
  apparent effort and emphasis, and record the rationale in the notes.
- No bonus criteria. If the handout mentions optional or extra-credit
  work without assigning it points, do not turn it into a bonus
  criterion; flag it in the notes instead.
- One criterion per unit the handout itself delineates — numbered
  problems, lettered parts, required deliverables. Go finer only when
  the reference solution shows separately checkable deliverables
  within one unit, and record why in the notes. Every criterion must
  be gradable from the submission alone: if the grader cannot score
  it without scoring its neighbor, merge them. Avoid both failure
  modes: a single criterion covering a multi-part assignment, and
  micro-criteria the handout never had.
- Record that the rubric is authored in the registry
  (`rubric_provenance = "authored"`) and — with the negative-search
  evidence — in the audit table and the notes, never in the
  rubric file itself: the grader reads the rubric as the authority,
  and a provenance note there invites it to second-guess the
  criteria.

**`intake-notes.md`.** Your report to the maintainer, and the only
place for uncertainty: the sources each registry fact came from, every
judgment call (id assignment, file association, weight arithmetic,
environment choice and any environment gap, rubric transcription and
its confidence),
everything you looked for and could not find, and open questions.
Finish by listing what a human still has to provide or verify.
