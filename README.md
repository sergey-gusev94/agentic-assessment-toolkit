# Agentic Assessment Toolkit

A Python toolkit built around one assignment-and-grading pipeline in which a
"submission" can come either from an autonomous coding agent or from a real
student. It serves two workflows:

1. **Benchmarking** coding agents (Codex CLI, Claude Code, Gemini CLI, and
   others, running on existing user subscriptions) on real chemical
   engineering coursework.
2. **Grading assistance** for professors and teaching assistants: an
   independent grade with per-criterion explanations, produced by the same
   machinery, as an information tool.

The project is in its initial development stage.

The implementation is **Codex-first**: the solving, grading, validation,
and reporting pipeline is built for Codex; other agent stacks and
cross-agent comparisons follow once the Codex pipeline is hardened. See
the [design](docs/design.md) for the authoritative sequencing decision
and the [roadmap](docs/roadmap.md) for planned work.

## Documentation

- [Project brief](docs/brief.md) — vision, use cases, scope, and constraints.
- [Design](docs/design.md) — decisions, contracts, and how the
  implemented pipelines work.
- [Course intake](docs/course-intake.md) — how raw course materials
  become a structured course in the data root.
- [Roadmap](docs/roadmap.md) — planned work, each item with the
  condition that triggers it.
- [Research](docs/research.md) — frozen research snapshot: alternatives
  considered, methodology, security model.
- [Data conventions](docs/data-conventions.md) — strict code–data separation:
  this repository is always safe to publish; all real course and student data
  lives in an external data root and is never committed.

## Usage

The `aat` command materializes Harbor tasks from a data root (external
to this repository, see [data conventions](docs/data-conventions.md))
and launches `harbor run`. The normal flow processes everything in
five commands (plus one-time setup):

```bash
aat init-data                                # one-time: create the ~/aat-data layout
aat intake --all                             # build courses/ from dumps under raw/
aat check-course --course PU_CHE597DS_S2026  # per course: re-run until clean
aat solve --all --config codex-high          # solve every assignment of every course
aat grade --from-solve codex-high --config codex-grader-high  # grade the agent's solutions
aat grade --all --config codex-grader-high   # grade every student submission
aat report                                   # tables + report.md under analysis/
```

The two `aat grade` commands cover disjoint submissions: `--from-solve`
grades the verified solver trials that `aat solve` left under
`solving/`, while `--all` grades the student folders under
`submissions/`. If one side is empty — no student submissions yet, say
— its command finds nothing and is simply unnecessary.

This sequence is safe to re-run verbatim: every command skips work
that is already done, so after a new dump lands in `raw/`, a new
assignment appears in a course, or new student folders arrive in
`submissions/`, the same commands do only the missing work. Narrow any
run with `--course ID` (and `--assignment ID`) instead of `--all`.

A course enters the data root through intake: copy everything
collected for it into `raw/<course_id>/`, then `aat intake --all` (or
`--course ID`) launches the Codex CLI once per unprocessed dump to
author the structured course tree under `courses/`, including a rubric
draft per assignment. Review the drafts and the agent's
`intake-notes.md`, fix what `aat check-course` flags, and re-run it
until clean. Intake needs the `codex` CLI on PATH; alternatively,
`aat intake --course ID --print-prompt` renders the brief to paste
into an interactive `codex` session. The full procedure, including the
review checklist, is in [course intake](docs/course-intake.md).

Grading never starts without a rubric: `aat grade` refuses any
assignment missing
`courses/<course_id>/rubrics/<assignment_id>/default.md`, which intake
drafts and you review.

The data root defaults to `~/aat-data`. Resolution never creates it;
`aat init-data` creates the directory and its top-level layout (add
`--git` to also make it a private git repository with a `.gitignore`
for regenerable outputs). Use `--data-root PATH` for a one-off override
or set `AAT_DATA_DIR` to change the default for an environment or
shell.

