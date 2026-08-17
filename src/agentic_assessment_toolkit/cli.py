"""The ``aat`` command line: solve and grading runs over Harbor, plus reports.

The run commands (docs/design.md, "CLI design"): ``aat solve`` and
``aat grade`` materialize tasks and launch Harbor; ``aat report`` is
read-only — it renders the statistics tables and Markdown report from
the data root, changing no experiment and no doneness. Three option
axes: selection and mechanics are flags; experiment configuration lives
only in named config files. Doneness is derived from the data root — a
solve item is done under a config when some job directory holds a
verified trial (one whose verifier recorded a reward) for its per-item
identity; a grading item additionally needs a valid grading result — so
bulk commands are naturally incremental and failed gradings are
regraded automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import base_images as base_images_mod
from . import check_course as check_course_mod
from . import config as config_mod
from . import data_root as data_root_mod
from . import harbor as harbor_mod
from . import hashing, provenance
from . import ingest as ingest_mod
from . import intake as intake_mod
from . import jobs as jobs_mod
from . import metrics as metrics_mod
from . import report as report_mod
from . import rubric as rubric_mod
from .base_images import BaseImageError
from .config import ConfigError, ExperimentConfig, Stage
from .course import CourseError
from .data_root import DataRootError
from .materialize._common import MaterializeError
from .materialize.grading import materialize_grading_task, prior_gradings_hash
from .materialize.solve import materialize_solve_task

SOLVE_JOBS_DIRNAME = "solving"
GRADING_JOBS_DIRNAME = "grading"
DATA_ROOT_HELP = "data root (default: AAT_DATA_DIR, then ~/aat-data)"
GUROBI_LICENSE_ENV_VAR = "AAT_GUROBI_LICENSE_FILE"


class CliError(Exception):
    """A usage or selection error surfaced to the user."""


@dataclass(frozen=True)
class _PlannedItem:
    item_id: str
    item_identity: str
    # Valid trials already pooled for (item_id, item_identity) across
    # jobs — what target-count --repeats subtracts from.
    existing: int
    environment_flavor: str
    materialize: Callable[[Path], harbor_mod.RunRecordItem]

    @property
    def done(self) -> bool:
        return self.existing > 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _run(args)
    except (
        BaseImageError,
        CliError,
        ConfigError,
        CourseError,
        DataRootError,
        harbor_mod.HarborAuthenticationError,
        MaterializeError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-root", metavar="PATH", help=DATA_ROOT_HELP)
    common.add_argument(
        "--config",
        required=True,
        metavar="NAME",
        help="experiment config name (configs/NAME.toml) or path",
    )
    common.add_argument(
        "--repeats",
        type=_positive_int,
        default=1,
        metavar="N",
        help=(
            "target trial count per item: ensure N valid trials exist, launching "
            "only each item's deficit (sampling depth, not identity); with "
            "--force, add N more instead"
        ),
    )
    common.add_argument(
        "--max-concurrent-trials",
        type=_positive_int,
        default=jobs_mod.DEFAULT_MAX_CONCURRENT_TRIALS,
        metavar="N",
        help=(
            "maximum Harbor trials running concurrently "
            f"(default: {jobs_mod.DEFAULT_MAX_CONCURRENT_TRIALS})"
        ),
    )
    common.add_argument(
        "--force",
        action="store_true",
        help="include already-done items; never overwrites, trials accumulate",
    )
    common.add_argument("--dry-run", action="store_true", help="list what would run, then exit")
    common.add_argument(
        "--materialize-only",
        action="store_true",
        help="write tasks, job config, and run record without invoking harbor",
    )

    parser = argparse.ArgumentParser(
        prog="aat", description="Materialize and launch Harbor solve and grading jobs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve = subparsers.add_parser(
        "solve", parents=[common], help="solve assignments with the configured agent"
    )
    solve.add_argument("--course", metavar="ID")
    solve.add_argument("--assignment", metavar="ID")
    solve.add_argument(
        "--gurobi-license-file",
        metavar="PATH",
        help=(
            "read-only host license file mounted into optimization containers "
            f"(default: {GUROBI_LICENSE_ENV_VAR})"
        ),
    )
    solve.add_argument(
        "--all", action="store_true", dest="all_items", help="every assignment of every course"
    )

    grade = subparsers.add_parser(
        "grade", parents=[common], help="grade submissions against reference solutions"
    )
    grade.add_argument(
        "--from-solve",
        metavar="NAME",
        help="grade verified solve trials produced under this solve config",
    )
    grade.add_argument(
        "--submissions", metavar="PATH", help="student folders under <data-root>/submissions"
    )
    grade.add_argument("--course", metavar="ID")
    grade.add_argument("--assignment", metavar="ID")
    grade.add_argument(
        "--all",
        action="store_true",
        dest="all_items",
        help="every student submission of every course",
    )
    grade.add_argument(
        "--sample",
        type=_positive_int,
        metavar="N",
        help=(
            "per assignment, grade only the first N submitted students in the "
            "deterministic hash order (student selection only; excludes "
            "pseudo-students)"
        ),
    )
    grade.add_argument(
        "--context-from",
        metavar="NAME",
        help=(
            "final-judge runs only: the initial grading config whose stored "
            "gradings each judge task presents as context"
        ),
    )
    grade.add_argument(
        "--gradings",
        type=_positive_int,
        metavar="N",
        help=(
            "final-judge runs only: present exactly N prior gradings per "
            "item — the first N usable ones under the --context-from config "
            "in the pool's deterministic order; items with fewer are "
            "skipped loudly"
        ),
    )

    intake = subparsers.add_parser(
        "intake", help="run the intake agent over unprocessed raw course dumps"
    )
    intake.add_argument("--course", metavar="ID")
    intake.add_argument(
        "--all",
        action="store_true",
        dest="all_items",
        help="every unprocessed dump under raw/",
    )
    intake.add_argument("--data-root", metavar="PATH", help=DATA_ROOT_HELP)
    intake.add_argument(
        "--model",
        default=intake_mod.DEFAULT_MODEL,
        metavar="NAME",
        help=f"codex model (default: {intake_mod.DEFAULT_MODEL})",
    )
    intake.add_argument(
        "--reasoning-effort",
        default=intake_mod.DEFAULT_REASONING_EFFORT,
        metavar="LEVEL",
        help=f"codex reasoning effort (default: {intake_mod.DEFAULT_REASONING_EFFORT})",
    )
    intake.add_argument(
        "--force",
        action="store_true",
        help="include processed and manually built courses (incremental pass)",
    )
    intake.add_argument("--dry-run", action="store_true", help="list what would run, then exit")
    intake.add_argument(
        "--print-prompt",
        action="store_true",
        help="print the rendered brief for --course (for an interactive session) and exit",
    )

    ingest = subparsers.add_parser(
        "ingest-submissions",
        help="normalize LMS submission exports into the submissions tree",
    )
    ingest.add_argument("--course", metavar="ID")
    ingest.add_argument(
        "--all",
        action="store_true",
        dest="all_items",
        help="every unprocessed dump under raw-submissions/",
    )
    ingest.add_argument("--data-root", metavar="PATH", help=DATA_ROOT_HELP)
    ingest.add_argument(
        "--force",
        action="store_true",
        help="also reprocess courses whose raw dump is unchanged",
    )
    ingest.add_argument("--dry-run", action="store_true", help="list what would run, then exit")

    check_auth = subparsers.add_parser(
        "check-auth",
        help="report the credential a config's agent would use, without launching (read-only)",
    )
    check_auth.add_argument(
        "--config",
        required=True,
        metavar="NAME",
        help="experiment config name (configs/NAME.toml) or path",
    )

    check = subparsers.add_parser(
        "check-course",
        help="report a course tree's contract violations, gaps, and intake notes (read-only)",
    )
    check.add_argument("--course", required=True, metavar="ID")
    check.add_argument("--data-root", metavar="PATH", help=DATA_ROOT_HELP)

    report = subparsers.add_parser(
        "report", help="render statistics tables and a Markdown report (read-only)"
    )
    report.add_argument("--course", metavar="ID", help="only this course")
    report.add_argument("--assignment", metavar="ID", help="only this assignment")
    report.add_argument(
        "--config",
        action="append",
        metavar="NAME",
        help="only named configs (repeatable; a solver name keeps its gradings too)",
    )
    report.add_argument(
        "--seed",
        type=int,
        default=metrics_mod.DEFAULT_SEED,
        metavar="N",
        help=f"bootstrap seed, recorded in provenance (default: {metrics_mod.DEFAULT_SEED})",
    )
    report.add_argument(
        "--out",
        metavar="PATH",
        help="report destination (default: <data-root>/analysis; never inside this repository)",
    )
    report.add_argument("--data-root", metavar="PATH", help=DATA_ROOT_HELP)

    init_data = subparsers.add_parser(
        "init-data", help="create the data root directory and its top-level layout"
    )
    init_data.add_argument("--data-root", metavar="PATH", help=DATA_ROOT_HELP)
    init_data.add_argument(
        "--git",
        action="store_true",
        help="also `git init` the data root and write a .gitignore for regenerable outputs",
    )
    return parser


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _run(args: argparse.Namespace) -> int:
    if args.command == "init-data":
        return _run_init_data(args)
    if args.command == "report":
        return _run_report(args)
    if args.command == "check-auth":
        return _run_check_auth(args)
    if args.command == "check-course":
        return _run_check_course(args)
    if args.command == "intake":
        return _run_intake(args)
    if args.command == "ingest-submissions":
        return _run_ingest_submissions(args)
    stage: Stage = "solve" if args.command == "solve" else "grade"
    root = data_root_mod.resolve_data_root(args.data_root)
    config = config_mod.load_config(_config_path(args.config))
    if config.stage != stage:
        raise CliError(
            f"config {config.name!r} has stage {config.stage!r}; `aat {args.command}` needs a {stage!r} config"
        )
    config_identity = config_mod.config_identity(config)
    # The name is a label; the identity is what the results are keyed by,
    # and it moves whenever the config, prompt, verifier, or task
    # template bytes change. Printing both means a reader never has to
    # guess which judge just ran.
    rubric_note = f", rubric {config.rubric_name!r}" if config.rubric_name is not None else ""
    print(
        f"config: {config.name}@{config_identity[:8]} ({config.agent} {config.model}{rubric_note})"
    )
    if config.agent not in config_mod.AGENT_TEMPLATE_SUFFIXES:
        # A supported Harbor agent the toolkit ships no image for still
        # runs; the cost is per trial, so it is said out loud rather than
        # left to be inferred from a build log.
        print(
            f"note: no environment template ships for agent {config.agent!r}; tasks build "
            "on the plain {flavor}.Dockerfile and Harbor installs that CLI in every trial"
        )
    # Resolved before planning, which hashes every selected submission:
    # a missing or malformed credential is setup, not selection, and
    # burying its error under a page of selection output is what makes a
    # first Claude run feel broken rather than unconfigured. The offline
    # modes stay credential-free, so `--dry-run` inspects any selection
    # without one.
    authentication = (
        None
        if args.dry_run or args.materialize_only
        else harbor_mod.resolve_harbor_authentication(config.agent)
    )
    if authentication is not None:
        print(f"auth: {authentication.method} (source: {authentication.source})")
    jobs_root = root / (SOLVE_JOBS_DIRNAME if stage == "solve" else GRADING_JOBS_DIRNAME)
    totals = harbor_mod.done_trial_totals(jobs_root, stage)
    if stage == "solve":
        gurobi_license_file = _resolve_gurobi_license_file(args.gurobi_license_file)
        n_judge_skipped = 0
        planned = _plan_solve(
            root,
            config,
            config_identity,
            totals,
            args,
            gurobi_license_file=gurobi_license_file,
        )
    else:
        gurobi_license_file = None
        planned, n_judge_skipped = _plan_grade(root, config, config_identity, totals, args)
    if stage == "solve" or not args.context_from:
        # Judge items share their item_id with the initial gradings of
        # the same submission, which a judge run requires to exist — so
        # the configuration-change note would fire on every fresh judge
        # run, implying a drift that never happened.
        _report_configuration_changes(planned, set(totals))
    status = _execute(
        stage,
        root,
        jobs_root,
        config,
        config_identity,
        planned,
        args,
        gurobi_license_file=gurobi_license_file,
        authentication=authentication,
    )
    if n_judge_skipped and not args.dry_run:
        # "Run outcomes are loud": a judge run that skipped items did
        # not deliver what was asked, even when everything it launched
        # succeeded — top up the initial gradings and re-run.
        print(
            f"judge: {n_judge_skipped} item(s) skipped with fewer than "
            f"{args.gradings} usable prior grading(s); top up (commands above), then re-run"
        )
        return status or 1
    return status


def _report_configuration_changes(planned: list[_PlannedItem], done: set[tuple[str, str]]) -> None:
    """Explain re-runs caused by a configuration change.

    An item that is not done under the current identity but has done
    trials under another one was measured before the configuration
    changed (config, prompt, environment, rubric, or task inputs).
    Re-running it is correct — doneness is per identity by design — but
    without this note a full re-run after a one-line prompt edit looks
    like data loss. Prior results stay untouched under their identity.
    """
    done_ids = {item_id for item_id, _ in done}
    changed = [item for item in planned if not item.done and item.item_id in done_ids]
    if changed:
        print(
            f"note: {len(changed)} of {len(planned)} item(s) have prior results under a "
            "different configuration (config, prompt, environment, rubric, or inputs "
            "changed) and will run again; prior results are kept under their old identity"
        )


def _run_init_data(args: argparse.Namespace) -> int:
    root, created = data_root_mod.init_data_root(args.data_root, git=args.git)
    if not created:
        print(f"data root {root} is already initialized; nothing to do")
        return 0
    print(f"initialized data root {root}")
    for name in created:
        print(f"  created {name}")
    return 0


def _run_report(args: argparse.Namespace) -> int:
    root = data_root_mod.resolve_data_root(args.data_root)
    # `is not None`, not truthiness: an empty value (typically an unset
    # shell variable) must stay a filter that matches nothing, never
    # silently widen the report to everything.
    report_dir = report_mod.write_report(
        root,
        courses=[args.course] if args.course is not None else None,
        assignments=[args.assignment] if args.assignment is not None else None,
        config_names=args.config,
        seed=args.seed,
        out_root=Path(args.out).expanduser().resolve() if args.out else None,
    )
    print(f"report directory: {report_dir}")
    return 0


def _run_intake(args: argparse.Namespace) -> int:
    if args.all_items and args.course:
        raise CliError("--all and --course are mutually exclusive")
    if args.print_prompt:
        if not args.course:
            raise CliError("--print-prompt needs --course")
        print(intake_mod.render_prompt(args.course))
        return 0

    root = data_root_mod.resolve_data_root(args.data_root)
    if args.course:
        selected = intake_mod.list_raw_courses(root, only=args.course)
        if not selected:
            raise CliError(f"no raw dump at {root / 'raw' / args.course}")
    elif args.all_items:
        selected = intake_mod.list_raw_courses(root)
        if not selected:
            raise CliError(f"no course dumps under {root / 'raw'}")
    else:
        raise CliError("select courses with --course or --all")

    to_run = [c for c in selected if args.force or c.status == "pending"]

    if args.dry_run:
        for course in selected:
            marker = "run " if course in to_run else "skip"
            print(f"{marker} [{course.status:7}] {course.course_id}")
        print(f"would run {len(to_run)} of {len(selected)} course(s)")
        return 0

    for course in selected:
        if course.status == "manual" and course not in to_run:
            print(
                f"skip {course.course_id}: courses/{course.course_id} exists with no "
                "intake record (built by hand?); use --force to run intake over it"
            )
    if not to_run:
        done = sum(1 for course in selected if course.status == "done")
        manual = sum(1 for course in selected if course.status == "manual")
        parts = []
        if done:
            parts.append(f"{done} already processed")
        if manual:
            parts.append(f"{manual} hand-built (skipped)")
        print(f"nothing to do: {', '.join(parts)}")
        return 0

    if intake_mod.codex_path() is None:
        raise CliError(
            "codex CLI not found on PATH; install it, or use "
            "`aat intake --course ID --print-prompt` for an interactive session"
        )

    failures = []
    for course in to_run:
        command = intake_mod.build_command(
            intake_mod.render_prompt(course.course_id), args.model, args.reasoning_effort
        )
        log_path = intake_mod.log_path_for(root, course.course_id)
        print(f"intake {course.course_id}: launching codex (log: {log_path})")
        exit_code = intake_mod.execute(command, cwd=root, log_path=log_path)
        if exit_code != 0:
            failures.append(course.course_id)
            print(f"intake {course.course_id}: codex exited {exit_code}; no receipt written")
            continue
        if not (root / "courses" / course.course_id).is_dir():
            failures.append(course.course_id)
            print(
                f"intake {course.course_id}: codex exited 0 but produced no "
                f"courses/{course.course_id}; no receipt written"
            )
            continue
        intake_mod.write_record(
            root,
            course.course_id,
            command=command,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            log_path=log_path,
        )
        print(check_course_mod.format_report(check_course_mod.check_course(root, course.course_id)))

    done = len(to_run) - len(failures)
    print(f"processed {done} of {len(to_run)} course(s)")
    if failures:
        print(f"failed (re-run `aat intake` to retry): {', '.join(failures)}")
        return 1
    return 0


def _run_ingest_submissions(args: argparse.Namespace) -> int:
    if args.all_items and args.course:
        raise CliError("--all and --course are mutually exclusive")
    root = data_root_mod.resolve_data_root(args.data_root)
    if args.course:
        selected = ingest_mod.list_raw_courses(root, only=args.course)
        if not selected:
            raise CliError(f"no raw submissions dump at {root / 'raw-submissions' / args.course}")
    elif args.all_items:
        selected = ingest_mod.list_raw_courses(root)
        if not selected:
            raise CliError(f"no submission dumps under {root / 'raw-submissions'}")
    else:
        raise CliError("select courses with --course or --all")

    to_run = [c for c in selected if args.force or c.status == "pending"]

    if args.dry_run:
        for course in selected:
            marker = "run " if course in to_run else "skip"
            print(f"{marker} [{course.status:7}] {course.course_id}")
        print(f"would ingest {len(to_run)} of {len(selected)} course(s)")
        return 0

    attention = 0
    # A processed course can still carry skipped/frozen rows from its
    # last run; the receipt keeps the counts so skipping the course
    # never silently reports clean.
    for course in selected:
        if course in to_run:
            continue
        recorded = ingest_mod.recorded_attention(ingest_mod.read_record(root, course.course_id))
        if recorded:
            print(
                f"ingest {course.course_id}: unchanged, but {recorded} submission(s) "
                "from the last run still need review (see tables/"
                f"{course.course_id}/{ingest_mod.SUBMISSIONS_CSV})"
            )
            attention += recorded

    if not to_run and attention == 0:
        print(f"nothing to do: {len(selected)} course(s) already processed")
        return 0

    for course in to_run:
        try:
            report = ingest_mod.ingest_course(root, course.course_id)
        except ingest_mod.IngestError as error:
            raise CliError(str(error)) from error
        print(ingest_mod.format_report(report))
        attention += len(report.attention)
    if attention:
        print(
            f"{attention} submission(s) need review (skipped or frozen); "
            "fix identities/zip mapping in manifest.toml, or resolve frozen "
            "conflicts, then re-run"
        )
        return 1
    return 0


def _run_check_auth(args: argparse.Namespace) -> int:
    """Report which credential a launch of this config would select.

    The same resolution a live run performs, with nothing launched and
    no data root touched — so a long run can be preceded by one cheap
    command instead of by its own first failure. Only the method and the
    selection source are printed, the two fields a run record keeps;
    the credential itself is never displayed, and neither is the path of
    a file that holds one.
    """
    config = config_mod.load_config(_config_path(args.config))
    authentication = harbor_mod.resolve_harbor_authentication(config.agent)
    if authentication is None:
        print(
            f"{config.name}: agent {config.agent!r} keeps Harbor's own authentication; "
            "aat resolves no credential for it"
        )
        return 0
    print(f"{config.name}: {authentication.description}")
    print(f"  method: {authentication.method}")
    print(f"  source: {authentication.source}")
    return 0


def _run_check_course(args: argparse.Namespace) -> int:
    root = data_root_mod.resolve_data_root(args.data_root)
    report = check_course_mod.check_course(root, args.course)
    print(check_course_mod.format_report(report))
    return 0 if report.ok else 1


def _config_path(value: str) -> Path:
    """A bare NAME resolves to ./configs/NAME.toml relative to the CWD.

    Resolved so error messages always show the absolute path that was
    tried.
    """
    if value.endswith(".toml") or os.sep in value:
        return Path(value).expanduser().resolve()
    return (Path("configs") / f"{value}.toml").resolve()


def _resolve_gurobi_license_file(value: str | None) -> Path | None:
    raw_path = value or os.environ.get(GUROBI_LICENSE_ENV_VAR, "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise CliError(f"Gurobi license file does not exist or is not a file: {path}")
    return path


def _plan_solve(
    root: Path,
    config: ExperimentConfig,
    config_identity: str,
    totals: dict[tuple[str, str], int],
    args: argparse.Namespace,
    *,
    gurobi_license_file: Path | None,
) -> list[_PlannedItem]:
    if args.all_items and args.course:
        raise CliError("--all and --course are mutually exclusive")
    if args.assignment and not args.course:
        raise CliError("--assignment requires --course")
    if args.all_items:
        course_ids = data_root_mod.list_courses(root)
        if not course_ids:
            raise CliError(f"no courses under {root / 'courses'}")
    elif args.course:
        course_ids = [args.course]
    else:
        raise CliError("select assignments with --course or --all")

    planned = []
    for course_id in course_ids:
        assignments = data_root_mod.list_assignments(root, course_id)
        if args.assignment is not None:
            assignments = [a for a in assignments if a.assignment_id == args.assignment]
            if not assignments:
                raise CliError(f"assignment {args.assignment!r} not found in course {course_id!r}")
        for assignment in assignments:
            if assignment.environment_flavor == config_mod.GRADING_FLAVOR:
                raise CliError(
                    f"assignment {assignment.item_id!r} resolves environment 'grading', "
                    "which is reserved for grading tasks; course flavors are for solve "
                    "tasks only (docs/design.md, environment templates)"
                )
            if gurobi_license_file is not None and assignment.environment_flavor != "optimization":
                raise CliError(
                    "--gurobi-license-file can only be used when every selected "
                    "assignment resolves environment 'optimization'; "
                    f"{assignment.item_id!r} resolves {assignment.environment_flavor!r}"
                )
            template_bytes = config_mod.environment_path(
                assignment.environment_flavor, config.agent
            ).read_bytes()
            identity = config_mod.item_identity(config_identity, template_bytes)
            planned.append(
                _PlannedItem(
                    item_id=assignment.item_id,
                    item_identity=identity,
                    existing=totals.get((assignment.item_id, identity), 0),
                    environment_flavor=assignment.environment_flavor,
                    materialize=_solve_materializer(assignment, config, identity),
                )
            )
    return planned


def _solve_materializer(
    assignment: data_root_mod.Assignment,
    config: ExperimentConfig,
    identity: str,
) -> Callable[[Path], harbor_mod.RunRecordItem]:
    def materialize(tasks_dir: Path) -> harbor_mod.RunRecordItem:
        task = materialize_solve_task(
            assignment_dir=assignment.directory,
            course_id=assignment.course_id,
            assignment_id=assignment.assignment_id,
            environment_flavor=assignment.environment_flavor,
            agent=config.agent,
            prompt_name=config.prompt_name,
            tasks_dir=tasks_dir,
        )
        return harbor_mod.RunRecordItem(
            item_id=task.item_id,
            task_dir_name=task.task_dir_name,
            item_identity=identity,
            course_id=assignment.course_id,
            assignment_id=assignment.assignment_id,
            input_hashes=task.input_hashes,
        )

    return materialize


@dataclass(frozen=True)
class _GradeSource:
    item_id: str
    course_id: str
    assignment_id: str
    submission_dir: Path
    name_parts: tuple[str, ...]
    # Lineage, copied verbatim into the run record's item.
    submission_source: str | None = None  # "student" | "solve-trial"
    student_id: str | None = None  # student submissions only
    solve_job_name: str | None = None  # solve-derived submissions only
    solve_trial_name: str | None = None


def _plan_grade(
    root: Path,
    config: ExperimentConfig,
    config_identity: str,
    totals: dict[tuple[str, str], int],
    args: argparse.Namespace,
) -> tuple[list[_PlannedItem], int]:
    """Plan the grading items; returns (items, judge items skipped).

    The skip count covers final-judge items short of ``--gradings``;
    it is zero for ordinary grading runs.
    """
    judge = _judge_context(root, config, args)
    sources = _grade_sources(root, args)
    # This config's own image, for this run's item identities. A judge
    # run's *lookup* of stored gradings uses the context config's
    # template instead (_JudgeContext.template_bytes), which is a
    # different image whenever the two configs name different agents.
    template_bytes = config_mod.environment_path(
        config_mod.GRADING_FLAVOR, config.agent
    ).read_bytes()
    rubric_name = config.rubric_name
    if rubric_name is None:  # load_config defaults grading configs to "default"
        raise CliError(f"config {config.name!r} names no rubric")

    n_skipped = 0
    planned = []
    # Many submissions share one assignment; hash each handout once.
    assignment_hashes: dict[Path, str] = {}
    for source in sources:
        assignment = data_root_mod.assignment_dir(root, source.course_id, source.assignment_id)
        if assignment not in assignment_hashes:
            assignment_hashes[assignment] = hashing.sha256_dir(assignment)
        reference = data_root_mod.reference_solution_dir(
            root, source.course_id, source.assignment_id
        )
        rubric = data_root_mod.find_rubric(
            root, source.course_id, source.assignment_id, rubric_name
        )
        if rubric is None:
            raise CliError(
                f"no rubric {rubric_name!r} for "
                f"{source.course_id}/{source.assignment_id}: grading never starts "
                "without a rubric (docs/design.md, decision 5); author "
                f"courses/{source.course_id}/rubrics/{source.assignment_id}/{rubric_name}.md "
                "first (the procedure in docs/data-conventions.md)"
            )
        # Parse at plan time so a bad rubric fails before any job
        # directory is created; the materializer parses it again.
        try:
            rubric_mod.parse_rubric_file(rubric)
        except rubric_mod.RubricError as error:
            raise CliError(str(error)) from error
        rubric_source = data_root_mod.find_rubric_source(
            root, source.course_id, source.assignment_id
        )
        rubric_source_hash = (
            hashing.sha256_dir(rubric_source) if rubric_source is not None else None
        )
        prior = None
        if judge is not None:
            prior = _prior_gradings_for(
                judge,
                root,
                source,
                assignment_hashes[assignment],
                rubric_source_hash,
                args,
            )
            if prior is None:
                # Short of --gradings; reported per item by
                # _prior_gradings_for, summarized (with a nonzero exit)
                # by _run.
                n_skipped += 1
                continue
        identity = config_mod.item_identity(
            config_identity,
            template_bytes,
            rubric.read_bytes(),
            assignment_hashes[assignment],
            rubric_source_hash,
            prior_gradings_hash(
                [(grading.result_path, grading.justification_path) for grading in prior]
            )
            if prior is not None
            else None,
        )
        planned.append(
            _PlannedItem(
                item_id=source.item_id,
                item_identity=identity,
                existing=totals.get((source.item_id, identity), 0),
                environment_flavor=config_mod.GRADING_FLAVOR,
                materialize=_grade_materializer(
                    source,
                    assignment,
                    reference,
                    rubric,
                    rubric_source,
                    config,
                    identity,
                    judge=judge,
                    prior=prior,
                ),
            )
        )
    _require_resolvable_rubric_history(root, sources)
    return planned, n_skipped


@dataclass(frozen=True)
class _JudgeContext:
    """Resolved final-judge inputs: the initial config and its stored gradings.

    ``template_bytes`` is the grading environment template of the
    *context* config's agent, not the judge's. Everything in the lookup
    key of a stored grading has to be the context config's own resolved
    input, or the key is one no stored grading was ever written under —
    and the two agents' templates differ, so a Claude judge over Codex
    gradings would otherwise match nothing.
    """

    config: ExperimentConfig
    config_identity: str
    template_bytes: bytes
    gradings: int
    pool: harbor_mod.PriorGradingPool


def _judge_context(
    root: Path, config: ExperimentConfig, args: argparse.Namespace
) -> _JudgeContext | None:
    """Validate the judge flags and gather the context config's gradings.

    A final-judge run needs all three legs — a ``judge = true`` config,
    ``--context-from``, and ``--gradings`` — and any partial
    combination is a usage error: a judge config without context would
    grade blind, and context supplied to an ordinary grader config
    would change the experiment without changing its identity.
    """
    if config.judge and not args.context_from:
        raise CliError(
            f"config {config.name!r} is a final-judge config (judge = true); select "
            "the initial gradings with --context-from NAME --gradings N"
        )
    if args.context_from and not config.judge:
        raise CliError(
            f"--context-from needs a final-judge config; {config.name!r} does not set judge = true"
        )
    if args.context_from and args.gradings is None:
        raise CliError(
            "--context-from requires --gradings N: the exact number of prior "
            "gradings each judge task presents (the evidence count is an "
            "experiment parameter, so it is never a default)"
        )
    if args.gradings is not None and not args.context_from:
        raise CliError("--gradings is only valid with --context-from")
    if not args.context_from:
        return None

    context_config = config_mod.load_config(_config_path(args.context_from))
    if context_config.stage != "grade":
        raise CliError(
            f"--context-from config {context_config.name!r} has stage "
            f"{context_config.stage!r}; the judge's context must be a grading config"
        )
    if context_config.judge:
        raise CliError(
            f"--context-from config {context_config.name!r} is itself a final-judge "
            "config; the judge's context must be the initial grading config"
        )
    if context_config.rubric_name != config.rubric_name:
        # A judge shown prior gradings measured against a different
        # rubric than the one its own criteria are enforced against
        # cannot reconcile them criterion by criterion, and the review
        # queue would compare scores across point splits.
        raise CliError(
            f"config {config.name!r} names rubric {config.rubric_name!r} but "
            f"--context-from config {context_config.name!r} names rubric "
            f"{context_config.rubric_name!r}; the judge and its context must "
            "grade against the same rubric"
        )
    return _JudgeContext(
        config=context_config,
        config_identity=config_mod.config_identity(context_config),
        template_bytes=config_mod.environment_path(
            config_mod.GRADING_FLAVOR, context_config.agent
        ).read_bytes(),
        gradings=args.gradings,
        pool=harbor_mod.prior_gradings_by_key(root / GRADING_JOBS_DIRNAME),
    )


def _prior_gradings_for(
    judge: _JudgeContext,
    root: Path,
    source: _GradeSource,
    assignment_hash: str,
    rubric_source_hash: str | None,
    args: argparse.Namespace,
) -> list[harbor_mod.PriorGrading] | None:
    """The item's ``--gradings`` prior gradings, or None (reported) when too few.

    Prior gradings are looked up by the *context* config's per-item
    identity — the same pooling key its own doneness uses — so every part
    of the key is a context-config input, down to its agent's grading
    environment template. The judge therefore consumes exactly the
    gradings that pool together under the frozen initial config, and a
    context rubric that has since advanced correctly matches nothing (the
    initial rounds under the new rubric do not exist yet). Within a pool
    the gradings are exchangeable
    repeats of one frozen experiment, so the selection is the first N
    in the pool's deterministic (job name, trial name) order: adding
    initial gradings later never changes what an existing judge item
    saw, and a larger N selects a strict superset of a smaller one's
    gradings — which is what lets the results layer supersede the
    smaller judgment after a re-judge (docs/design.md, decision 16).
    """
    context_rubric = data_root_mod.find_rubric(
        root, source.course_id, source.assignment_id, judge.config.rubric_name or "default"
    )
    key = None
    if context_rubric is not None:
        context_identity = config_mod.item_identity(
            judge.config_identity,
            judge.template_bytes,
            context_rubric.read_bytes(),
            assignment_hash,
            rubric_source_hash,
        )
        key = (source.item_id, context_identity)
    prior = judge.pool.by_key.get(key, []) if key is not None else []
    missing = judge.pool.missing_artifacts.get(key, 0) if key is not None else 0
    if len(prior) >= judge.gradings:
        return prior[: judge.gradings]
    detail = f"{len(prior)} of {judge.gradings} required prior grading(s)"
    if missing:
        detail += f" ({missing} more unusable: grading artifacts missing on disk)"
    command = _top_up_command(judge, root, source, args, usable=len(prior), missing=missing)
    print(
        f"skipping {source.item_id}: {detail} under config "
        f"{judge.config.name!r}; top up: {shlex.join(command)}"
    )
    return None


def _top_up_command(
    judge: _JudgeContext,
    root: Path,
    source: _GradeSource,
    args: argparse.Namespace,
    *,
    usable: int,
    missing: int,
) -> list[str]:
    """The exact initial-grading command that completes a skipped judge item.

    Doneness counts valid trials whether or not their artifacts survive,
    while the judge can only use gradings whose artifacts are on disk —
    so when unusable gradings exist, a plain target of ``gradings``
    would launch nothing. ``--force`` then adds exactly the usable
    shortfall instead.
    """
    command = ["aat", "grade", "--config", args.context_from]
    if source.submission_source == "student" and source.student_id is not None:
        submission = root / "submissions" / source.course_id / source.student_id
        command += ["--submissions", str(submission / source.assignment_id)]
    else:
        if args.from_solve:
            command += ["--from-solve", args.from_solve]
        command += ["--course", source.course_id, "--assignment", source.assignment_id]
    if missing:
        command += ["--force", "--repeats", str(judge.gradings - usable)]
    else:
        command += ["--repeats", str(judge.gradings)]
    return command


def _require_resolvable_rubric_history(root: Path, sources: list[_GradeSource]) -> None:
    """Refuse to grade an assignment whose stored results lost their rubric.

    A rubric name may advance to new bytes; the superseded bytes must
    stay on disk under ``archive/``, because every stored grading result
    refers to the version it was graded against by hash
    (docs/data-conventions.md, "Course content contract"). Checking here
    means the moment a revision would strand history is the moment it is
    caught — before another job's results are added to the pile.
    """
    orphans = provenance.orphaned_rubrics(
        root, assignments={(source.course_id, source.assignment_id) for source in sources}
    )
    if orphans:
        raise CliError(
            "stored grading results refer to rubric versions that are no longer "
            "on disk, so those results can no longer be resolved:\n"
            + "\n".join(f"  - {provenance.orphan_message(root, orphan)}" for orphan in orphans)
        )


def _grade_materializer(
    source: _GradeSource,
    assignment: Path,
    reference: Path,
    rubric: Path,
    rubric_source: Path | None,
    config: ExperimentConfig,
    identity: str,
    *,
    judge: _JudgeContext | None = None,
    prior: list[harbor_mod.PriorGrading] | None = None,
) -> Callable[[Path], harbor_mod.RunRecordItem]:
    def materialize(tasks_dir: Path) -> harbor_mod.RunRecordItem:
        task = materialize_grading_task(
            assignment_dir=assignment,
            submission_dir=source.submission_dir,
            reference_solution_dir=reference,
            rubric_path=rubric,
            rubric_source_dir=rubric_source,
            item_id=source.item_id,
            name_parts=source.name_parts,
            agent=config.agent,
            prompt_name=config.prompt_name,
            tasks_dir=tasks_dir,
            prior_gradings=(
                [(grading.result_path, grading.justification_path) for grading in prior]
                if prior is not None
                else None
            ),
        )
        return harbor_mod.RunRecordItem(
            item_id=task.item_id,
            task_dir_name=task.task_dir_name,
            item_identity=identity,
            course_id=source.course_id,
            assignment_id=source.assignment_id,
            input_hashes=task.input_hashes,
            submission_source=source.submission_source,
            student_id=source.student_id,
            solve_job_name=source.solve_job_name,
            solve_trial_name=source.solve_trial_name,
            context_config_name=judge.config.name if judge is not None else None,
            context_config_identity=judge.config_identity if judge is not None else None,
            prior_trials=(
                tuple(
                    harbor_mod.PriorTrialRef(
                        job_name=grading.job_name, trial_name=grading.trial_name
                    )
                    for grading in prior
                )
                if prior is not None
                else None
            ),
        )

    return materialize


def _grade_sources(root: Path, args: argparse.Namespace) -> list[_GradeSource]:
    # --course/--assignment narrow --from-solve rather than selecting a
    # second source, so they only count as a source on their own.
    selectors = [
        bool(args.from_solve),
        bool(args.submissions),
        bool((args.course or args.all_items) and not args.from_solve),
    ]
    if sum(selectors) != 1:
        raise CliError(
            "select exactly one submission source: --from-solve NAME "
            "(optionally narrowed by --course/--assignment), --submissions PATH, "
            "or --course/--all"
        )
    if args.from_solve and (args.submissions or args.all_items):
        raise CliError("--from-solve is narrowed by --course/--assignment only")
    if args.all_items and args.course:
        raise CliError("--all and --course are mutually exclusive")
    if args.assignment and not (args.course or args.from_solve):
        raise CliError("--assignment requires --course or --from-solve")

    if args.from_solve:
        if args.sample is not None:
            raise CliError(
                "--sample selects students, so it applies to student-submission "
                "selection only, never to --from-solve"
            )
        selection = harbor_mod.verified_solve_submissions(
            root / SOLVE_JOBS_DIRNAME,
            args.from_solve,
            course_id=args.course,
            assignment_id=args.assignment,
        )
        _report_skipped_solve_trials(selection, args.from_solve)
        return [
            _GradeSource(
                item_id=sub.item_id,
                course_id=sub.course_id,
                assignment_id=sub.assignment_id,
                submission_dir=sub.directory,
                name_parts=(sub.solve_job_name, sub.trial_name),
                submission_source="solve-trial",
                solve_job_name=sub.solve_job_name,
                solve_trial_name=sub.trial_name,
            )
            for sub in selection.submissions
        ]

    if args.submissions:
        if args.sample is not None and _submissions_path_depth(root, args.submissions) > 1:
            # A sample over a frame narrowed to one student (or one
            # student's assignment) is not a class panel; the recorded
            # sample would silently mean "of whatever the path kept".
            raise CliError(
                "--sample needs a course-wide frame; --submissions points below "
                "the course level, so select with --course (or a course-level "
                "path) instead"
            )
        sources = _student_sources_from_path(root, args.submissions)
    else:
        course_ids = [args.course] if args.course else _submission_courses(root)
        sources = _student_sources(root, course_ids, assignment_id=args.assignment, student_id=None)
    if args.sample is not None:
        sources = _sampled_sources(sources, args.sample)
    return sources


def _submissions_path_depth(root: Path, raw_path: str) -> int:
    """How many levels below submissions/ the path points (course = 1)."""
    submissions_root = (root / "submissions").resolve()
    path = Path(raw_path).expanduser().resolve()
    try:
        return len(path.relative_to(submissions_root).parts)
    except ValueError:
        return 0  # outside the tree; _student_sources_from_path rejects it


def _sample_order_key(course_id: str, student_id: str) -> tuple[str, str]:
    """The deterministic sample order: students sorted by id hash.

    Hashing the (course-scoped) student id gives an order that is fixed
    across configs, machines, and time — so every run samples the same
    students — while destroying any correlation with enrollment order,
    which the sequential ingest-assigned ids carry. No seed and no
    stored state: the panel is derivable from the data root alone, and
    the first N students are a prefix of the first N+K, so raising the
    sample later grades only the new students.
    """
    digest = hashlib.sha256(f"{course_id}/{student_id}".encode()).hexdigest()
    return (digest, student_id)


def _sampled_sources(sources: list[_GradeSource], sample: int) -> list[_GradeSource]:
    """Per assignment, the first ``sample`` submitted students in hash order.

    Pseudo-students (underscore-prefixed ids) are excluded from both the
    frame and the selection: sampling is about real class coverage, and
    grader-check submissions are selected deliberately via
    ``--submissions``. Taking the first N *submitted* students per
    assignment (a deterministic top-up past students who did not submit
    it) keeps the per-assignment count at N while the panel's core stays
    the same students on every assignment.
    """
    by_assignment: dict[tuple[str, str], list[_GradeSource]] = {}
    n_pseudo = 0
    for source in sources:
        if source.student_id is None:
            continue
        if source.student_id.startswith("_"):
            n_pseudo += 1
            continue
        by_assignment.setdefault((source.course_id, source.assignment_id), []).append(source)

    sampled: list[_GradeSource] = []
    for (course_id, assignment_id), group in sorted(by_assignment.items()):
        ordered = sorted(
            group, key=lambda source: _sample_order_key(course_id, str(source.student_id))
        )
        chosen = ordered[:sample]
        sampled.extend(chosen)
        print(
            f"sample: {course_id}/{assignment_id}: {len(chosen)} of {len(group)} "
            "submitted student(s) (deterministic hash-order prefix)"
        )
    if n_pseudo:
        print(
            f"sample: excluded {n_pseudo} pseudo-student submission(s); grade them "
            "deliberately with --submissions and no --sample"
        )
    return sampled


_SKIP_DESCRIPTIONS = {
    "solve-failed": "solve failed before producing a submission",
    "empty-submission": "submission artifact is missing or empty (output-contract failure)",
}


def _report_skipped_solve_trials(
    selection: harbor_mod.SolveSubmissionSelection, solve_config_name: str
) -> None:
    """Say what ``--from-solve`` passed over, and how to rerun it.

    Grading skips failed and empty solve trials by design — grading
    nonexistent work would be worse — but the skip must be loud: a
    per-trial line for every skip, and an explicit warning with the
    exact scoped rerun command for each assignment that has no gradable
    submission at all.
    """
    for skip in selection.skipped:
        description = _SKIP_DESCRIPTIONS.get(skip.reason, skip.reason)
        print(
            f"skipping solve trial {skip.solve_job_name}/{skip.trial_name} "
            f"({skip.course_id}/{skip.assignment_id}): {description}"
        )
    gradable = {(sub.course_id, sub.assignment_id) for sub in selection.submissions}
    missing = sorted(
        {(skip.course_id, skip.assignment_id) for skip in selection.skipped} - gradable
    )
    for course_id, assignment_id in missing:
        # A failed solve is not done and reruns incrementally; an
        # assignment whose every trial verified with an empty
        # submission needs --force for a fresh attempt.
        needs_force = all(
            skip.reason == "empty-submission"
            for skip in selection.skipped
            if (skip.course_id, skip.assignment_id) == (course_id, assignment_id)
        )
        command = [
            "aat",
            "solve",
            "--config",
            solve_config_name,
            "--course",
            course_id,
            "--assignment",
            assignment_id,
        ] + (["--force"] if needs_force else [])
        print(
            f"warning: {course_id}/{assignment_id} has no gradable submission "
            f"under solve config {solve_config_name!r}; rerun: {shlex.join(command)}"
        )


def _submission_courses(root: Path) -> list[str]:
    submissions_root = root / "submissions"
    if not submissions_root.is_dir():
        return []
    return sorted(entry.name for entry in submissions_root.iterdir() if entry.is_dir())


def _student_sources_from_path(root: Path, raw_path: str) -> list[_GradeSource]:
    submissions_root = (root / "submissions").resolve()
    path = Path(raw_path).expanduser().resolve()
    try:
        relative = path.relative_to(submissions_root)
    except ValueError:
        raise CliError(
            f"--submissions must point inside {submissions_root} "
            "(a course, student, or assignment directory)"
        ) from None
    parts = relative.parts
    if not parts:
        raise CliError("--submissions must name a course, student, or assignment directory")
    if len(parts) > 3:
        raise CliError("--submissions goes at most three levels deep: course/student/assignment")
    if not path.is_dir():
        raise CliError(f"--submissions path does not exist: {path}")
    course_id = parts[0]
    student_id = parts[1] if len(parts) >= 2 else None
    assignment_id = parts[2] if len(parts) == 3 else None
    return _student_sources(root, [course_id], assignment_id=assignment_id, student_id=student_id)


def _student_sources(
    root: Path,
    course_ids: list[str],
    *,
    assignment_id: str | None,
    student_id: str | None,
) -> list[_GradeSource]:
    sources = []
    for course_id in course_ids:
        for submission in data_root_mod.list_student_submissions(root, course_id, assignment_id):
            if student_id is not None and submission.student_id != student_id:
                continue
            sources.append(
                _GradeSource(
                    item_id=submission.item_id,
                    course_id=submission.course_id,
                    assignment_id=submission.assignment_id,
                    submission_dir=submission.directory,
                    name_parts=(
                        submission.course_id,
                        submission.student_id,
                        submission.assignment_id,
                    ),
                    submission_source="student",
                    student_id=submission.student_id,
                )
            )
    return sources


@dataclass(frozen=True)
class _LaunchedJob:
    """One Harbor job of this invocation: a deficit group's directory and items."""

    job_dir: Path
    record_items: list[harbor_mod.RunRecordItem]
    repeats: int  # this job's n_attempts: the group's deficit (or --force's N)
    command: list[str]


