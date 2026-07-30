# Open-Source Agentic Assessment and LLM Benchmarking Research

Research snapshot: 2026-07-30

## Executive summary

The recommended foundation for Agentic Assessment Toolkit is
[Harbor](https://github.com/harbor-framework/harbor) together with its bundled
[RewardKit](https://www.harborframework.com/docs/rewardkit).

Harbor is the closest existing system to the intended workflow:

```text
assignment directory
        ↓
Harbor task and isolated environment
        ↓
Codex, Claude Code, or another agent solves it
        ↓
immutable artifacts, raw transcript, and ATIF trajectory
        ↓
separate hidden verifier
        ↓
deterministic tests and rubric/agent judge
        ↓
structured scores and comparison viewer
        ↓
later regrade without rerunning the solver
```

Agentic Assessment Toolkit should therefore be a thin domain-specific layer
around Harbor rather than a new agent runner, sandbox framework, transcript
format, experiment database, or results viewer. The toolkit should own
assignment import conventions, reusable scientific environments, rubrics,
validation, benchmark configuration, and statistical reporting.

The best alternatives are:

1. [Promptfoo](https://github.com/promptfoo/promptfoo) plus a small
   workspace-copy and artifact-archive wrapper, when minimizing initial code is
   more important than strong isolation.
2. [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) plus
   [Inspect SWE](https://github.com/meridianlabs-ai/inspect_swe) and a
   native-subscription agent adapter, when evaluation statistics and research
   controls justify more integration work.

Neither the initial prompt nor this repository contains a URL for the referenced
project, so this research evaluates tools against the described workflow rather
than performing a direct feature-by-feature comparison with that repository.

## Requirements used for comparison

The candidate systems were evaluated against the following requirements:

- Each assignment may be a directory containing its problem statement,
  documents, starter code, notebooks, data, simulators, and other assignment
  assets.
- A complete autonomous agent must solve the assignment inside an isolated
  workspace.
- Codex CLI and Claude Code must be usable through existing user subscriptions,
  without requiring ordinary per-token API billing.
- Solver prompts must be experiment configuration, not assignment content, so
  the same immutable assignment can be tested with different agents, models,
  prompts, reasoning efforts, agent versions, and repeated attempts.
- Agent outputs, modified files, raw transcripts, structured trajectories,
  errors, timings, and usage information must be retained.
- Grading must support both deterministic tests and model- or agent-based rubric
  judgments with partial credit and multiple score dimensions.
- Solving and grading must be separable in time so that recorded solutions can
  be regraded without rerunning the solver.
- Results must be reproducible enough for research use and exportable for
  independent analysis.
- The same grading machinery should be reusable later for real student
  submissions, without making course-management features part of the benchmark
  core.
- The preferred solution should use one package, or a small combination of
  packages, and minimize project-specific code.

## Primary recommendation: Harbor and RewardKit

### Architectural fit

The toolkit should model three distinct layers:

```text
assignment
├── problem statement
├── starter files
├── documents and data
├── notebooks or simulators
└── environment requirements

experiment configuration
├── solver prompt template
├── agent, model, and reasoning effort
├── tools and permissions
├── network policy
└── resource and time limits

grading configuration
├── rubric
├── deterministic tests
├── reference solution or expected evidence
└── grader prompt, model, and policy
```

An assignment is the immutable problem being evaluated. A solver prompt is an
independent experimental variable applied to that problem. A grading
configuration is versioned separately so that saved solutions can be regraded.
Keeping these identities separate makes paired comparisons possible and avoids
duplicating an assignment for every prompting strategy.

Harbor's materialized task format has almost exactly the required execution
shape:

```text
harbor-task/
├── instruction.md
├── task.toml
├── environment/
│   └── Dockerfile
├── solution/
│   └── solve.sh
└── tests/
    ├── test.sh
    ├── deterministic.py
    └── rubric.toml
```

`instruction.md` is the text ultimately presented to the agent; it should not
be treated as the canonical assignment or as a prompt stored inside the
assignment. The toolkit should generate it for each task configuration from:

1. The canonical assignment statement.
2. The selected solver prompt template.
3. Any standardized output contract.

The generated Harbor task and resolved job configuration should record the
assignment and prompt hashes independently.

The optional `solution/` directory contains an instructor or oracle solution
that can be used to validate that the task and verifier are correct. The native
task format is documented in Harbor's
[task documentation](https://www.harborframework.com/docs/tasks).

Harbor provides:

- Local task directories and versioned datasets.
- Local Docker and Docker Compose execution.
- Cloud sandbox backends including Daytona, Modal, E2B, Runloop, and others.
- CPU, memory, storage, GPU, timeout, operating-system user, and network
  policies.
- Separate solver and verifier containers.
- Multiple agents, models, attempts, and concurrent trials.
- Native Codex CLI, Claude Code, Gemini CLI, OpenHands, Mini-SWE-Agent, Aider,
  OpenCode, and other agent integrations.
- Custom agents through an importable Python interface.
- Raw agent streams and session data.
- Normalized
  [Agent Trajectory Interchange Format](https://www.harborframework.com/docs/agents/trajectory-format)
  trajectories.
- Arbitrary artifact collection with manifests.
- Scalar or multidimensional numerical rewards.
- Resolved job configuration and provenance locks.
- A local viewer for jobs, trials, trajectories, tool calls, artifacts,
  verifier details, errors, token usage, timings, and side-by-side comparisons.

Harbor's conceptual hierarchy maps cleanly to this project:

| Harbor concept | Project meaning |
|---|---|
| Task | One assignment and its execution/grading environment |
| Dataset | A course, unit, domain, or benchmark suite |
| Agent | Codex CLI, Claude Code, Gemini CLI, or another complete scaffold |
| Trial | One agent attempt on one assignment |
| Job | A collection of trials across assignments and configurations |
| Artifact | The final submission, report, code, notebook, plots, or other evidence |
| Verifier | Deterministic checks and rubric-based grading |

See Harbor's
[core concepts](https://www.harborframework.com/docs/core-concepts) and
[evaluation workflow](https://www.harborframework.com/docs/run-jobs/run-evals).

### Delayed grading and regrading

Harbor has a first-class operation matching the requested two-stage workflow:

```bash
harbor job regrade <solver-job> -p <tasks-with-new-rubrics>
```

Regrading holds the recorded agent execution fixed, reconstructs the declared
artifacts in a new verifier environment, and runs only the new verifier. It does
not modify the source job and records provenance back to the source trial. This
is documented in Harbor's
[regrade guide](https://www.harborframework.com/docs/run-jobs/regrade).

Important constraints:

- Regrading was added after the v0.20.0 stable release. The project should pin
  the exact post-release commit containing regrading or wait for the next stable
  release.
- The new verifier must use a separate verifier environment.
- All files required for later grading must have been declared and collected as
  artifacts during the original solver run.
- Current regrading supports completed single-step trials; multi-step regrading
  is not yet supported.

### RewardKit

RewardKit is an independent package distributed in the Harbor repository. It
provides:

- Deterministic file, command, text, regex, JSON, CSV, spreadsheet, database,
  HTTP, image, and trajectory checks.
- Custom Python criteria.
- Binary, Likert, and bounded numeric rubric items.
- Criterion weights, required conditions, optional conditions, thresholds, and
  partial credit.
- Weighted-mean, all-pass, any-pass, threshold, and required-pass aggregation.
- Multiple score dimensions such as correctness, methodology, units,
  presentation, and interpretation.
- LLM judges through model providers.
- Agent judges through Claude Code or Codex that may inspect files and run
  commands.
- Reference answers and configurable judge prompts.
- Per-criterion explanations, errors, and audit details.
- Optional PDF, DOCX, PPTX, XLSX, and image support.
- Isolated execution for criteria that might modify a workspace.

Relevant documentation:

- [RewardKit overview](https://www.harborframework.com/docs/rewardkit)
- [Judge criteria](https://www.harborframework.com/docs/rewardkit/judge-criteria)
- [Built-in criteria](https://www.harborframework.com/docs/rewardkit/built-in-criteria)

For scientific assignments, an effective verifier should combine:

1. Deterministic numerical checks for balances, equations, units, tolerances,
   spreadsheet cells, required files, and executable results.
2. Rubric judgments for assumptions, method selection, reasoning quality,
   engineering interpretation, plots, and discussion.
3. Separate component rewards so that a total grade does not erase diagnostic
   information.

### Fast prototype with `harbor exec`

Recent Harbor versions include an experimental `harbor exec` command that can
turn loose directories into temporary tasks, copy inputs into a work directory,
run agents, and preserve selected artifacts.

This is the lowest-code path for the first proof of concept:

```bash
harbor exec \
  -p assignments \
  --scan \
  -i "Solve this assignment and place the final work in /app/submission" \
  -f /app/submission \
  -a codex \
  -k 1
```

It should not become the permanent benchmark schema because:

- The command is explicitly experimental.
- Its autogenerated verification is intentionally shallow.
- Durable rubrics, hidden tests, oracle solutions, and controlled environments
  are clearer in native Harbor task directories.

Use it to validate authentication, container execution, and artifact collection,
then migrate representative assignments to ordinary Harbor tasks.

## Subscription-backed execution

### Codex solving

Harbor already supports a user's existing Codex CLI login. Its
[Codex adapter](https://github.com/harbor-framework/harbor/blob/main/src/harbor/agents/installed/codex.py)
accepts:

```bash
export CODEX_FORCE_AUTH_JSON=1
```

to use `~/.codex/auth.json`, or:

```bash
export CODEX_AUTH_JSON_PATH=/protected/path/auth.json
```

to select an explicit authentication file. Harbor uploads that file into a
temporary directory in the agent container, invokes `codex exec --json`,
captures its sessions and trajectory, and performs best-effort credential
cleanup.

OpenAI documents Codex as included with eligible ChatGPT plans and supports
signing the CLI in with ChatGPT:
[Using Codex with a ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-chatgpt).

No Harbor source modification is required for subscription-backed Codex solving.

### Claude Code solving

Harbor's
[Claude Code adapter](https://github.com/harbor-framework/harbor/blob/main/src/harbor/agents/installed/claude_code.py)
supports a Claude subscription token:

```bash
claude setup-token

export CLAUDE_CODE_OAUTH_TOKEN=...
export CLAUDE_FORCE_OAUTH=1
```

Harbor then removes any competing API-key authentication and invokes the
official Claude CLI noninteractively with streaming JSON output.

Official references:

- [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage)
- [Using Claude Code with Pro or Max](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)

Anthropic announced that Agent SDK and `claude -p` automation would move from
ordinary subscription limits to a separate monthly credit on June 15, 2026,
but paused that change before it took effect. As of the June 15 update, Agent
SDK, `claude -p`, and third-party app usage still draw from the user's existing
subscription limits, and the proposed separate monthly credit is not available.
The superseded proposal remains on the same page below the update, so its
details should not be mistaken for current policy:
[Claude Agent SDK subscription policy](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan).

Because Anthropic says it is still working on an updated plan, recheck this
policy before a benchmark run and record the policy date with the results.

No Harbor source modification is required for subscription-backed Claude
solving.

### Subscription-backed grading

When grading runs locally under the same operating-system account and does not
need an isolated verifier, Codex can be the only model-based grader. Run
`codex login` once with the ChatGPT account. Subsequent `codex exec` calls reuse
the cached login and draw from eligible ChatGPT subscription usage rather than
requiring an API key.

A minimal grading invocation is:

```bash
codex exec \
  --cd /path/to/grading-bundle \
  --sandbox read-only \
  --output-schema /path/to/grade.schema.json \
  --output-last-message /path/to/grade.json \
  "Grade the submission using the supplied rubric and return the requested JSON."
```

The grading bundle can expose the solution, rubric, reference material, and
tests under distinct paths. Use `--json` and retain the JSONL event stream when
the complete grader trajectory is required.

RewardKit can also use this cached local login. Its Codex agent-judge
implementation performs an explicit login only when `CODEX_ACCESS_TOKEN` or
`OPENAI_API_KEY` is present. When neither variable is set, it launches
`codex exec` without logging in, so Codex inherits the current user's cached
ChatGPT authentication.

The authentication complication applies to a fresh container, remote worker, or
isolated verifier where the user's Codex credential cache is absent.
RewardKit's documented container-friendly path uses `CODEX_ACCESS_TOKEN`, which
is created by eligible Business or Enterprise workspace administrators. A
personal subscription user normally has a local `auth.json` instead, so an
isolated verifier would need that file injected or a small adapter change.

For the no-isolation grading workflow, neither is necessary. Run Codex or
standalone RewardKit on the host as the already authenticated user. Ensure
`OPENAI_API_KEY` is unset when using RewardKit this way; if it is present,
RewardKit explicitly logs Codex in with that key and the run uses API billing.

Relevant sources:

- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [RewardKit Codex agent implementation](https://github.com/harbor-framework/harbor/blob/main/packages/rewardkit/src/rewardkit/agents.py)
- [RewardKit subscription authentication](https://www.harborframework.com/docs/rewardkit#subscription-auth)

### Gemini and other subscription CLIs

The official [Gemini CLI](https://github.com/google-gemini/gemini-cli) is
Apache-2.0 and supports cached Google-account authentication, noninteractive
execution, JSON or streaming JSON, model selection, and several sandbox modes.
It is a useful third agent-stack baseline.

Third-party tools should not extract and reuse Gemini or Claude consumer OAuth
credentials through unofficial clients. Invoke the official CLI or sanctioned
Agent SDK and keep vendor terms and institutional policies in scope.

## What is actually being benchmarked

Subscription-backed comparisons measure complete agent stacks, not bare
language models.

The experimental object is:

```text
agent scaffold
+ model backend and product snapshot
+ agent and CLI version
+ system instructions
+ prompt template
+ reasoning effort
+ tools, skills, and MCP servers
+ network policy
+ resource, turn, token, time, and cost limits
```

Codex CLI and Claude Code have different system prompts, tools, planning logic,
memory, compaction, subagents, and permission behavior. Results should therefore
be labeled as, for example:

```text
Codex CLI 0.x + model Y + high effort
Claude Code 2.x + model Z + high effort
```

rather than simply "GPT versus Claude."

A tightly controlled model-only comparison normally uses one shared scaffold
and provider APIs. The hard subscription constraint makes that unavailable in
many cases. The limitation should be stated explicitly in reports.

## Recommended scope for Agentic Assessment Toolkit

The Python package should own:

- A canonical assignment and dataset directory convention.
- A separate registry of solver prompt templates and experiment
  configurations.
- Importers that convert existing assignment folders into Harbor tasks.
- Materialization of a selected assignment and solver prompt as Harbor's
  `instruction.md`.
- Reusable container templates for common coursework environments.
- Shared RewardKit rubric templates.
- Task and oracle validation.
- Generation of pinned Harbor job configurations.
- Domain-specific aggregation and statistical reporting.
- Optional one-way exporters to course systems.

It should not implement:

- A new agent API.
- A Docker orchestration framework.
- A new model-provider abstraction.
- A new transcript schema.
- A new experiment database.
- A new results viewer.

A possible project layout is:

```text
benchmarks/
├── chemical_engineering/
│   ├── dataset.toml
│   ├── metric.py
│   └── tasks/
│       ├── material_balance_01/
│       ├── reactor_design_01/
│       └── process_control_01/
configs/
├── codex-high.yaml
├── codex-medium.yaml
├── claude-high.yaml
└── comparison.yaml
rubrics/
├── technical-report.toml
├── notebook-analysis.toml
└── spreadsheet-model.toml
src/agentic_assessment_toolkit/
├── importers/
├── validation/
└── reporting/
```

## Tiered recommendations

The tiers have the following meanings:

- **S:** viable foundation for the whole project.
- **A:** strong adjunct, specialized starting point, or optional extension.
- **B:** useful only under a narrower scope.
- **C:** benchmark or environment to import, not a general foundation.

| Tier | Approach | Subscription compatibility | Custom work | Recommendation |
|---|---|---:|---:|---|
| S1 | [Harbor](https://github.com/harbor-framework/harbor) + RewardKit | Native Codex and Claude solving | Low | Best durable foundation |
| S2 | Harbor `exec` + task template | Native | Very low | Fastest MVP; migrate to native tasks |
| S3 | [Promptfoo](https://github.com/promptfoo/promptfoo) + workspace fixture manager | Native Codex and Claude sessions | Low to medium | Best lightweight prototype |
| S4 | [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) + [Inspect SWE](https://github.com/meridianlabs-ai/inspect_swe) native-auth fork | Adapter required | Medium | Best research/statistics alternative |
| A1 | Harbor + [MLflow](https://github.com/mlflow/mlflow) | Native through Harbor | Low to medium | Add only when Harbor's viewer is insufficient |
| A2 | [ScienceAgentBench](https://github.com/OSU-NLP-Group/ScienceAgentBench) through Harbor | Native through Harbor | Very low | Best scientific proof of concept |
| A3 | [METR Task Standard](https://github.com/METR/task-standard) + Inspect | Adapter required | Medium to high | Strong portable task standard |
| A4 | [Otter-Grader](https://github.com/ucbds-infra/otter-grader) + Harbor | Native through Harbor | Low | Excellent notebook-specific deterministic scorer |
| A5 | [MLE-bench](https://github.com/openai/mle-bench) patterns + CLI solver | External CLI works | Medium | Useful for data-science competition tasks |
| A6 | [SWE-bench](https://github.com/SWE-bench/SWE-bench) harness + CLI solver | External CLI works | Low for code | Excellent for software repair only |
| A7 | [AutoRubric](https://github.com/delip/autorubric) | Model adapter required | Medium | Promising judge-calibration sidecar, but alpha |
| B1 | [DeepEval](https://github.com/confident-ai/deepeval) | Custom CLI model wrapper | Medium to high | Strong grader, weak assignment runner |
| B2 | [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) | Custom model wrapper | High | Add only for conventional text benchmarks |
| B3 | [OpenAI Evals](https://github.com/openai/evals) | Custom solver | High | Reuse ideas, not the foundation |
| B4 | [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) | API-oriented | Medium | Strong research agent, misses the auth requirement |
| B5 | [OpenHands benchmarks](https://github.com/OpenHands/benchmarks) | API or OpenHands credits | Medium | Use only if OpenHands is the chosen agent |
| C1 | [BrowserGym](https://github.com/ServiceNow/BrowserGym) + [AgentLab](https://github.com/ServiceNow/AgentLab) | Adapter required | High | Separate web-assignment track only |
| C2 | [OSWorld](https://github.com/xlang-ai/OSWorld) | Adapter required | High | Separate GUI/desktop track only |
| C3 | [tau2-bench](https://github.com/sierra-research/tau2-bench) | Adapter required | High | Conversational/tool-policy track only |

## Alternative foundations

### Promptfoo

Promptfoo now supports:

- Codex SDK and app-server providers.
- Claude Agent SDK.
- Working directories and agent tools.
- Prompt, provider, and test Cartesian products.
- Model and reasoning-effort variants.
- Repeats, concurrency, retries, rate controls, and disk caching.
- Deterministic assertions, `llm-rubric`, and filesystem-aware
  `agent-rubric`.
- HTML, JSON, JSONL, CSV, YAML, and JUnit exports.
- Existing Codex/ChatGPT and Claude subscription sessions.

References:

- [Coding-agent evaluation guide](https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/)
- [Codex SDK provider](https://www.promptfoo.dev/docs/providers/openai-codex-sdk/)
- [Claude Agent SDK provider](https://www.promptfoo.dev/docs/providers/claude-agent-sdk/)
- [Agent rubric](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/agent-rubric/)

The central weakness is that a writable `working_dir` is not a complete
per-trial workspace and artifact lifecycle. Without a wrapper, one model or
repeat can modify files seen by the next one. Official benchmark runs would
need to:

1. Create a unique workspace copy for every
   assignment/agent/configuration/repeat.
2. Run the agent in that copy.
3. Preserve the resulting directory or diff.
4. Grade it read-only.
5. Disable output caching during official stochastic runs, or ensure cache
   identity includes the immutable workspace digest.

Promptfoo is an excellent one- or two-day prototype. Harbor is safer once the
benchmark grows or begins executing untrusted code.

### Inspect AI

Inspect is stronger than Harbor in several evaluation-science areas:

- Rich built-in metrics.
- Epochs and repeated sampling.
- Bootstrap and clustered standard errors.
- Pass-at-k reducers.
- Mature deferred scoring.
- Detailed transcripts and DataFrame analysis.
- Strong evaluation-set retry and recovery behavior.

References:

- [Scoring workflow](https://inspect.aisi.org.uk/scoring-workflow.html)
- [Metrics](https://inspect.aisi.org.uk/metrics.html)
- [Eval sets](https://inspect.aisi.org.uk/eval-sets.html)
- [Sandboxing](https://inspect.aisi.org.uk/sandboxing.html)

However, stock Inspect SWE deliberately runs Codex CLI and Claude Code through
Inspect's host-side model-provider bridge. It uses the real CLI scaffold but
replaces native subscription authentication with API-provider routing.

Subscription-backed Inspect execution therefore requires a custom or forked
agent mode that:

- Disables the Inspect model bridge.
- Preserves the Inspect SWE CLI installation and transcript parsing.
- Uses native `codex exec` or `claude -p` authentication.
- Records native JSON events into the Inspect transcript.

Inspect is the strongest alternative when statistical controls justify a
moderate integration project. It is not the minimum-code choice for the hard
subscription requirement.

### METR Task Standard and Vivaria

[METR Task Standard](https://github.com/METR/task-standard) is a strong portable
specification for arbitrary task assets, setup, environments, instructions,
scoring, resource requirements, and optional auxiliary machines.

Its limitations for this project are:

- Agents and model calls are explicitly outside its scope.
- Rich rubrics and criterion-level feedback are not first-class.
- The canonical final score is comparatively restrictive.
- A separate runner and reporting system are required.
- Current activity is limited.

It is useful as a portability reference or through an Inspect bridge, but Harbor
already supplies a more complete task-plus-agent-plus-grader workflow.

[Vivaria](https://github.com/METR/vivaria) should not be chosen for a new
project. METR is transitioning toward Inspect, Vivaria has substantial
client/server and deployment overhead, and subscription CLI integration is not
native.

### General evaluation libraries

[DeepEval](https://github.com/confident-ai/deepeval) is a capable Python and
pytest-oriented evaluation library with G-Eval-style rubrics and specialized
agent/tool/plan metrics. It does not provision disposable assignment
workspaces, launch subscription coding agents, or retain complete solution
directories. Both the execution and subscription adapters would be custom.

[lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) is
excellent for conventional MMLU, GSM8K, and language-model benchmarks, including
public prompt configurations, caching, seeds, standard errors, and sample logs.
It is not an autonomous agent or mutable-workspace benchmark framework and does
not supply generic rubric judging.

[OpenAI Evals](https://github.com/openai/evals) contains useful model-graded
templates and a beta solver abstraction, but it lacks a general secure
assignment-folder lifecycle, first-class experiment grid, and strong
fine-grained recovery. Its open-source package has also seen comparatively
sparse feature development. It should be treated as a source of patterns, not a
new core dependency.

## Experiment tracking and reporting

Harbor's immutable job directories and local viewer are sufficient for the
first implementation. Adding a second observability system immediately would
duplicate job identities, datasets, scores, and artifacts.

### MLflow

[MLflow](https://github.com/mlflow/mlflow) is the preferred optional tracking
layer after Harbor's built-in viewer becomes insufficient. It is Apache-2.0,
runs locally with SQLite, stores arbitrary artifacts, and supports searchable
parameters, metrics, tags, traces, datasets, and feedback.

Use it later for:

- Long-lived searchable experiment history.
- Multi-user access.
- Cross-project dashboards.
- Human annotations and feedback.
- Publication-oriented experiment indexing.

References:

- [MLflow tracking](https://mlflow.org/docs/latest/ml/tracking/)
- [GenAI evaluation](https://mlflow.org/docs/latest/genai/eval-monitor/index.html)
- [Evaluating existing traces](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/traces/)

### Other observability products

- [Phoenix](https://github.com/Arize-ai/phoenix) has a good local LLM-native UI
  and SQLite deployment, but uses Elastic License 2.0 and is less natural for
  arbitrary solution-directory artifacts.
- [Langfuse](https://github.com/langfuse/langfuse) has a capable MIT-licensed
  core, but a full self-hosted deployment requires several services and
  substantial resources.
- [W&B Weave](https://github.com/wandb/weave) has an open SDK, but its normal
  supported UI and self-managed platform are not a lightweight free local
  deployment.
- Braintrust provides a polished managed comparison experience, but the full
  platform is proprietary.
- DuckDB and Parquet are a good archival fallback, but adopting them directly
  would require inventing the run schema and reports that Harbor already
  provides.

## Scientific and chemical-engineering references

### ScienceAgentBench

[ScienceAgentBench](https://github.com/OSU-NLP-Group/ScienceAgentBench) contains
102 authentic scientific data-analysis tasks derived from research workflows.
An existing
[Harbor dataset](https://hub.harborframework.com/datasets/scienceagentbench/scienceagentbench/latest)
provides converted tasks, scientific data, deterministic and judge-based
evaluation, and CPU/GPU execution variants.

It is the best ready-made proof that Harbor can support scientific assignments
rather than only software-engineering tasks.

### Terminal-Bench Science

[Terminal-Bench Science](https://github.com/harbor-framework/terminal-bench-science)
is directly relevant to chemistry and computational-science task authoring. It
is still small and in progress, so it should be studied as a task-quality
reference rather than adopted as a primary dataset.

Its task-design guidance is valuable:

- Prefer deterministic outcome verification.
- Pin scientific dependencies.
- Avoid live external services in normal tests.
- Validate stochastic tasks statistically.
- Derive ground truth rather than using unexplained constants.
- Use LLM judging only when deterministic verification is inadequate and
  document the reason.

### Terminal-Bench

[Terminal-Bench 2.1](https://github.com/harbor-framework/terminal-bench-2-1) is a
useful current source of Harbor task examples.

The legacy [`terminal-bench`/`tb`](https://github.com/harbor-framework/terminal-bench)
harness should not be used for new infrastructure. Its maintainers direct new
Terminal-Bench users to Harbor.

### Specialized formats

Not every chemical-engineering environment will be portable:

- Python, Jupyter, R, LaTeX, open data formats, and GNU Octave are straightforward
  to package in Linux containers.
- MATLAB may require institutional licensing and a suitable container/runtime
  arrangement.
- Aspen Plus, HYSYS, COMSOL, and other proprietary desktop tools may require
  licensed Windows machines or VM-backed tasks rather than ordinary Linux
  containers.
- Handwritten PDFs and scans require OCR and careful multimodal grading.

Harbor can still represent such tasks, but software licensing and environment
availability remain external constraints.

## Education and grading platforms

Education platforms should remain downstream integrations rather than the
benchmark spine.

### Otter-Grader

[Otter-Grader](https://github.com/ucbds-infra/otter-grader) is the best optional
deterministic scorer for notebook-heavy coursework. It supports Python and R
notebooks, R Markdown, and Quarto; public and hidden tests; partial credit;
parallel Docker grading; structured results; CSV output; and PDF generation.

Best use:

```text
Harbor agent solves notebook assignment
        ↓
Harbor verifier invokes Otter
        ↓
Otter test scores + RewardKit qualitative rubric
```

It is not a general arbitrary-folder benchmark schema.

### PrairieLearn

[PrairieLearn](https://github.com/PrairieLearn/PrairieLearn) is a strong
downstream teaching platform for parameterized STEM questions, manual grading,
Jupyter or VS Code workspaces, external Docker graders, and LMS integration. It
is a substantial course-delivery server, not an LLM benchmark runner.

### Submitty

[Submitty](https://github.com/Submitty/Submitty) provides rich institutional
submission, autograding, rubric, TA-assignment, peer-grading, inquiry, and
reporting workflows. It is operationally heavy and lacks model/agent experiment
semantics. Export benchmark grades to it later rather than making it the
benchmark core.

### nbgrader

[nbgrader](https://github.com/jupyter/nbgrader) is appropriate when an existing
JupyterHub/formgrader workflow already exists. It supplies notebook assignment
generation, hidden-test restoration, a gradebook, batch autograding, manual
comments, and HTML feedback. It lacks a built-in sandbox and is less convenient
than Otter as a benchmark component.

### GitHub Classroom

GitHub Classroom should not be used for new work. GitHub closed new sign-ups on
2026-05-26 and scheduled the service and API to shut down on 2026-08-28:
[GitHub Classroom retirement announcement](https://github.blog/changelog/2026-05-26-github-classroom-sign-ups-are-no-longer-available/).

The reusable idea is simply a starter repository plus protected tests and CI,
not the retiring service.

### AutoRubric

[AutoRubric](https://github.com/delip/autorubric) is a new MIT-licensed grading
sidecar with weighted positive and negative criteria, multiple scale types,
judge ensembles, few-shot calibration, checkpointed batches, agreement
statistics, and bootstrap metrics. Its research includes a college-chemistry
evaluation.

It is still alpha and expects provider API credentials or local models. It is
worth revisiting if judge calibration becomes central, but Promptfoo's
subscription-aware `agent-rubric` or RewardKit is simpler initially.

## Benchmark methodology

### Development runs

For ordinary development:

- Use one attempt per task and configuration.
- Use low concurrency to conserve subscription capacity.
- Use public development rubrics and tests.
- Cache Docker images and dependencies, but not stochastic model outputs.
- Validate every task with the instructor/oracle solution.

### Reportable runs

For results intended for a report, paper, or meaningful comparison:

- Use at least five independent attempts per task/configuration when subscription
  allowances permit.
- Interleave competing agents and configurations across time to reduce provider
  drift.
- Pin the Harbor release or commit.
- Pin every agent CLI version and disable automatic updates.
- Use exact model identifiers where the subscription product exposes them.
- Record the date when only a floating product snapshot is available.
- Pin prompt, rubric, assignment, and dependency hashes.
- Pin container images by digest rather than `latest`.
- Preserve raw native CLI transcripts as the source of truth.
- Preserve normalized ATIF trajectories for cross-agent analysis.
- Treat timeouts, refusals, parse failures, and agent failures as explicit
  outcomes.
- Do not silently drop failures from the denominator.
- Report mean score, per-assignment distributions, pass-at-1 or pass-at-k where
  appropriate, wall time, and token usage when it is actually exposed.
- Never infer monetary cost for a flat-rate subscription run.
- Report 95% uncertainty intervals.
- Bootstrap by assignment rather than treating repeated attempts as new,
  independent assignments.

Harbor's default aggregation is intentionally simple. A dataset-level
`metric.py` should calculate:

- Per-dimension means.
- Overall mean or weighted mean.
- Pass rate and eligible pass-at-k statistics.
- Bootstrap confidence intervals clustered by assignment.
- Infrastructure-error, timeout, refusal, and parse-failure rates.
- Score distributions by domain and difficulty.

See Harbor's
[custom metrics documentation](https://www.harborframework.com/docs/datasets/metrics).

### Grader calibration

Before trusting model-based grading:

1. Create a diverse instructor- or TA-graded calibration set.
2. Retain a separate holdout that is never used to tune the judge.
3. Blind human and model graders to the solver identity.
4. Measure human-human and model-human agreement for every rubric dimension.
5. Prefer focused, single-dimension judgments over one large holistic prompt.
6. Freeze the judge prompt, model, effort, and rubric before the benchmark run.
7. Audit all borderline cases and a random sample of ordinary cases.
8. Use deterministic evidence wherever possible and reserve LLM judging for
   genuinely semantic criteria.

### Prompt-injection tests

Candidate solutions, filenames, comments, PDFs, notebook cells, and command
output must all be treated as untrusted.

The grader-calibration suite should contain adversarial examples such as:

- Fake rubric overrides.
- `GRADE: 100` instructions.
- Instructions embedded in source comments or documents.
- Delimiter and zero-width-character attacks.
- Attempts to cause shell, network, connector, or MCP use.
- Extremely large files or outputs.
- Malicious filenames and file layouts.

Judges should use strict structured-output schemas. Parse failures should be
recorded as errors or unscored results, never silently converted into a pass or
ordinary zero.

## Security

### Subscription credentials inside sandboxes

Harbor's stock subscription integration makes Codex or Claude credentials
available inside the agent container. This is acceptable only for trusted,
professor-authored benchmark tasks and trusted images.

It is unsafe for arbitrary student-controlled or adversarial inputs because:

- Codex `auth.json` contains reusable authentication material.
- Claude setup tokens may be long-lived.
- Code or instructions inside the same container can attempt to read or disclose
  those credentials.
- Docker does not hide a secret from code running inside that container.

For real student submissions:

- Do not place personal subscription credentials in the submission container.
- Execute student code in a disposable container or VM.
- Export a sanitized, read-only evidence bundle.
- Grade the evidence separately.
- Use a host-side judge or a narrowly scoped credential broker when subscription
  judging is required.
- Never mount the Docker socket, user home, SSH keys, cloud credentials, or broad
  host directories.
- Prefer institutional accounts and policies for student data.

For a future public or adversarial benchmark, the secure architecture is a
host-side inference broker:

```text
agent CLI in sandbox
        ↓
narrow per-run localhost proxy
        ↓
host-side subscription credential
        ↓
provider
```

The sandbox receives no reusable account credential. The broker should enforce
model allowlists, per-run budgets, rate limits, and audit logging. Inspect's
[agent bridge](https://inspect.aisi.org.uk/agent-bridge.html) is a useful design
reference, although subscription-backed host providers would still require an
adapter.

### Network policy and Harbor hardening

Network access is a benchmark condition, not a setting that should be disabled
globally. For the primary use case, where an agent is allowed to search for
documentation or even find a published solution, Harbor's public network mode
is appropriate. It is part of the capability being measured. A blanket
`no-network` policy can also prevent an in-sandbox Codex or Claude agent from
reaching its model provider.

Use an explicit policy for each stage:

| Stage | Recommended policy | Purpose |
| --- | --- | --- |
| Solver on trusted, professor-authored assignments | Public network | Allows web research, documentation access, and package installation |
| Explicitly offline solver benchmark | No network | Measures unaided solving under reproducible offline conditions |
| Restricted solver benchmark | Provider and approved-domain egress | Allows the agent to operate while constraining unrelated access |
| Deterministic verifier | No network unless a test requires it | Makes verification repeatable and limits untrusted code |
| LLM rubric grader | Provider-only egress or a host-side judge | Allows model calls without general browsing |
| Execution of untrusted student code | No network | Prevents exfiltration, callbacks, downloads, and network abuse |

Do not combine results from public, restricted, and offline modes as though they
were the same benchmark condition. Record the effective network policy with
every trial. Public-web results can also drift as external pages and package
repositories change, so retain timestamps, retrieved evidence where lawful,
dependency locks, and full trajectories.

For a normal web-enabled solving run, a suitable baseline is:

```toml
[environment]
network_mode = "public"

[agent]
user = "agent"

[verifier]
environment_mode = "separate"

[verifier.environment]
network_mode = "no-network"
```

The verifier setting above assumes purely local deterministic checks. If the
verifier launches a model-based judge, it needs controlled access to that model
provider or the judgment should run through a host-side service. The important
hardening boundary is the separate verifier, which keeps hidden tests,
reference answers, and rubrics out of the solver environment.

For arbitrary student submissions, execute student code in a disposable,
credential-free, no-network sandbox and pass only sanitized, read-only evidence
to the grader. An allowlist does not by itself solve credential exposure if a
reusable credential remains readable by untrusted code.

Additional requirements:

- Run both agent and verifier as non-root users where possible.
- Give the verifier only explicitly declared artifacts.
- Keep tests, reference answers, and grader credentials out of the solver
  environment.
- Use private verifier images identified by digest.
- Do not mount the Docker daemon or socket.
- Limit CPU, memory, storage, process count, and wall time.
- Prefer VM or microVM-backed environments for adversarial executable content.
- Treat logs and trajectories as sensitive because they may contain assignment
  content, model output, or accidentally disclosed secrets.

### Student privacy

Real student submissions may contain names, IDs, email addresses, comments, and
grades. Before sending them through a personal consumer subscription:

- Remove direct and indirect identifiers.
- Keep the re-identification map outside the benchmark system.
- Disable optional training or data-sharing settings.
- Confirm institutional policy and vendor agreements.
- Prefer institution-managed Business, Team, Edu, or Enterprise accounts.
- Establish access controls and retention periods for artifacts and transcripts.

The grading capability is technically a side effect of the benchmark design,
but the governance requirements are different from those of a synthetic
benchmark.

## Adoption plan

### Phase 1: proof of concept

1. Pin Harbor v0.20.0 for an authentication and execution smoke test.
2. Pin the post-release regrade commit, or upgrade to the next stable release
   once available.
3. Select two representative assignments:
   - One deterministic numerical or notebook assignment.
   - One open-ended report or data-analysis assignment.
4. Use local Docker.
5. Test Codex and Claude subscription authentication.
6. Require solutions under a consistent path such as `/app/submission`.
7. Preserve the complete submission directory as a declared artifact.
8. Use separate verifier containers.
9. Combine deterministic RewardKit checks with Claude subscription rubric
   criteria.
10. Regrade the recorded solutions with a modified rubric.

Expected effort: approximately one to two days.

### Phase 2: durable benchmark convention

1. Define the project task and dataset directory convention.
2. Add a converter for consistently structured existing assignments.
3. Add reusable Python/Jupyter/LaTeX/Octave/spreadsheet environment templates.
4. Add oracle validation and task linting.
5. Add reusable rubric templates.
6. Add pinned job configurations for agents, models, prompts, and efforts.
7. Add one dataset-level statistical `metric.py`.
8. Document artifact and metadata requirements.

Expected effort: approximately one week, excluding domain rubric authoring.

### Phase 3: methodological validation

1. Build a human-graded calibration set.
2. Build adversarial prompt-injection cases for the grader.
3. Run at least five attempts per task/configuration on a manageable pilot.
4. Measure judge-human and human-human agreement.
5. Add confidence intervals and explicit failure accounting.
6. Freeze task, prompt, environment, and grader versions for the first official
   benchmark.

Most work in this phase is domain and evaluation design rather than software
engineering.

### Phase 4: optional extensions

Add only when a concrete need exists:

- MLflow for long-lived, multi-user experiment tracking.
- Otter-Grader for notebook-specific grading.
- PrairieLearn, Submitty, or LMS exporters for course operation.
- A host-side subscription credential broker for untrusted assignments.
- Cloud or VM-backed sandboxes for scale or adversarial execution.
- Conventional lm-evaluation-harness tasks for static language-model baselines.
- BrowserGym or OSWorld as separate web and desktop benchmark tracks.

## Final decision

The strongest one-platform answer is:

> Adopt Harbor and RewardKit as the execution, sandboxing, trajectory,
> artifact, grading, and results platform. Build Agentic Assessment Toolkit as a
> thin domain-specific layer around Harbor.

The lowest-code alternative is:

> Use Promptfoo with a small workspace-copy and archive wrapper, accepting weaker
> environment isolation and artifact lifecycle guarantees.

The research-oriented alternative is:

> Use Inspect AI with a native-auth fork of Inspect SWE when richer statistical
> controls justify the additional subscription adapter.

Harbor removes nearly all of the generic code this project would otherwise need.
The remaining custom work is the work that cannot be eliminated responsibly:
assignment conversion, scientific environments, trusted grading evidence,
domain rubrics, judge calibration, and research-quality aggregation.
