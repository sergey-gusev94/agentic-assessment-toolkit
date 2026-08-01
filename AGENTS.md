# AGENTS.md

## Project purpose

Agentic Assessment Toolkit is a Python toolkit for benchmarking coding
agents on university coursework and for LLM-assisted grading of student
submissions, built as a thin layer over Harbor.
The distribution is named `agentic-assessment-toolkit`; the Python import
package is `agentic_assessment_toolkit`. Documentation and examples may use
`aat` as a local import alias.

Decisions and contracts live under `docs/` (start with `docs/design.md`).
Documentation is maintained in place, not as a changelog: superseded
decisions are rewritten, not preserved as history.

## Development

The project is in its initial development stage. Backward compatibility is not
required unless explicitly requested.

## Validation

Run `make check` after changes. It performs formatting verification, linting,
strict type checking, and tests. Run `make format` to apply formatting.

Normal tests must be deterministic, local, offline, and credential-free.

Live execution — Harbor runs, Docker, subscription-authenticated agent or
model calls, network access to model providers — is out of scope for
repository work. Do not attempt it or make any deliverable depend on it; the
maintainer performs all live validation outside the repository.
