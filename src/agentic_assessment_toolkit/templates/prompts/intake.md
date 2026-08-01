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
reference solution, or a rubric file — only add new ones.
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
    └── <assignment_id>/default.md
```

Do not create or edit `intake-record.json`: the toolkit writes that
receipt itself after you finish.

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
independent of permission); `due` (plain TOML date); and `excluded` —
a short reason, only for assessments that have no assignment directory
and never will (not codeable, materials missing from the dump).

**A fact the materials do not state is left absent.** Never write a
guess, a placeholder, or a sentinel into a typed field; list what you
looked for and could not find in the notes instead.

**Reference solutions.** Instructor/oracle solution files go under
`reference_solutions/<assignment_id>/`, verbatim. Never place them in
the assignment directory: the solver reads only `assignments/`, and
that separation is what keeps answers away from it.

**Rubrics.** For each assignment whose materials state a point split —
grading schemes in notebook cells, points in section headers, rubric
documents — draft `rubrics/<assignment_id>/default.md`. Ordinary
Markdown prose plus one bullet line per criterion in exactly this
format:

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
Granularity: one criterion per problem or per explicitly-pointed item,
exactly as the source states it — never invent a finer split than the
source supports. Surrounding prose may carry grading guidance from the
materials. Transcribe faithfully; where the source is vague (section
totals only, unclear bonus status), still draft the best faithful
rubric and flag the ambiguity in the notes. Where no point information
exists at all, draft nothing and note it.

**`intake-notes.md`.** Your report to the maintainer, and the only
place for uncertainty: the sources each registry fact came from, every
judgment call (id assignment, file association, weight arithmetic,
environment choice, rubric transcription and its confidence),
everything you looked for and could not find, and open questions.
Finish by listing what a human still has to provide or verify.
