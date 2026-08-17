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

Two agent stacks are implemented, **Codex and Claude Code**, each usable
as solver and as grader. Solver and grader are separate jobs, so either
agent can grade the other's work, and cross-agent comparison needs no
new machinery. Everything downstream of the agent — prompts, verifiers,
identities, results, statistics — is agent-agnostic. See the
[design](docs/design.md) for the decisions and contracts, and the
[roadmap](docs/roadmap.md) for planned work, including further agents.

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
and launches `harbor run`. The normal flow processes everything in a
handful of commands (plus one-time setup):

```bash
aat init-data                                # one-time: create the ~/aat-data layout
aat intake --all                             # build courses/ from dumps under raw/
aat check-course --course PU_CHE597DS_S2026  # per course: re-run until clean
aat ingest-submissions --all                 # build submissions/ from LMS exports
aat solve --all --config codex-high          # solve every assignment of every course
aat grade --from-solve codex-high --config codex-grader-sol-high  # grade the agent's solutions
aat grade --all --config codex-grader-sol-high   # grade every student submission
aat report                                   # tables + report.md under analysis/
```

The two `aat grade` commands cover disjoint submissions: `--from-solve`
grades the verified solver trials that `aat solve` left under
`solving/`, while `--all` grades the student folders under
`submissions/`. If one side is empty — no student submissions yet, say
— its command finds nothing and is simply unnecessary.

This sequence is safe to re-run verbatim: every command skips work
that is already done, so after a new dump lands in `raw/`, a new
assignment appears in a course, or a new LMS export lands in
`raw-submissions/`, the same commands do only the missing work. Narrow
any run with `--course ID` (and `--assignment ID`) instead of `--all`.

Student submissions enter the data root through ingest: drop the LMS
export zips (Brightspace download folders or Gradescope graded-copy
PDFs) into `raw-submissions/<course_id>/`, then
`aat ingest-submissions --all` normalizes them into pseudonymized
per-student folders under `submissions/` and identity tables under
`tables/` — deterministic code, no agent. Gradescope grade-summary
pages are split off so the grader never sees the professor's scores.
The command prints a review summary and exits nonzero while anything
needs attention (an unmatched zip name, an unresolvable identity);
fixes are one-line entries in `raw-submissions/<course_id>/manifest.toml`,
then re-run. The full contract is in
[data conventions](docs/data-conventions.md), "Submission ingest".

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

The committed initial-grader and final-judge configs form the same
model-and-reasoning matrix:

| Model and reasoning | Initial grader | Final judge |
| --- | --- | --- |
| Sol, high | `codex-grader-sol-high` | `codex-judge-sol-high` |
| Luna, high | `codex-grader-luna-high` | `codex-judge-luna-high` |
| Luna, max | `codex-grader-luna-max` | `codex-judge-luna-max` |
| Terra, high | `codex-grader-terra-high` | `codex-judge-terra-high` |
| Opus 5, high | `claude-grader-opus5-high` | `claude-judge-opus5-high` |
| Opus 5, max | `claude-grader-opus5-max` | `claude-judge-opus5-max` |
| Sonnet 5, high | `claude-grader-sonnet5-high` | `claude-judge-sonnet5-high` |

Config names describe rather than define: what results are keyed by is
the config identity. Grading configs read
`<agent>-grader-<model>-<effort>` and `<agent>-judge-<model>-<effort>`.
The solve configs do not share one pattern: `codex-high` names the agent
and the effort, because it was written when Codex was the only stack,
while `claude-opus5-high` and `claude-sonnet5-high` name the agent, the
model, and the effort. `codex-high` keeps its name because the name is
how stored results are selected afterwards — `aat grade --from-solve
codex-high`, `--context-from`, `aat report --config` — so renaming it
would leave every existing solve job unreachable by the name that
selects it.

Solver and grader are separate jobs, so any solver config can be graded
by any grader config — including across agents.

The judge model is independent of the initial grader model. Each judge
run names the one initial-grader config whose stored results it consumes
with `--context-from`, plus the exact number of stored gradings each
task presents with `--gradings`.

### Authentication for solving and grading

AAT resolves the configured agent's credential before creating a job,
preferring subscription-backed access over per-token API billing.

#### Codex