def _write_job_record(
    job: _LaunchedJob,
    *,
    executed: bool,
    stage: Stage,
    config: ExperimentConfig,
    config_identity: str,
    args: argparse.Namespace,
    cli_version: str | None,
    authentication: harbor_mod.HarborAuthentication | None,
) -> None:
    harbor_mod.write_run_record(
        job.job_dir,
        stage=stage,
        config=config,
        config_identity=config_identity,
        command=job.command,
        executed=executed,
        repeats=job.repeats,
        max_concurrent_trials=args.max_concurrent_trials,
        items=job.record_items,
        cli_version=cli_version,
        authentication=authentication,
        repeats_target=args.repeats,
        sample=getattr(args, "sample", None),
    )


def _deficit_groups(
    planned: list[_PlannedItem], args: argparse.Namespace
) -> dict[int, list[_PlannedItem]]:
    """Items to run, grouped by how many trials each still needs.

    Harbor's ``n_attempts`` is job-wide, so items with different
    deficits cannot share one job; each deficit becomes one job. Without
    ``--force`` an item's deficit is the target minus its pooled valid
    trials; ``--force`` adds ``--repeats`` more to every planned item,
    which is a single group.
    """
    if args.force:
        return {args.repeats: list(planned)} if planned else {}
    groups: dict[int, list[_PlannedItem]] = {}
    for item in planned:
        deficit = args.repeats - item.existing
        if deficit > 0:
            groups.setdefault(deficit, []).append(item)
    return groups


