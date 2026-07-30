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

## Documentation

- [Project brief](docs/brief.md) — vision, use cases, scope, and constraints.
- [Design](docs/design.md) — decisions, architecture of the two pipelines,
  and roadmap.
- [Research](docs/research.md) — frozen research snapshot: alternatives
  considered, methodology, security model.
- [Data conventions](docs/data-conventions.md) — strict code–data separation:
  this repository is always safe to publish; all real course and student data
  lives in an external data root and is never committed.

## Development

Requires Python 3.12+. Install and validate with:

```bash
pip install -e ".[dev]"
make check
```