Live Codex runs automatically reuse the file-based login of the local
Codex CLI. In normal use, authenticate once with `codex login`, then run
`aat solve` or `aat grade` without exporting anything. AAT finds
`${CODEX_HOME:-~/.codex}/auth.json`, validates it before creating a job,
and tells Harbor to copy it into the Codex container. This works for both
ChatGPT subscription login and API-key login saved by Codex; OpenAI's
[Codex authentication documentation](https://learn.chatgpt.com/docs/auth)
describes those login and storage choices.

AAT resolves Codex authentication in this order:

1. `CODEX_AUTH_JSON_PATH` selects a specific auth file.
2. `CODEX_FORCE_AUTH_JSON=true` selects `~/.codex/auth.json`;
   `CODEX_FORCE_AUTH_JSON=false` explicitly selects `OPENAI_API_KEY`.
3. Otherwise, the cached file under `CODEX_HOME` (or `~/.codex`) is used.
4. If no cached file exists, a non-empty `OPENAI_API_KEY` is used.

The cached file deliberately wins when both it and `OPENAI_API_KEY` are
present, which prevents an unrelated shell API key from silently changing a
subscription-backed run to usage-based billing. To choose a separate auth
file for one run:

```bash
CODEX_AUTH_JSON_PATH=/protected/path/auth.json \
  aat grade --all --config codex-grader-terra-high
```

To explicitly choose usage-based API authentication even when a cached login
exists:

```bash
CODEX_FORCE_AUTH_JSON=0 OPENAI_API_KEY=... \
  aat grade --all --config codex-grader-terra-high
```

An absent, empty, unreadable, or invalid selected credential fails before AAT
creates job or task directories. If `codex login status` succeeds but no
`auth.json` exists because Codex uses the operating-system keyring, set
`cli_auth_credentials_store = "file"` in the Codex `config.toml` and run
`codex login` again. Treat `auth.json` like a password: Harbor temporarily
copies it into each Codex container. AAT records only the non-secret method and
selection source in `aat-run.json`, never the credential, token, or auth-file
path.

This preflight validates the selected local credential file or environment
variable, not the remote account. A provider can still reject a revoked login,
an expired key that cannot be refreshed, or an account without access.

`--dry-run` and `--materialize-only` remain offline and do not require
authentication. Running the command printed by `--materialize-only` manually
bypasses AAT's automatic injection; set `CODEX_AUTH_JSON_PATH` or
`OPENAI_API_KEY` in that shell first. `aat intake` invokes the host Codex CLI
directly, so it already uses the same local cached login without Harbor
injection.

#### Claude Code

Live Claude Code runs use the subscription token from `claude
setup-token`. Write it once to `~/.claude/aat-oauth-token` and every
later `aat solve` or `aat grade` finds it with nothing exported, the way
Codex runs find `~/.codex/auth.json`:

```bash
claude setup-token > ~/.claude/aat-oauth-token
chmod 600 ~/.claude/aat-oauth-token
```

Keep that file outside this repository and the data root; it is a
credential like `auth.json`. The host `claude` CLI is needed only to
mint the token — the task images carry their own pinned copy. Minting is
the one interactive step, the counterpart of `codex login`.

`~/.claude/.credentials.json`, the interactive login the `claude` CLI
keeps, is deliberately not read: its access token lasts hours and only
that CLI refreshes it, so a long run would lose its credential
mid-flight. `claude setup-token` exists to mint the long-lived token for
non-interactive use.

AAT resolves Claude authentication in this order:

1. `CLAUDE_FORCE_OAUTH` selects explicitly: `true`, `1`, or `yes` selects
   the subscription token, and `false`, `0`, or `no` selects
   `ANTHROPIC_API_KEY`.
2. Otherwise, `AAT_CLAUDE_TOKEN_FILE`, when set, names the token file to
   read; naming a file that is missing, empty, or holding more than the
   token is an error, not a fallthrough.
3. Otherwise, a non-empty `CLAUDE_CODE_OAUTH_TOKEN`.
4. Otherwise, `~/.claude/aat-oauth-token` when it exists.
5. If there is none, `ANTHROPIC_API_KEY` is used; once it is set at all
   it must be non-empty, and the error names it.

Harbor's Claude Code adapter reads environment variables only, so a
token file is read at launch and its contents travel to the Harbor
subprocess in `CLAUDE_CODE_OAUTH_TOKEN`. Neither the token nor the path
enters the run record, which keeps only the method
(`claude-oauth-token`) and the selection source.

`ANTHROPIC_AUTH_TOKEN` is not a credential source here. Harbor's adapter
delivers whatever it selects in `ANTHROPIC_API_KEY`, so a bearer token
would travel in the wrong header, and it is only meaningful against a
gateway `ANTHROPIC_BASE_URL` — which AAT removes (below).

The token deliberately wins when both it and `ANTHROPIC_API_KEY` are
present. This matters more than for Codex: Harbor's Claude Code adapter
prefers the API key over the token unless `CLAUDE_FORCE_OAUTH` is
truthy, so AAT sets that variable *and* removes both
`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from Harbor's
environment — an unrelated shell API key can never turn a
subscription-backed run into a usage-based bill. To choose usage-based
API authentication deliberately:

```bash
CLAUDE_FORCE_OAUTH=0 ANTHROPIC_API_KEY=... \
  aat grade --all --config claude-grader-opus5-high
```

Whichever credential is selected, AAT also removes the variables
Harbor's adapter would otherwise read from the shell: `ANTHROPIC_BASE_URL`
and `ANTHROPIC_MODEL` (which provider and model the run reaches),
`CLAUDE_CODE_USE_BEDROCK` and `AWS_BEARER_TOKEN_BEDROCK` (either one puts
the run on Bedrock, a third billing route this project does not use), and
the behavior fallbacks `CLAUDE_CODE_MAX_TURNS`,
`CLAUDE_CODE_EFFORT_LEVEL`, `MAX_THINKING_TOKENS`,
`CLAUDE_CODE_MAX_OUTPUT_TOKENS`, and
`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING`. None of them can enter a config
identity, so a value left in a shell would change every trial of a run
without leaving any trace of having done so. Agent settings that should
change a run belong in a config's `agent_args`, which is recorded and
part of the identity.

### Gurobi WLS for optimization tasks

Keep `gurobi.lic` outside this repository and the data root. Give `aat
solve` its host path either through `--gurobi-license-file PATH` or the
`AAT_GUROBI_LICENSE_FILE` environment variable:

```bash
export AAT_GUROBI_LICENSE_FILE=/home/sgusev/gurobi.lic

aat solve --course PU_CHE597CO_S2026 \
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
harbor view ~/aat-data/grading/20260801T044338Z__codex-grader-sol-high__44292e75
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
`pandas` back the statistics and reporting layer, and `pypdf` backs the
submission-ingest PDF splitting. Running actual jobs additionally
requires Docker, which packaging cannot provide; repository tests never
invoke it.

The task images carry their own pinned copy of the agent CLI, so no
agent CLI has to be installed on the host to run trials. The two stacks
differ in what the host is needed for: Claude Code needs the `claude` CLI
once, to mint the subscription token, and never again, while Codex needs
its CLI installed and logged in, because AAT reads that cached login at
every launch (and `aat intake` invokes the host Codex CLI directly).