def _execute(
    stage: Stage,
    root: Path,
    jobs_root: Path,
    config: ExperimentConfig,
    config_identity: str,
    planned: list[_PlannedItem],
    args: argparse.Namespace,
    *,
    gurobi_license_file: Path | None,
    authentication: harbor_mod.HarborAuthentication | None,
) -> int:
    groups = _deficit_groups(planned, args)
    to_run = [item for group in groups.values() for item in group]

    if args.dry_run:
        for item in planned:
            deficit = args.repeats if args.force else max(args.repeats - item.existing, 0)
            marker = "run " if deficit > 0 else "skip"
            if args.force:
                # Additive semantics: the target is existing + N, so a
                # "3 of 2" reading must never appear.
                status = "forced"
                counts = f"({item.existing} valid trial(s), adding {args.repeats})"
            else:
                if item.existing == 0:
                    status = "pending"
                elif deficit > 0:
                    status = "partial"
                else:
                    status = "complete"
                counts = f"({item.existing} of {args.repeats} valid trial(s))"
            print(f"{marker} [{status:8}] {item.item_id} {counts}")
        job_note = f" across {len(groups)} job(s) (one per deficit)" if len(groups) > 1 else ""
        print(
            f"would run {len(to_run)} of {len(planned)} item(s){job_note}; "
            f"max concurrent trials: {args.max_concurrent_trials}"
        )
        if gurobi_license_file is not None:
            print(f"Gurobi license: read-only mount from {gurobi_license_file}")
        return 0

    if not to_run:
        print(
            f"nothing to do: {len(planned)} item(s) already at the target of "
            f"{args.repeats} valid trial(s) under config {config.name!r}"
        )
        return 0

    # Executing runs record the version of the harbor binary that will
    # actually be invoked; materialize-only stays offline and records the
    # package metadata version instead.
    cli_version = None if args.materialize_only else harbor_mod.cli_harbor_version()

    launched: list[_LaunchedJob] = []
    # Largest deficits first, so the thinnest items start soonest.
    for deficit in sorted(groups, reverse=True):
        items = groups[deficit]
        job_dir = harbor_mod.create_unique_dir(
            jobs_root, harbor_mod.job_dir_name(config.name, config_identity)
        )
        # Tasks live outside the Harbor job directory: on resume, Harbor
        # deletes any job-dir subdirectory without a per-trial result.json
        # as a stale trial, which would destroy the task inputs
        # (docs/data-conventions.md, "Job directories and run records").
        tasks_dir = root / "tasks" / job_dir.name
        tasks_dir.mkdir(parents=True)

        record_items = [item.materialize(tasks_dir) for item in items]
        job_config = jobs_mod.build_harbor_job_config(
            config=config,
            task_dirs=[tasks_dir / item.task_dir_name for item in record_items],
            job_dir=job_dir,
            repeats=deficit,
            max_concurrent_trials=args.max_concurrent_trials,
            gurobi_license_file=gurobi_license_file,
        )
        job_config_path = jobs_mod.write_harbor_job_config(job_dir, job_config)
        command = harbor_mod.build_harbor_command(job_config_path)
        job = _LaunchedJob(
            job_dir=job_dir, record_items=record_items, repeats=deficit, command=command
        )
        # Written as executed: false and flipped just before this job's
        # launch: in a multi-job invocation an abort can leave later
        # jobs unlaunched, and their records must not claim otherwise.
        _write_job_record(
            job,
            executed=False,
            stage=stage,
            config=config,
            config_identity=config_identity,
            args=args,
            cli_version=cli_version,
            authentication=authentication,
        )
        launched.append(job)
        print(f"job directory: {job_dir}")
        print(f"materialized {len(record_items)} task(s), {deficit} trial(s) per item")

    print(f"max concurrent trials: {args.max_concurrent_trials}")
    if gurobi_license_file is not None:
        print(f"Gurobi license: read-only mount from {gurobi_license_file}")
    flavors = sorted({item.environment_flavor for item in to_run})
    if args.materialize_only:
        for flavor in flavors:
            print(
                f"base image {base_images_mod.base_image_reference(flavor, config.agent)} is built "
                "when aat launches harbor; a manual run must build it first from any "
                "task's environment/base.Dockerfile"
            )
        for job in launched:
            print(f"materialize-only; harbor not invoked. command: {shlex.join(job.command)}")
        return 0
    base_images_mod.ensure_base_images(flavors, config.agent)
    harbor_status = 0
    for index, job in enumerate(launched):
        _write_job_record(
            job,
            executed=True,
            stage=stage,
            config=config,
            config_identity=config_identity,
            args=args,
            cli_version=cli_version,
            authentication=authentication,
        )
        print(f"launching: {shlex.join(job.command)}")
        harbor_status = harbor_mod.invoke_harbor(job.command, authentication)
        if harbor_status != 0:
            remaining = len(launched) - index - 1
            if remaining:
                print(
                    f"harbor exited {harbor_status}; not launching the remaining "
                    f"{remaining} job(s) — re-run the same command to continue"
                )
            break
    existing_by_key = {(item.item_id, item.item_identity): item.existing for item in planned}
    failed = _report_run_summary(stage, launched, existing_by_key, args)
    if harbor_status != 0:
        return harbor_status
    return 1 if failed else 0


