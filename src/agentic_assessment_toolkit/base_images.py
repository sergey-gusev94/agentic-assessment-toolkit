"""Shared per-flavor base images for task environments.

Every task Dockerfile used to embed the full flavor template, so every
trial's ``docker compose build`` re-resolved the template's public base
image tag against its registry — hundreds of registry round-trips per
job, and any transient registry failure or throttle failed trials
mid-run. Instead, the flavor template is built once per launch as a
local image named after the template's content hash, and each task's
Dockerfile starts FROM that local name: per-task builds resolve
entirely locally. Only the launch-time base build touches the registry,
and it retries with backoff before any trial starts.

The template bytes still enter per-item identities exactly as before,
and every task directory keeps a verbatim copy of the template as
``environment/base.Dockerfile``, so the image any task ran on can be
rebuilt from the task directory alone.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Iterable

from . import config
from .hashing import sha256_file

# One initial build attempt, then one retry per delay.
BUILD_RETRY_DELAYS_SEC = (10.0, 60.0)


class BaseImageError(Exception):
    """A required base image is missing and cannot be built."""


def base_image_reference(flavor: str) -> str:
    """The local image name for a flavor's template, pinned by content.

    The tag is the template's content hash, so a template edit yields a
    fresh name and a stale image can never be reused. The name exists in
    no registry namespace, so builds FROM it never contact one.
    """
    digest = sha256_file(config.environment_path(flavor))
    return f"aat-env-{flavor}:{digest[:16]}"


def ensure_base_images(flavors: Iterable[str]) -> None:
    """Build each flavor's base image unless it already exists locally.

    Called once per launch, before Harbor: a base image that cannot be
    built would fail every trial of its flavor, so failing here — after
    retries — is strictly better than starting the job.
    """
    for flavor in sorted(set(flavors)):
        reference = base_image_reference(flavor)
        try:
            if _image_exists(reference):
                continue
            _build_base_image(flavor, reference)
        except OSError as error:
            raise BaseImageError(
                f"cannot run docker to prepare base image {reference}: {error}"
            ) from error


def _image_exists(reference: str) -> bool:
    completed = subprocess.run(  # noqa: S603
        ["docker", "image", "inspect", reference], capture_output=True, check=False
    )
    return completed.returncode == 0


def _build_base_image(flavor: str, reference: str) -> None:
    template = config.environment_path(flavor)
    attempts = len(BUILD_RETRY_DELAYS_SEC) + 1
    print(f"building base image {reference} (environment flavor {flavor!r})")
    for attempt in range(1, attempts + 1):
        # The build context is an empty directory: templates never copy
        # from context — task inputs are layered on by each task's own
        # Dockerfile.
        with tempfile.TemporaryDirectory() as context:
            completed = subprocess.run(  # noqa: S603
                ["docker", "build", "--tag", reference, "--file", str(template), context],
                check=False,
            )
        if completed.returncode == 0:
            return
        if attempt < attempts:
            delay = BUILD_RETRY_DELAYS_SEC[attempt - 1]
            print(
                f"base image build failed (attempt {attempt} of {attempts}); "
                f"retrying in {delay:.0f} s"
            )
            time.sleep(delay)
    raise BaseImageError(
        f"base image {reference} failed to build after {attempts} attempts; "
        "no trial can run without it"
    )
