"""Harbor command construction, invocation, run records, and job-output reading.

The toolkit invokes Harbor only through its CLI; reading Harbor's
per-trial ``result.json`` (the same file its viewer consumes) is the one
deliberate coupling to Harbor's on-disk output format (docs/design.md,
"Run records and idempotence").
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from . import __version__
from .config import CLAUDE_CODE_AGENT, CODEX_AGENT, ExperimentConfig, Stage
from .grading_schema import JUSTIFICATION_FILENAME, RESULT_FILENAME
from .hashing import sha256_bytes

RUN_RECORD_FILENAME = "aat-run.json"
# Version 3 adds the selection sample, the target-repeats accounting
# (`repeats` is what the job's Harbor config ran; `repeats_target` is
# the target count the invocation ensured), and the final-judge lineage
# on items (context config, prior grading trials).
RUN_RECORD_SCHEMA_VERSION = 3

CODEX_AUTH_JSON_PATH_ENV = "CODEX_AUTH_JSON_PATH"
CODEX_FORCE_AUTH_JSON_ENV = "CODEX_FORCE_AUTH_JSON"
CODEX_HOME_ENV = "CODEX_HOME"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

# Host variables Harbor's Codex adapter reads on its own, removed on both
# Codex credential paths for the same reason as the Claude list below.
# `OPENAI_BASE_URL` is the whole list: the adapter copies it into the
# container's Codex configuration, so every model call of the run would go
# to that host while the run record still names the cached login. The Codex
# adapter declares no behavior fallbacks, so nothing else needs removing.
CODEX_HOST_OVERRIDE_ENVS = ("OPENAI_BASE_URL",)

CLAUDE_FORCE_OAUTH_ENV = "CLAUDE_FORCE_OAUTH"
CLAUDE_CODE_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
CLAUDE_TOKEN_FILE_ENV = "AAT_CLAUDE_TOKEN_FILE"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"

# The Claude counterpart of the cached Codex login: a file holding the
# subscription token from `claude setup-token`, discovered automatically
# so a launch needs nothing exported. Harbor's Claude adapter reads only
# environment variables, so the file is read here and its contents are
# placed in CLAUDE_CODE_OAUTH_TOKEN for the Harbor subprocess alone.
# ~/.claude/.credentials.json is deliberately not this file: it holds the
# interactive login, whose access token lasts hours and which only the
# `claude` CLI can refresh, so a long run would lose its credential
# mid-flight.
CLAUDE_TOKEN_FILE_RELATIVE = (".claude", "aat-oauth-token")

# Host variables Harbor's Claude Code adapter reads on its own, which
# every managed Claude launch removes on both credential paths. Two
# kinds are here, and both would otherwise let the host redirect a run
# without leaving a trace in the run record or the config identity:
# variables that choose the provider or billing route, and variables the
# adapter falls back to for agent behavior. Behavior belongs in a
# config's `agent_args`, which is recorded in the run record and enters
# the config identity.
CLAUDE_HOST_OVERRIDE_ENVS = (
    # Redirects the API calls to another host and, because the adapter
    # then keeps the provider-prefixed model name, asks that host for
    # "anthropic/claude-opus-5" — a name the official API rejects.
    "ANTHROPIC_BASE_URL",
    # Either one alone flips the adapter into Bedrock mode: a third
    # billing route, taken while the run record still says the method was
    # the subscription token. Stripping the two is enough, because every
    # other AWS passthrough is gated on Bedrock mode being on.
    "CLAUDE_CODE_USE_BEDROCK",
    "AWS_BEARER_TOKEN_BEDROCK",
    # Only read when the config names no model, which ours always do;
    # removed anyway so no host value can decide what ran.
    "ANTHROPIC_MODEL",
    # Behavior fallbacks. CLAUDE_CODE_EFFORT_LEVEL is the sharpest: the
    # config schema allows omitting reasoning_effort, and then the host
    # value would silently set the effort of every trial.
    "CLAUDE_CODE_MAX_TURNS",
    "CLAUDE_CODE_EFFORT_LEVEL",
    "MAX_THINKING_TOKENS",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
)
# ANTHROPIC_DEFAULT_SONNET_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL,
# ANTHROPIC_DEFAULT_HAIKU_MODEL, and CLAUDE_CODE_SUBAGENT_MODEL are
# deliberately absent: the adapter only ever writes them (from the model
# it already resolved) and never reads the host's values, so removing
# them would suggest a protection that is not needed.

_TRUE_ENV_VALUES = frozenset({"true", "1", "yes"})
_FALSE_ENV_VALUES = frozenset({"false", "0", "no"})


class HarborAuthenticationError(Exception):
    """A live Harbor run cannot authenticate its configured agent."""


@dataclass(frozen=True)
class HarborAuthentication:
    """Resolved, non-interactive authentication for one Harbor launch.

    ``environment_changes`` may contain credentials and is deliberately
    excluded from the representation and run record. Only ``method`` and
    ``source`` are durable provenance.
    """

    method: str
    source: str
    description: str
    environment_changes: Mapping[str, str | None] = field(repr=False, compare=False)

    def provenance(self) -> dict[str, str]:
        return {"method": self.method, "source": self.source}


def harbor_version() -> str:
    """The installed Harbor package version.

    Read from package metadata so that offline paths (tests,
    ``--materialize-only``) never invoke Harbor.
    """
    return metadata.version("harbor")


def cli_harbor_version() -> str | None:
    """What ``harbor --version`` actually prints, from the binary on PATH.

    The PATH binary is what ``invoke_harbor`` runs, and it can belong to
    a different environment than this package's ``harbor`` dependency —
    so executing runs record this version, not the package metadata.
    ``None`` when the binary is missing or misbehaves.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            ["harbor", "--version"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def build_harbor_command(job_config_path: Path) -> list[str]:
    # --yes: harbor prompts on stdin when task env templates reference
    # host variables; a wrapper must never block on that prompt.
    return ["harbor", "run", "-c", str(job_config_path), "--yes"]


def resolve_harbor_authentication(
    agent: str,
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> HarborAuthentication | None:
    """Resolve authentication before a live Harbor job is materialized.

    Both managed agents prefer subscription-backed execution (design
    decision 3) and fall back to an API key only when explicitly selected
    or when no subscription credential exists. Codex runs prefer the same
    file-based cached login used by the local Codex CLI; Claude Code runs
    prefer the subscription token from ``claude setup-token``, taken from
    the environment or from the token file this toolkit discovers under
    the user's home. Explicit Harbor overrides retain precedence in both
    cases. Other agents retain Harbor's own authentication behavior.

    Bedrock-backed Claude is deliberately out of scope: it is a third
    billing route this project does not use, so rather than manage it,
    Claude launches remove the variables that would select it (see
    ``CLAUDE_HOST_OVERRIDE_ENVS``) — a run must not take a route its own
    record does not name.
    """
    environment = os.environ if environ is None else environ
    if agent == CODEX_AGENT:
        return _resolve_codex_authentication(environment, Path.home() if home is None else home)
    if agent == CLAUDE_CODE_AGENT:
        return _resolve_claude_code_authentication(
            environment, Path.home() if home is None else home
        )
    return None


def _resolve_codex_authentication(
    environment: Mapping[str, str], user_home: Path
) -> HarborAuthentication:
    if CODEX_AUTH_JSON_PATH_ENV in environment:
        raw_path = environment[CODEX_AUTH_JSON_PATH_ENV]
        if not raw_path.strip():
            raise HarborAuthenticationError(f"{CODEX_AUTH_JSON_PATH_ENV} is set but empty")
        auth_path = Path(raw_path).expanduser().resolve()
        _validate_codex_auth_file(auth_path, source=CODEX_AUTH_JSON_PATH_ENV)
        return _auth_file_authentication(auth_path, source=CODEX_AUTH_JSON_PATH_ENV)

    if CODEX_FORCE_AUTH_JSON_ENV in environment:
        force_file = _parse_env_bool(
            environment[CODEX_FORCE_AUTH_JSON_ENV], name=CODEX_FORCE_AUTH_JSON_ENV
        )
        if force_file:
            auth_path = user_home / ".codex" / "auth.json"
            _validate_codex_auth_file(auth_path, source=CODEX_FORCE_AUTH_JSON_ENV)
            return _auth_file_authentication(auth_path, source=CODEX_FORCE_AUTH_JSON_ENV)
        return _api_key_authentication(environment, explicitly_selected=True)

    codex_home = environment.get(CODEX_HOME_ENV)
    auth_path = (
        Path(codex_home).expanduser() / "auth.json"
        if codex_home and codex_home.strip()
        else user_home / ".codex" / "auth.json"
    ).resolve()
    try:
        auth_path.stat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise HarborAuthenticationError(
            f"cannot inspect the automatic Codex auth file: {auth_path}"
        ) from error
    else:
        _validate_codex_auth_file(auth_path, source="automatic-cache")
        return _auth_file_authentication(auth_path, source="automatic-cache")

    api_key = environment.get(OPENAI_API_KEY_ENV)
    if api_key is not None:
        return _api_key_authentication(environment, explicitly_selected=False)

    raise HarborAuthenticationError(
        "no file-based Codex login or OpenAI API key is available; run 'codex login' "
        f"so {auth_path} exists, or set {OPENAI_API_KEY_ENV}. If Codex stores its login "
        'in the OS keyring, set cli_auth_credentials_store = "file" in Codex config '
        "and log in again"
    )


def _parse_env_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    raise HarborAuthenticationError(
        f"invalid {name} value {value!r}; expected true/false/1/0/yes/no"
    )


def _validate_codex_auth_file(path: Path, *, source: str) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HarborAuthenticationError(
            f"{source} selected a missing Codex auth file: {path}"
        ) from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarborAuthenticationError(
            f"{source} selected an unreadable or invalid Codex auth file: {path}"
        ) from error
    if not isinstance(data, dict) or not data:
        raise HarborAuthenticationError(
            f"{source} selected an empty or invalid Codex auth file: {path}"
        )


def _auth_file_authentication(path: Path, *, source: str) -> HarborAuthentication:
    return HarborAuthentication(
        method="codex-auth-json",
        source=source,
        description=(
            "cached Codex login" if source == "automatic-cache" else "explicit Codex auth file"
        ),
        environment_changes={
            CODEX_AUTH_JSON_PATH_ENV: str(path),
            CODEX_FORCE_AUTH_JSON_ENV: None,
            OPENAI_API_KEY_ENV: None,
            **dict.fromkeys(CODEX_HOST_OVERRIDE_ENVS),
        },
    )


def _api_key_authentication(
    environment: Mapping[str, str], *, explicitly_selected: bool
) -> HarborAuthentication:
    api_key = environment.get(OPENAI_API_KEY_ENV)
    if api_key is None or not api_key.strip():
        selection = (
            f" because {CODEX_FORCE_AUTH_JSON_ENV}=false selected API-key authentication"
            if explicitly_selected
            else ""
        )
        raise HarborAuthenticationError(f"{OPENAI_API_KEY_ENV} is missing or empty{selection}")
    return HarborAuthentication(
        method="openai-api-key",
        source=(CODEX_FORCE_AUTH_JSON_ENV if explicitly_selected else OPENAI_API_KEY_ENV),
        description="OpenAI API key",
        environment_changes={
            CODEX_AUTH_JSON_PATH_ENV: None,
            CODEX_FORCE_AUTH_JSON_ENV: "0",
            OPENAI_API_KEY_ENV: api_key,
            **dict.fromkeys(CODEX_HOST_OVERRIDE_ENVS),
        },
    )


def _resolve_claude_code_authentication(
    environment: Mapping[str, str], user_home: Path
) -> HarborAuthentication:
    """Select the Claude Code credential, subscription token first.

    Harbor's Claude Code adapter prefers ``ANTHROPIC_API_KEY`` over the
    subscription token unless ``CLAUDE_FORCE_OAUTH`` is truthy, so a run
    the user believes is subscription-backed would silently bill per
    token whenever a key happens to sit in the shell. Resolving here
    makes the choice explicit and one-way.

    ``ANTHROPIC_AUTH_TOKEN`` is not a credential source here, and neither
    is it left in place (see ``_claude_token_authentication``): the
    adapter passes whatever it selects in ``ANTHROPIC_API_KEY``, so a
    bearer token would travel in the wrong header, and a bearer token is
    only meaningful against the gateway ``ANTHROPIC_BASE_URL`` names —
    which a managed launch removes.

    Precedence on failure mirrors Codex: a candidate that is absent is
    skipped, while the last-resort variable, once present, is selected
    and must be non-empty. ``CLAUDE_FORCE_OAUTH=false`` never reads the
    token file, so a broken token file cannot fail a launch that
    deliberately selected the API key.
    """
    if CLAUDE_FORCE_OAUTH_ENV in environment:
        force_token = _parse_env_bool(
            environment[CLAUDE_FORCE_OAUTH_ENV], name=CLAUDE_FORCE_OAUTH_ENV
        )
        if force_token:
            return _claude_token_authentication(
                _claude_token_selection(environment, user_home),
                user_home,
                explicitly_selected=True,
            )
        return _anthropic_api_key_authentication(environment, explicitly_selected=True)

    selection = _claude_token_selection(environment, user_home)
    if selection is not None:
        return _claude_token_authentication(selection, user_home, explicitly_selected=False)

    if ANTHROPIC_API_KEY_ENV in environment:
        return _anthropic_api_key_authentication(environment, explicitly_selected=False)

    raise HarborAuthenticationError(
        "no Claude Code subscription token or Anthropic API key is available; run "
        f"'claude setup-token > {_claude_default_token_file(user_home)}' "
        f"(or export {CLAUDE_CODE_OAUTH_TOKEN_ENV}), or set {ANTHROPIC_API_KEY_ENV}"
    )


def _claude_default_token_file(user_home: Path) -> Path:
    return user_home.joinpath(*CLAUDE_TOKEN_FILE_RELATIVE)


def _claude_token_selection(
    environment: Mapping[str, str], user_home: Path
) -> tuple[str, str] | None:
    """The subscription token and the source that supplied it, if any.

    The order mirrors Codex: an explicitly named file wins, because
    naming it is a deliberate act; then the environment variable Harbor
    itself reads; then the automatically discovered token file. An
    explicitly named file that cannot be read is an error rather than a
    fallthrough, exactly as ``CODEX_AUTH_JSON_PATH`` is.
    """
    if CLAUDE_TOKEN_FILE_ENV in environment:
        raw_path = environment[CLAUDE_TOKEN_FILE_ENV]
        if not raw_path.strip():
            raise HarborAuthenticationError(f"{CLAUDE_TOKEN_FILE_ENV} is set but empty")
        path = Path(raw_path).expanduser().resolve()
        return _read_claude_token_file(path, source=CLAUDE_TOKEN_FILE_ENV), CLAUDE_TOKEN_FILE_ENV

    token = environment.get(CLAUDE_CODE_OAUTH_TOKEN_ENV)
    if token is not None and token.strip():
        return token, CLAUDE_CODE_OAUTH_TOKEN_ENV

    path = _claude_default_token_file(user_home)
    try:
        path.stat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise HarborAuthenticationError(
            f"cannot inspect the automatic Claude token file: {path}"
        ) from error
    return _read_claude_token_file(path, source="automatic-token-file"), "automatic-token-file"


def _read_claude_token_file(path: Path, *, source: str) -> str:
    """The one token a token file holds, with surrounding whitespace gone.

    The file is written by redirecting ``claude setup-token``, so it
    normally holds the token and a trailing newline. Anything else — a
    missing file, an empty one, or several whitespace-separated words
    from a session that printed more than the token — is rejected here,
    before a job directory exists, rather than reaching every trial as a
    provider rejection that looks like an agent failure.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise HarborAuthenticationError(
            f"{source} selected a missing Claude token file: {path}"
        ) from error
    except (OSError, UnicodeDecodeError) as error:
        raise HarborAuthenticationError(
            f"{source} selected an unreadable Claude token file: {path}"
        ) from error
    token = content.strip()
    if not token:
        raise HarborAuthenticationError(f"{source} selected an empty Claude token file: {path}")
    if len(token.split()) > 1:
        raise HarborAuthenticationError(
            f"{source} selected a Claude token file holding more than the token: {path}; "
            "it must contain only the output of 'claude setup-token'"
        )
    return token


def _claude_token_authentication(
    selection: tuple[str, str] | None, user_home: Path, *, explicitly_selected: bool
) -> HarborAuthentication:
    if selection is None:
        reason = (
            f" because {CLAUDE_FORCE_OAUTH_ENV}=true selected the subscription token"
            if explicitly_selected
            else ""
        )
        raise HarborAuthenticationError(
            f"{CLAUDE_CODE_OAUTH_TOKEN_ENV} is missing or empty and no token file exists "
            f"at {_claude_default_token_file(user_home)}{reason}; run "
            f"'claude setup-token > {_claude_default_token_file(user_home)}' to obtain one"
        )
    token, token_source = selection
    return HarborAuthentication(
        method="claude-oauth-token",
        source=(CLAUDE_FORCE_OAUTH_ENV if explicitly_selected else token_source),
        description="Claude Code subscription token",
        # CLAUDE_FORCE_OAUTH=1 makes Harbor's adapter use the token, and
        # both Anthropic key variables are dropped from the Harbor
        # subprocess environment entirely: an API key the adapter cannot
        # see is an API key the run cannot silently bill against. The
        # host-override variables go with them, so nothing left in the
        # shell can change the provider, the route, or the agent's
        # behavior.
        environment_changes={
            CLAUDE_FORCE_OAUTH_ENV: "1",
            CLAUDE_CODE_OAUTH_TOKEN_ENV: token,
            ANTHROPIC_API_KEY_ENV: None,
            ANTHROPIC_AUTH_TOKEN_ENV: None,
            **dict.fromkeys(CLAUDE_HOST_OVERRIDE_ENVS),
        },
    )


def _anthropic_api_key_authentication(
    environment: Mapping[str, str], *, explicitly_selected: bool
) -> HarborAuthentication:
    api_key = environment.get(ANTHROPIC_API_KEY_ENV)
    if api_key is None or not api_key.strip():
        selection = (
            f" because {CLAUDE_FORCE_OAUTH_ENV}=false selected API-key authentication"
            if explicitly_selected
            else ""
        )
        raise HarborAuthenticationError(f"{ANTHROPIC_API_KEY_ENV} is missing or empty{selection}")
    return HarborAuthentication(
        method="anthropic-api-key",
        source=(CLAUDE_FORCE_OAUTH_ENV if explicitly_selected else ANTHROPIC_API_KEY_ENV),
        description="Anthropic API key",
        # The selected key is passed through; the subscription token, the
        # unused bearer-token variable, and the host overrides are
        # dropped, so exactly one route with one model reaches Harbor.
        environment_changes={
            CLAUDE_FORCE_OAUTH_ENV: "0",
            CLAUDE_CODE_OAUTH_TOKEN_ENV: None,
            ANTHROPIC_API_KEY_ENV: api_key,
            ANTHROPIC_AUTH_TOKEN_ENV: None,
            **dict.fromkeys(CLAUDE_HOST_OVERRIDE_ENVS),
        },
    )


def harbor_subprocess_env(
    base: Mapping[str, str] | None = None,
    changes: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Subprocess environment for Harbor runs.

    Telemetry is disabled: nothing leaves the machine except calls to
    the model providers (docs/brief.md, constraints).
    """
    environment = dict(os.environ if base is None else base)
    for name, value in (changes or {}).items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    environment["HARBOR_TELEMETRY"] = "0"
    return environment


def invoke_harbor(command: list[str], authentication: HarborAuthentication | None = None) -> int:
    changes = None if authentication is None else authentication.environment_changes
    completed = subprocess.run(  # noqa: S603
        command, env=harbor_subprocess_env(changes=changes), check=False
    )
    return completed.returncode


def utc_stamp(now: datetime | None = None) -> str:
    moment = now if now is not None else datetime.now(UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def job_dir_name(config_name: str, config_identity: str, now: datetime | None = None) -> str:
    return f"{utc_stamp(now)}__{config_name}__{config_identity[:8]}"


def create_unique_dir(parent: Path, base_name: str) -> Path:
    """Create ``parent/base_name``, uniquifying on same-second collisions.

    Job and report directory names are timestamped to the second, and
    their identity lives in their records (``aat-run.json``,
    ``provenance.json``), not in the name — so a ``-N`` suffix is
    harmless. Multi-deficit-group invocations create several jobs in
    one second, so the suffix is routine there, not rare.
    """
    for attempt in range(1, 100):
        name = base_name if attempt == 1 else f"{base_name}-{attempt}"
        target = parent / name
        try:
            target.mkdir(parents=True)
        except FileExistsError:
            continue
        return target
    raise OSError(f"cannot create a fresh directory under {parent}")


@dataclass(frozen=True)
class PriorTrialRef:
    """A grading trial named as a final-judge input (run-record lineage)."""

    job_name: str
    trial_name: str


@dataclass(frozen=True)
class RunRecordItem:
    """One requested item: identity, lineage, and the exact input hashes.

    ``course_id`` and ``assignment_id`` are recorded explicitly so every
    run record is self-describing — for solve-derived grading items the
    ``item_id`` is ``<solve-job>/<trial>`` and carries neither. The
    lineage fields say where a grading item's submission came from; they
    are always serialized, so no consumer ever parses an item id.
    """

    item_id: str
    task_dir_name: str
    item_identity: str
    course_id: str
    assignment_id: str
    input_hashes: dict[str, str]
    submission_source: str | None = None  # "student" | "solve-trial"; None for solve items
    student_id: str | None = None  # student grading items only
    solve_job_name: str | None = None  # solve-derived grading items only
    solve_trial_name: str | None = None
    # Final-judge lineage: the initial grading config whose gradings the
    # judge task presents, and exactly which trials they came from.
    context_config_name: str | None = None
    context_config_identity: str | None = None
    prior_trials: tuple[PriorTrialRef, ...] | None = None


def write_run_record(
    job_dir: Path,
    *,
    stage: Stage,
    config: ExperimentConfig,
    config_identity: str,
    command: list[str],
    executed: bool,
    repeats: int,
    max_concurrent_trials: int,
    items: list[RunRecordItem],
    cli_version: str | None = None,
    authentication: HarborAuthentication | None = None,
    repeats_target: int | None = None,
    sample: int | None = None,
) -> Path:
    record = {
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "created_utc": datetime.now(UTC).isoformat(),
        "toolkit_version": __version__,
        "harbor_version": cli_version if cli_version is not None else harbor_version(),
        "harbor_version_source": "cli" if cli_version is not None else "package-metadata",
        "stage": stage,
        "config": {
            "name": config.name,
            "sha256": sha256_bytes(config.raw_bytes),
            "agent": config.agent,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "prompt": config.prompt_name,
            "rubric": config.rubric_name,
            "agent_args": list(config.agent_args),
            "judge": config.judge,
        },
        "config_identity": config_identity,
        # `repeats` is what this job's Harbor config ran (its n_attempts);
        # `repeats_target` is the target count the invocation ensured,
        # from which this job's deficit was derived. They differ exactly
        # when existing valid trials already covered part of the target.
        "repeats": repeats,
        "repeats_target": repeats_target if repeats_target is not None else repeats,
        # The requested selection sample (--sample), or None when the
        # whole selection ran; the items list is the frame that resulted.
        "sample": sample,
        "max_concurrent_trials": max_concurrent_trials,
        "command": command,
        "executed": executed,
        "authentication": None if authentication is None else authentication.provenance(),
        "items": [asdict(item) for item in items],
    }
    path = job_dir / RUN_RECORD_FILENAME
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_run_record(job_dir: Path) -> dict[str, object] | None:
    path = job_dir / RUN_RECORD_FILENAME
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return record if isinstance(record, dict) else None


def job_dirs(jobs_root: Path) -> list[Path]:
    if not jobs_root.is_dir():
        return []
    return sorted(
        (entry for entry in jobs_root.iterdir() if (entry / RUN_RECORD_FILENAME).is_file()),
        key=lambda entry: entry.name,
    )


def trial_results(job_dir: Path) -> Iterator[tuple[Path, dict[str, object]]]:
    """Yield (trial_dir, result) for every Harbor trial result in a job dir.

    The AAT job directory is the Harbor job directory (flat layout), so
    trials are its immediate subdirectories that hold a ``result.json``;
    the ``tasks/`` directory and Harbor's job-level files have none and
    are skipped naturally.
    """
    if not job_dir.is_dir():
        return
    for trial_dir in sorted(job_dir.iterdir(), key=lambda entry: entry.name):
        result_path = trial_dir / "result.json"
        if not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(result, dict):
            yield trial_dir, result


def is_verified_trial(result: dict[str, object]) -> bool:
    """Verified: the verifier recorded a reward (docs/design.md, denominator policy).

    A non-empty ``verifier_result.rewards`` proves verification ran to
    completion; any pre-verification failure (agent error, timeout,
    verifier crash) leaves it absent. A late exception recorded after a
    reward exists does not un-verify the trial.
    """
    verifier_result = result.get("verifier_result")
    return isinstance(verifier_result, dict) and bool(verifier_result.get("rewards"))


def is_graded_trial(result: dict[str, object]) -> bool:
    """Verified with a *valid* grading result.

    The grading verifier writes ``base_pct`` into the rewards only
    when ``grading_result.json`` passed validation; a contract violation
    emits the bare ``{"reward": 0.0}``. An invalid grading is a failed
    measurement, not a grade, so it never counts as done
    (docs/design.md, "Run records and idempotence").
    """
    if not is_verified_trial(result):
        return False
    verifier_result = result.get("verifier_result")
    if not isinstance(verifier_result, dict):
        return False
    rewards = verifier_result.get("rewards")
    return isinstance(rewards, dict) and "base_pct" in rewards


def done_trial_counts(
    job_dir: Path, check: Callable[[dict[str, object]], bool] = is_verified_trial
) -> dict[str, int]:
    """How many of each task's trials pass ``check`` in one job directory.

    With ``--repeats N`` a task has N trials in its job; the count is
    what tells a fully sampled item apart from one that lost trials to
    failures, which the run summary must report (docs/design.md, "Run
    records and idempotence").
    """
    counts: dict[str, int] = {}
    for _, result in trial_results(job_dir):
        if check(result):
            task_name = result.get("task_name")
            if isinstance(task_name, str):
                counts[task_name] = counts.get(task_name, 0) + 1
    return counts


def verified_task_names(
    job_dir: Path, check: Callable[[dict[str, object]], bool] = is_verified_trial
) -> set[str]:
    return set(done_trial_counts(job_dir, check))


def _record_items(record: dict[str, object]) -> list[dict[str, object]]:
    items = record.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def items_by_task_dir(record: dict[str, object]) -> dict[str, dict[str, object]]:
    """A run record's items indexed by ``task_dir_name``.

    A trial is matched to its item through this name (Harbor records it
    as the trial's ``task_name``), so an item without a string
    ``task_dir_name`` can never claim a trial and is dropped here.
    """
    index: dict[str, dict[str, object]] = {}
    for item in _record_items(record):
        task_dir_name = item.get("task_dir_name")
        if isinstance(task_dir_name, str):
            index[task_dir_name] = item
    return index


def done_trial_totals(jobs_root: Path, stage: Stage) -> dict[tuple[str, str], int]:
    """Done-trial counts per (item_id, item_identity), pooled across jobs.

    The key is the doneness and pooling key of docs/design.md: the
    identity alone is not item-specific (it hashes config + environment
    + rubric bytes), so distinct items routinely share one identity. The
    per-trial check is stage-specific: solve failures (reward 0) are
    countable outcomes and count; grading requires a valid grading
    result. The counts are what target-count ``--repeats`` subtracts
    from: an item's deficit is the target minus its pooled total. Fails
    closed: a job whose recorded stage does not match contributes
    nothing.
    """
    check = is_graded_trial if stage == "grade" else is_verified_trial
    totals: dict[tuple[str, str], int] = {}
    for job_dir in job_dirs(jobs_root):
        record = read_run_record(job_dir)
        if record is None or record.get("stage") != stage:
            continue
        counts = done_trial_counts(job_dir, check)
        for task_dir_name, item in items_by_task_dir(record).items():
            count = counts.get(task_dir_name, 0)
            if not count:
                continue
            item_id = item.get("item_id")
            item_identity = item.get("item_identity")
            if isinstance(item_id, str) and isinstance(item_identity, str):
                key = (item_id, item_identity)
                totals[key] = totals.get(key, 0) + count
    return totals


def done_items(jobs_root: Path, stage: Stage) -> set[tuple[str, str]]:
    """(item_id, item_identity) pairs with at least one done trial."""
    return set(done_trial_totals(jobs_root, stage))


@dataclass(frozen=True)
class SolveSubmission:
    """A verified solve trial's submission artifact, ready for grading."""

    item_id: str
    course_id: str
    assignment_id: str
    solve_job_name: str
    trial_name: str
    directory: Path


@dataclass(frozen=True)
class SkippedSolveTrial:
    """An in-scope solve trial that yields no gradable submission.

    ``reason`` is ``"solve-failed"`` (the trial was never verified, so
    the agent errored, timed out, or the infrastructure failed) or
    ``"empty-submission"`` (verified, but the submission artifact is
    missing or empty — an output-contract failure). A failed solve is
    not done and reruns without ``--force``; an empty submission counts
    as a verified outcome, so producing a new attempt needs ``--force``.
    """

    course_id: str
    assignment_id: str
    solve_job_name: str
    trial_name: str
    reason: str


@dataclass(frozen=True)
class SolveSubmissionSelection:
    """What ``--from-solve`` selected, and what it had to pass over."""

    submissions: list[SolveSubmission]
    skipped: list[SkippedSolveTrial]


def verified_solve_submissions(
    solving_root: Path,
    solve_config_name: str,
    *,
    course_id: str | None = None,
    assignment_id: str | None = None,
) -> SolveSubmissionSelection:
    """Submission artifacts of verified solve trials under a named solve config.

    Each verified trial with a non-empty submission artifact is one
    gradable item. In-scope trials that yield no gradable submission —
    failed solves and empty submission artifacts — are returned as
    ``skipped`` so the caller can report them; grading nonexistent work
    would be worse than skipping, but the skip must never be silent.
    ``--course``/``--assignment`` narrowing excludes trials from scope
    entirely: a trial the user did not ask about is neither selected
    nor reported.
    """
    submissions = []
    skipped = []
    for job_dir in job_dirs(solving_root):
        record = read_run_record(job_dir)
        if record is None or record.get("stage") != "solve":
            continue
        config = record.get("config")
        if not isinstance(config, dict) or config.get("name") != solve_config_name:
            continue
        record_items = items_by_task_dir(record)
        for trial_dir, result in trial_results(job_dir):
            task_name = result.get("task_name")
            item = record_items.get(task_name) if isinstance(task_name, str) else None
            if item is None:
                continue
            item_course_id = item.get("course_id")
            item_assignment_id = item.get("assignment_id")
            if not isinstance(item_course_id, str) or not isinstance(item_assignment_id, str):
                continue
            if course_id is not None and item_course_id != course_id:
                continue
            if assignment_id is not None and item_assignment_id != assignment_id:
                continue
            # Harbor mirrors the artifact's absolute container path under
            # the trial's artifacts/ directory: /app/submission →
            # artifacts/app/submission.
            artifact_dir = trial_dir / "artifacts" / "app" / "submission"
            if not is_verified_trial(result):
                reason = "solve-failed"
            elif not artifact_dir.is_dir() or not any(artifact_dir.rglob("*")):
                reason = "empty-submission"
            else:
                reason = None
            if reason is not None:
                skipped.append(
                    SkippedSolveTrial(
                        course_id=item_course_id,
                        assignment_id=item_assignment_id,
                        solve_job_name=job_dir.name,
                        trial_name=trial_dir.name,
                        reason=reason,
                    )
                )
                continue
            submissions.append(
                SolveSubmission(
                    item_id=f"{job_dir.name}/{trial_dir.name}",
                    course_id=item_course_id,
                    assignment_id=item_assignment_id,
                    solve_job_name=job_dir.name,
                    trial_name=trial_dir.name,
                    directory=artifact_dir,
                )
            )
    return SolveSubmissionSelection(submissions=submissions, skipped=skipped)


@dataclass(frozen=True)
class PriorGrading:
    """One stored valid grading, usable as final-judge input.

    ``result_path`` and ``justification_path`` point at the trial's
    mirrored grading artifacts; both exist — a valid trial whose
    artifacts have since gone missing is excluded (and counted) by
    ``prior_gradings_by_key``, because a judge task cannot present bytes
    that are not on disk.
    """

    job_name: str
    trial_name: str
    result_path: Path
    justification_path: Path


@dataclass(frozen=True)
class PriorGradingPool:
    """Stored valid gradings by pooling key, plus missing-artifact counts."""

    by_key: dict[tuple[str, str], list[PriorGrading]]
    # (item_id, item_identity) -> valid trials whose artifact files are
    # missing on disk; reported so an unusable grading is never silent.
    missing_artifacts: dict[tuple[str, str], int]


def prior_gradings_by_key(grading_root: Path) -> PriorGradingPool:
    """Every stored valid grading, keyed by (item_id, item_identity).

    The key is the pooling key, so a judge item's prior gradings are
    looked up with the *context* config's per-item identity — exactly
    the gradings that pool together under the frozen initial config.
    Lists are sorted by (job_name, trial_name) for deterministic round
    numbering. Fails closed like doneness: a job whose recorded stage is
    not ``grade`` contributes nothing.
    """
    by_key: dict[tuple[str, str], list[PriorGrading]] = {}
    missing: dict[tuple[str, str], int] = {}
    for job_dir in job_dirs(grading_root):
        record = read_run_record(job_dir)
        if record is None or record.get("stage") != "grade":
            continue
        record_items = items_by_task_dir(record)
        for trial_dir, result in trial_results(job_dir):
            if not is_graded_trial(result):
                continue
            task_name = result.get("task_name")
            item = record_items.get(task_name) if isinstance(task_name, str) else None
            if item is None:
                continue
            item_id = item.get("item_id")
            item_identity = item.get("item_identity")
            if not isinstance(item_id, str) or not isinstance(item_identity, str):
                continue
            key = (item_id, item_identity)
            output_dir = trial_dir / "artifacts" / "app" / "grading_output"
            result_path = output_dir / RESULT_FILENAME
            justification_path = output_dir / JUSTIFICATION_FILENAME
            if not result_path.is_file() or not justification_path.is_file():
                missing[key] = missing.get(key, 0) + 1
                continue
            by_key.setdefault(key, []).append(
                PriorGrading(
                    job_name=job_dir.name,
                    trial_name=trial_dir.name,
                    result_path=result_path,
                    justification_path=justification_path,
                )
            )
    for gradings in by_key.values():
        gradings.sort(key=lambda grading: (grading.job_name, grading.trial_name))
    return PriorGradingPool(by_key=by_key, missing_artifacts=missing)
