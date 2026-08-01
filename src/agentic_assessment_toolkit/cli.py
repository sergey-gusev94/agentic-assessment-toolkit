"""The ``aat`` command line: solve and grading runs over Harbor, plus reports.

Three commands (docs/design.md, "CLI design"): ``aat solve`` and ``aat
grade`` materialize tasks and launch Harbor; ``aat report`` is
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
import os
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import check_course as check_course_mod
from . import config as config_mod
from . import data_root as data_root_mod
from . import harbor as harbor_mod
from . import hashing
from . import intake as intake_mod
from . import jobs as jobs_mod
from . import metrics as metrics_mod
from . import report as report_mod
from . import rubric as rubric_mod
from .config import ConfigError, ExperimentConfig, Stage
from .course import CourseError
from .data_root import DataRootError
from .materialize._common import MaterializeError
from .materialize.grading import materialize_grading_task
from .materialize.solve import materialize_solve_task

SOLVE_JOBS_DIRNAME = "solving"
GRADING_JOBS_DIRNAME = "grading"


class CliError(Exception):
    """A usage or selection error surfaced to the user."""


@dataclass(frozen=True)
class _PlannedItem:
    item_id: str
    item_identity: str
    done: bool
    materialize: Callable[[Path], harbor_mod.RunRecordItem]


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _run(args)
    except (CliError, ConfigError, CourseError, DataRootError, MaterializeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-root", metavar="PATH", help="explicit data root (else AAT_DATA_DIR)"
    )
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
        help="Harbor attempts per item (sampling depth, not identity)",
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
    intake.add_argument(
        "--data-root", metavar="PATH", help="explicit data root (else AAT_DATA_DIR)"
    )
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

    check = subparsers.add_parser(
        "check-course",
        help="report a course tree's contract violations, gaps, and intake notes (read-only)",
    )
    check.add_argument("--course", required=True, metavar="ID")
    check.add_argument("--data-root", metavar="PATH", help="explicit data root (else AAT_DATA_DIR)")

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
    report.add_argument(
        "--data-root", metavar="PATH", help="explicit data root (else AAT_DATA_DIR)"
    )
    return parser


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _run(args: argparse.Namespace) -> int:
    if args.command == "report":
        return _run_report(args)
    if args.command == "check-course":
        return _run_check_course(args)
    if args.command == "intake":
        return _run_intake(args)
    stage: Stage = "solve" if args.command == "solve" else "grade"
    root = data_root_mod.resolve_data_root(args.data_root)
    config = config_mod.load_config(_config_path(args.config))
    if config.stage != stage:
        raise CliError(
            f"config {config.name!r} has stage {config.stage!r}; `aat {args.command}` needs a {stage!r} config"
        )
    config_identity = config_mod.config_identity(config)
    jobs_root = root / (SOLVE_JOBS_DIRNAME if stage == "solve" else GRADING_JOBS_DIRNAME)
    done = harbor_mod.done_items(jobs_root, stage)
    if stage == "solve":
        planned = _plan_solve(root, config, config_identity, done, args)
    else:
        planned = _plan_grade(root, config, config_identity, done, args)
    return _execute(stage, root, jobs_root, config, config_identity, planned, args)


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
    courses = intake_mod.list_raw_courses(root)
    if args.course:
        selected = [course for course in courses if course.course_id == args.course]
        if not selected:
            raise CliError(f"no raw dump at {root / 'raw' / args.course}")
    elif args.all_items:
        selected = courses
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
        print(f"nothing to do: {len(selected)} course dump(s) already processed")
        return 0

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


def _plan_solve(
    root: Path,
    config: ExperimentConfig,
    config_identity: str,
    done: set[tuple[str, str]],
    args: argparse.Namespace,
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
            template_bytes = config_mod.environment_path(assignment.environment_flavor).read_bytes()
            identity = config_mod.item_identity(config_identity, template_bytes)
            planned.append(
                _PlannedItem(
                    item_id=assignment.item_id,
                    item_identity=identity,
                    done=(assignment.item_id, identity) in done,
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
    done: set[tuple[str, str]],
    args: argparse.Namespace,
) -> list[_PlannedItem]:
    sources = _grade_sources(root, args)
    template_bytes = config_mod.environment_path(config_mod.GRADING_FLAVOR).read_bytes()
    rubric_name = config.rubric_name
    if rubric_name is None:  # load_config defaults grading configs to "default"
        raise CliError(f"config {config.name!r} names no rubric")

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
                "first (the manual procedure in docs/data-conventions.md)"
            )
        # Parse at plan time so a bad rubric fails before any job
        # directory is created; the materializer parses it again.
        try:
            rubric_mod.parse_rubric_file(rubric)
        except rubric_mod.RubricError as error:
            raise CliError(str(error)) from error
        identity = config_mod.item_identity(
            config_identity,
            template_bytes,
            rubric.read_bytes(),
            assignment_hashes[assignment],
        )
        planned.append(
            _PlannedItem(
                item_id=source.item_id,
                item_identity=identity,
                done=(source.item_id, identity) in done,
                materialize=_grade_materializer(
                    source, assignment, reference, rubric, config, identity
                ),
            )
        )
    return planned


def _grade_materializer(
    source: _GradeSource,
    assignment: Path,
    reference: Path,
    rubric: Path,
    config: ExperimentConfig,
    identity: str,
) -> Callable[[Path], harbor_mod.RunRecordItem]:
    def materialize(tasks_dir: Path) -> harbor_mod.RunRecordItem:
        task = materialize_grading_task(
            assignment_dir=assignment,
            submission_dir=source.submission_dir,
            reference_solution_dir=reference,
            rubric_path=rubric,
            item_id=source.item_id,
            name_parts=source.name_parts,
            prompt_name=config.prompt_name,
            tasks_dir=tasks_dir,
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
        submissions = harbor_mod.verified_solve_submissions(
            root / SOLVE_JOBS_DIRNAME,
            args.from_solve,
            course_id=args.course,
            assignment_id=args.assignment,
        )
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
            for sub in submissions
        ]

    if args.submissions:
        return _student_sources_from_path(root, args.submissions)

    course_ids = [args.course] if args.course else _submission_courses(root)
    return _student_sources(root, course_ids, assignment_id=args.assignment, student_id=None)


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


def _execute(
    stage: Stage,
    root: Path,
    jobs_root: Path,
    config: ExperimentConfig,
    config_identity: str,
    planned: list[_PlannedItem],
    args: argparse.Namespace,
) -> int:
    to_run = planned if args.force else [item for item in planned if not item.done]

    if args.dry_run:
        for item in planned:
            status = "done" if item.done else "pending"
            marker = "run " if (args.force or not item.done) else "skip"
            print(f"{marker} [{status:7}] {item.item_id}")
        print(
            f"would run {len(to_run)} of {len(planned)} item(s); "
            f"max concurrent trials: {args.max_concurrent_trials}"
        )
        return 0

    if not to_run:
        print(f"nothing to do: {len(planned)} item(s) already done under config {config.name!r}")
        return 0

    job_dir = harbor_mod.create_unique_dir(
        jobs_root, harbor_mod.job_dir_name(config.name, config_identity)
    )
    # Tasks live outside the Harbor job directory: on resume, Harbor
    # deletes any job-dir subdirectory without a per-trial result.json
    # as a stale trial, which would destroy the task inputs
    # (docs/data-conventions.md, "Job directories and run records").
    tasks_dir = root / "tasks" / job_dir.name
    tasks_dir.mkdir(parents=True)

    record_items = [item.materialize(tasks_dir) for item in to_run]
    job_config = jobs_mod.build_harbor_job_config(
        config=config,
        task_dirs=[tasks_dir / item.task_dir_name for item in record_items],
        job_dir=job_dir,
        repeats=args.repeats,
        max_concurrent_trials=args.max_concurrent_trials,
    )
    job_config_path = jobs_mod.write_harbor_job_config(job_dir, job_config)
    command = harbor_mod.build_harbor_command(job_config_path)
    # Executing runs record the version of the harbor binary that will
    # actually be invoked; materialize-only stays offline and records the
    # package metadata version instead.
    cli_version = None if args.materialize_only else harbor_mod.cli_harbor_version()
    harbor_mod.write_run_record(
        job_dir,
        stage=stage,
        config=config,
        config_identity=config_identity,
        command=command,
        executed=not args.materialize_only,
        repeats=args.repeats,
        max_concurrent_trials=args.max_concurrent_trials,
        items=record_items,
        cli_version=cli_version,
    )

    print(f"job directory: {job_dir}")
    print(f"materialized {len(record_items)} task(s)")
    print(f"max concurrent trials: {args.max_concurrent_trials}")
    if args.materialize_only:
        print(f"materialize-only; harbor not invoked. command: {shlex.join(command)}")
        return 0
    print(f"launching: {shlex.join(command)}")
    return harbor_mod.invoke_harbor(command)


if __name__ == "__main__":
    sys.exit(main())