def _report_run_summary(
    stage: Stage,
    launched: list[_LaunchedJob],
    existing_by_key: dict[tuple[str, str], int],
    args: argparse.Namespace,
) -> int:
    """Print the requested/succeeded/failed accounting; return the failure count.

    Harbor exits zero when a *job* finishes, even if trials inside it
    failed — which is how a single lost assignment stays invisible. This
    summary counts every requested item across the invocation's jobs
    (one per deficit group), names each item that did not succeed with
    the exact scoped rerun command, and the caller turns a nonzero
    failure count into a nonzero exit status. An item succeeds when at
    least one valid trial exists for it — pooled prior trials included —
    so failed items are exactly the not-done ones.

    Succeeding is not the whole request: an item can end below its
    target trial count. Such items are named with their pooled count and
    counted into the returned failure total — the run did not deliver
    what was asked. Because ``--repeats`` is a target, a plain re-run of
    the same command launches exactly the missing trials; only
    ``--force`` runs (which add rather than ensure) top up differently.
    """
    check = harbor_mod.is_graded_trial if stage == "grade" else harbor_mod.is_verified_trial
    failed: list[harbor_mod.RunRecordItem] = []
    incomplete: list[tuple[harbor_mod.RunRecordItem, int, int]] = []
    n_requested = 0
    for job in launched:
        counts = harbor_mod.done_trial_counts(job.job_dir, check)
        for item in job.record_items:
            n_requested += 1
            new_valid = counts.get(item.task_dir_name, 0)
            existing = existing_by_key.get((item.item_id, item.item_identity), 0)
            total = existing + new_valid
            target = existing + job.repeats if args.force else args.repeats
            if total == 0:
                failed.append(item)
            elif total < target:
                incomplete.append((item, total, target))
    succeeded_label = "graded" if stage == "grade" else "verified"
    print(
        f"run summary: {n_requested} item(s) requested, "
        f"{n_requested - len(failed)} {succeeded_label}, {len(failed)} failed"
    )
    for item in failed:
        print(f"  failed: {item.course_id}/{item.assignment_id} ({item.item_id})")
    if failed:
        # The rerun must reproduce the invocation's selection and judge
        # flags: without --context-from/--gradings a judge config
        # refuses to run at all, and without --sample a target-count
        # rerun would launch the deficit for every unsampled student.
        from_solve = getattr(args, "from_solve", None)
        sample = getattr(args, "sample", None)
        context_from = getattr(args, "context_from", None)
        for course_id, assignment_id in sorted(
            {(item.course_id, item.assignment_id) for item in failed}
        ):
            command = ["aat", "solve" if stage == "solve" else "grade", "--config", args.config]
            if from_solve:
                command += ["--from-solve", from_solve]
            command += ["--course", course_id, "--assignment", assignment_id]
            if sample is not None:
                command += ["--sample", str(sample)]
            if context_from:
                command += [
                    "--context-from",
                    context_from,
                    "--gradings",
                    str(args.gradings),
                ]
            if args.repeats != 1:
                command += ["--repeats", str(args.repeats)]
            print(f"  rerun: {shlex.join(command)}")
    for item, total, target in incomplete:
        print(
            f"  incomplete: {item.course_id}/{item.assignment_id} ({item.item_id}): "
            f"{total} of {target} valid trial(s)"
        )
    if incomplete:
        if args.force:
            print(
                "  note: --force adds trials rather than ensuring a target; "
                "re-run with --force --repeats <missing> to add the rest"
            )
        else:
            print(
                "  note: --repeats is a target, so re-running the same command "
                "launches exactly the missing trials"
            )
    return len(failed) + len(incomplete)


if __name__ == "__main__":
    sys.exit(main())
