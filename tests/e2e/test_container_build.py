"""Container build smoke test.

We don't run the full container with mock devices (that's a separate
integration responsibility); we just confirm:

  1. ``Containerfile`` syntax is valid for the runtime in use.
  2. The build stage that pulls Ubuntu, installs apt deps, clones the
     voltcyclone-fpga submodule, and runs ``build_vfio_constants.sh``
     completes successfully on this architecture.

The real-world ``make container`` and CI release path do the runtime
stage too; we stop at ``--target build`` here because the runtime stage
adds another ~3 minutes for a smoke test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.requires_container_runtime,
    pytest.mark.slow,
]


def test_containerfile_build_stage(
    container_runtime: str, repo_root: Path, tmp_path: Path
) -> None:
    """``podman/docker build --target build`` should succeed."""
    image_tag = "pcileechfwgenerator:e2e-test"

    proc = subprocess.run(
        [
            container_runtime,
            "build",
            "--target",
            "build",
            "--file",
            "Containerfile",
            "--tag",
            image_tag,
            ".",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=900,  # cold builds with Ubuntu + apt + git clone can take a while
    )

    if proc.returncode != 0:
        pytest.fail(
            f"container build (--target build) failed with exit "
            f"{proc.returncode}\n"
            f"--- stdout (tail) ---\n{proc.stdout[-2000:]}\n"
            f"--- stderr (tail) ---\n{proc.stderr[-2000:]}"
        )

    # Best-effort cleanup; don't fail the test if it doesn't work.
    subprocess.run(
        [container_runtime, "image", "rm", image_tag],
        capture_output=True,
        timeout=30,
        check=False,
    )
