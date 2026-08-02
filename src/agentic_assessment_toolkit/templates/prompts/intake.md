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
- any standalone professor rubric files that belong in `source/`;
- a proposed `default.md` rubric. When either bundle states a numeric
  point allocation, the rubric is **transcribed** at the most granular
  explicit point split the sources support. Only when **both** bundles
  contain no numeric point allocation, the rubric is **authored** by
  the subagent under the authoring rules below, and the report must
  also include the negative-search evidence and any qualitative
  grading guidance found.

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
checked, point evidence, rubric action (`transcribed` or `authored`),
and conflicts. Use the reports to write the course artifacts. Every
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

**`course.toml`.** A `[course]` table — `title`, `institution`,
`term`, and `environment`, the default environment flavor
(`data-science`, `optimization`, or `scientific-python`; judge from
the packages the course actually uses, note the choice) — and one
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
`rubric_provenance` (`transcribed | authored` — how the assessment's
`default.md` got its point split; set it exactly when you draft the
rubric, matching the audit table's rubric action, and leave it absent
for assessments without a rubric); and `excluded` —
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

**Rubrics.** When the professor distributed a standalone rubric
document (a rubric PDF, a grading-scheme handout — as opposed to
points embedded in the assignment or reference files), copy it
verbatim into `rubrics/<assignment_id>/source/`; the grader is shown
it alongside your transcription.

Draft `rubrics/<assignment_id>/default.md` for every material-backed
assignment. When the student-facing **or instructor/reference**
materials state a point split — grading schemes in notebook cells,
points in section headers, rubric documents — **transcribe** it. When
neither bundle states one, **author** the rubric yourself under the
authoring rules below. Either way the rubric is a **detailed grading
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

Transcribe faithfully; where the source is vague (section totals only,
unclear bonus status), still draft the best faithful rubric and flag
the ambiguity in the notes.

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
environment choice, rubric transcription and its confidence),
everything you looked for and could not find, and open questions.
Finish by listing what a human still has to provide or verify.