Experiment configs live under [`configs/`](configs/); a bare
`--config NAME` resolves to `configs/NAME.toml` relative to the current
working directory, so run from the repository root or pass an explicit
path. Selection and mechanics (`--repeats`, `--max-concurrent-trials`,
`--force`, `--dry-run`, `--materialize-only`) are CLI flags. Concurrent
trials default to 8. Every run command requires an explicit selection —
`--course ID` or `--all` for `aat solve` and `aat intake`, and exactly
one submission source for `aat grade` (`--from-solve NAME`,
`--submissions PATH` for one course, student, or assignment folder
under `submissions/`, or `--course`/`--all` for student folders); only
the read-only `aat report` defaults to everything. Already-done items
are skipped by default, so bulk
commands are naturally incremental. `aat report` is read-only: it
writes the statistics tables, a Markdown report, and provenance into
one timestamped directory under `analysis/` in the data root (`--out`
moves the destination, which is never allowed inside this repository).
See the [design](docs/design.md) for the full CLI contract.

### Gurobi WLS for optimization tasks

Keep `gurobi.lic` outside this repository and the data root. Give `aat
solve` its host path either through `--gurobi-license-file PATH` or the
`AAT_GUROBI_LICENSE_FILE` environment variable:

```bash
export AAT_GUROBI_LICENSE_FILE=/home/sgusev/gurobi.lic

aat solve --course PU_CHE597CO_S2026 --assignment HW04 \
  --config codex-high --max-concurrent-trials 1
```

The toolkit verifies that the path is a file and that every selected
assignment uses the `optimization` environment. The generated Harbor
job mounts the file read-only at `/opt/gurobi/gurobi.lic`; neither the
credential bytes nor their individual WLS values enter a task, image,
or recorded JSON file. The host path is recorded in `harbor-job.json`.
Omit the option and environment variable to run the optimization image
without Gurobi licensing and use its license-free solvers.

Before a course run, build the shipped image and solve a one-variable
model in it. This is live validation: it needs Docker, internet access
to Gurobi WLS, and an active license.

```bash
docker build \
  --file src/agentic_assessment_toolkit/templates/environments/optimization.Dockerfile \
  --tag aat-optimization-license-check .

docker run --rm -i \
  --mount "type=bind,source=$AAT_GUROBI_LICENSE_FILE,target=/opt/gurobi/gurobi.lic,readonly" \
  aat-optimization-license-check python - <<'PY'
import gurobipy as gp

model = gp.Model("license-check")
x = model.addVar(lb=0, name="x")
model.addConstr(x <= 1)
model.setObjective(x, gp.GRB.MAXIMIZE)
model.optimize()
assert model.Status == gp.GRB.OPTIMAL
assert abs(model.ObjVal - 1) < 1e-9
print("Gurobi WLS license check passed")
PY
```

Start an Academic WLS course run with one concurrent trial unless the
license portal shows capacity for more. Read-only mounting prevents the
container from changing the file; code inside the container can still
read it, so use a dedicated, renewable credential.

To repeat a completed item, add `--force`. Repeats are additional trials;
they never replace prior results:

```bash
# Add one new solver trial.
aat solve --course PU_CHE597DS_S2026 --assignment HW5 \
  --config codex-high --force

# Add three new solver trials in one job.
aat solve --course PU_CHE597DS_S2026 --assignment HW5 \
  --config codex-high --force --repeats 3
```

A subsequent `aat grade --from-solve codex-high ...` automatically selects
new solver trials that have not yet been graded. To run another independent
grader trial over an already-graded submission, use `--force` on `aat grade`
(and optionally `--repeats N`).

Inspect a single run with the exact per-job command printed by `aat`, or
browse a whole stage — job directories are Harbor job directories, so the
viewer works on the shared `solving/` and `grading/` parents too:

```bash
harbor view ~/aat-data/grading/20260801T044338Z__codex-grader-high__44292e75
harbor view ~/aat-data/solving
```

Do not use Harbor's suggested `upload` command for real coursework unless the
assignment, reference solution, submissions, and transcripts are authorized
for disclosure or have been sanitized.

## Development

Requires Python 3.12+. Install and validate with:

```bash
pip install -e ".[dev]"
make check
```

Harbor is the package's runtime-orchestration dependency; `numpy` and
`pandas` back the statistics and reporting layer. Running actual jobs
additionally requires Docker and the agent CLIs (Codex CLI
first), which are external tools packaging cannot provide; repository
tests never invoke any of them.
