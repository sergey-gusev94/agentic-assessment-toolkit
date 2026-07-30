# Code–Data Separation Conventions

This document specifies the concrete conventions behind the "strict code–data
separation" constraint in [brief.md](brief.md). The repository must always be
safe to publish; all real data lives outside it. This is a specification for
future implementation — none of it is enforced in code yet.

## Data root

All real data lives under a single directory outside the repository working
tree, called the **data root**.

Resolution order:

1. An explicit path passed programmatically or on the command line.
2. The `AAT_DATA_DIR` environment variable.
3. Otherwise: fail with a clear error. There is no default inside the
   repository, and the toolkit must refuse a data root that resolves to a
   path inside the repository working tree.

## Data root layout

```text
$AAT_DATA_DIR/
├── courses/                    # immutable source-of-truth course content
│   └── <course_id>/            #   e.g. PU_CHE456_F2025
│       ├── assignments/        #   one directory per assignment, as received
│       └── reference_solutions/#   instructor/oracle solutions
├── submissions/                # real student submissions, as received
│   └── <course_id>/
├── tables/                     # rosters, grade exports, identity mappings
├── runs/                       # Harbor jobs: trials, artifacts, transcripts,
│                               #   trajectories, verifier output
├── grading/                    # grading-assistant outputs for real students
└── scratch/                    # disposable working space
```

Notes:

- `courses/` content is immutable once registered; benchmark results
  reference assignments by hash.
- Transcripts and trajectories under `runs/` are data, not logs: they embed
  full assignment content and possibly student text.
- `tables/` holds the only mapping between real identities and anonymized
  IDs; it never leaves the data root.

## What is committable and what is not

| Committable (repository) | Never committed (data root) |
|---|---|
| Package code and tests | Real assignments and handouts |
| Documentation | Reference/oracle solutions |
| Rubric templates | Student submissions |
| Solver prompt templates | Rosters, grade exports, identity maps |
| Experiment/job config templates | Harbor runs, transcripts, trajectories |
| Environment (Dockerfile) templates | Grading outputs for real students |
| Small fully synthetic example assignments and fixtures | Credentials and auth files |

The research document's sketch layout places `benchmarks/.../tasks/` inside
the repository; this convention supersedes that detail. Real course
assignments are course-owned content and belong in the data root. The
repository may contain only synthetic example tasks created for tests,
documentation, and demos.

## Repository-side guards (defense in depth)

The structural rule above is the primary defense. In addition, the repository
should keep:

- `.gitignore` patterns for data-shaped directories (`data/`, `runs/`,
  `reference_repos/`), so accidental in-repo copies stay untracked.
- A pre-commit guard that rejects notebooks, CSV/XLSX files, PDFs, and files
  above a small size threshold anywhere outside an explicit synthetic-fixture
  allowlist (e.g. `tests/fixtures/`).

## Tests and examples

Tests and documentation examples use only small, fully synthetic fixtures
authored for this repository. They must never be derived from real course
content or student work, consistent with the AGENTS.md rule that tests are
deterministic, local, offline, and credential-free.
