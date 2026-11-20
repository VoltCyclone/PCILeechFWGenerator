"""The startup qualification is bound to the board source the generator ships.

Stale pins pointing at an older board make every requester build of a
donor with ABCD ranges and Disable Supported fail read-back. A board
bump that changes a pinned file must fail here, not in a user's Vivado run.
"""

import hashlib

import pytest
from pcileechfwgenerator.device_clone.requester_tracking import (
    QUALIFIED_STARTUP_SOURCE_SHA256,
)
from pcileechfwgenerator.file_management.repo_manager import RepoManager
from pcileechfwgenerator.vivado_handling.requester_plan import REQUESTER_SOURCE
from pcileechfwgenerator.vivado_handling.requester_qualification import (
    qualify_staged_startup,
)

BOARD = "pcileech_100t484_x1"

try:
    BOARD_SRC = (
        RepoManager.get_board_path(BOARD, repo_root=RepoManager.ensure_repo()) / "src"
    )
except Exception:  # pragma: no cover - submodule missing
    BOARD_SRC = None

pytestmark = pytest.mark.skipif(BOARD_SRC is None, reason="needs voltcyclone-fpga")


@pytest.mark.parametrize("name", sorted(QUALIFIED_STARTUP_SOURCE_SHA256))
def test_pinned_source_matches_shipped_board(name):
    digest = hashlib.sha256((BOARD_SRC / name).read_bytes()).hexdigest()
    assert (
        digest == QUALIFIED_STARTUP_SOURCE_SHA256[name]
    ), f"{name} changed on the board: recheck the DRP startup wiring and re-pin"


def test_shipped_board_is_qualified(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    names = {REQUESTER_SOURCE, *QUALIFIED_STARTUP_SOURCE_SHA256}
    for name in names:
        (src / name).write_bytes((BOARD_SRC / name).read_bytes())
    qualification = qualify_staged_startup(tmp_path, sources=names, board=BOARD)
    assert qualification is not None
