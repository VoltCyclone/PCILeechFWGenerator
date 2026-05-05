"""End-to-end smoke regression for board source generation (issue #593, T5).

Drives ``FileManager.copy_pcileech_sources`` and the donor patcher against
the real upstream submodule for the two boards reported in #593:

- ``75t`` (EnigmaX1): build succeeds; FIFO carries donor IDs and not Xilinx
  defaults.
- ``pcileech_75t484_x1`` (CaptainDMA/75t484_x1): the staged FIFO is rewritten
  with donor IDs *and* the cfg-id assigns are commented so synthesis no
  longer fails on undeclared interface fields.

These are the canary for future submodule drift — if upstream changes the
RW reset block or the IfPCIeFifoCore interface again, these tests fail
before users hit a multi-minute Vivado synthesis error.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


_REPO_ROOT = project_root
_SUBMODULE = _REPO_ROOT / "lib" / "voltcyclone-fpga"


pytestmark = pytest.mark.skipif(
    not _SUBMODULE.is_dir() or not (_SUBMODULE / "EnigmaX1").is_dir(),
    reason="voltcyclone-fpga submodule not initialized",
)


@pytest.fixture
def staged_board(tmp_path):
    """Copy a board's source files using the production FileManager pipeline."""
    from pcileechfwgenerator.file_management.file_manager import FileManager

    def _stage(board_name: str) -> Path:
        fm = FileManager(output_dir=tmp_path)
        fm.create_pcileech_structure()
        fm.copy_pcileech_sources(board_name)
        return tmp_path

    return _stage


def _intel_donor():
    from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import DonorIDs

    return DonorIDs(
        vendor_id=0x8086,
        device_id=0x1533,
        subsystem_vendor_id=0x8086,
        subsystem_id=0x0001,
        revision_id=0x03,
    )


def _apply_patch(staging_root: Path):
    from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import (
        apply_fifo_donor_patch,
    )

    return apply_fifo_donor_patch(staging_root / "src", _intel_donor())


# ---------------------------------------------------------------------------
# 75t (EnigmaX1) — build path users hit when picking the legacy board.
# ---------------------------------------------------------------------------


class TestBoard75t:
    def test_fifo_carries_donor_ids_after_patch(self, staged_board):
        staging_root = staged_board("75t")
        result = _apply_patch(staging_root)

        assert result["processed"] is True
        assert result["changed"] is True
        # EnigmaX1 has no cfg-id assigns to begin with.
        assert result["cfg_assigns_commented"] is False

        fifo = (staging_root / "src" / "pcileech_fifo.sv").read_text()
        assert "rw[143:128] <= 16'h8086;" in fifo  # subsys vendor
        assert "rw[175:160] <= 16'h8086;" in fifo  # vendor
        assert "rw[191:176] <= 16'h1533;" in fifo  # device
        assert "rw[199:192] <= 8'h03;" in fifo     # rev
        # And that the Xilinx defaults are gone in the patched lines.
        for default_line in (
            "rw[143:128] <= 16'h10EE",
            "rw[175:160] <= 16'h10EE",
            "rw[191:176] <= 16'h0666",
        ):
            assert default_line not in fifo, (
                f"Xilinx default still present after patch: {default_line!r}"
            )

    def test_pcie_core_config_initializer_uses_donor(self, staged_board):
        staging_root = staged_board("75t")
        _apply_patch(staging_root)

        fifo = (staging_root / "src" / "pcileech_fifo.sv").read_text()
        # Trailing 5 fields of the packed initializer become donor IDs.
        assert (
            "8'h03, 16'h1533, 16'h8086, 16'h0001, 16'h8086"
            in fifo
        )


# ---------------------------------------------------------------------------
# pcileech_75t484_x1 — the synthesis-broken board reported in #593.
# ---------------------------------------------------------------------------


class TestBoardPcileech75t484x1:
    def test_fifo_carries_donor_ids_after_patch(self, staged_board):
        staging_root = staged_board("pcileech_75t484_x1")
        result = _apply_patch(staging_root)

        assert result["processed"] is True
        assert result["changed"] is True
        # CaptainDMA/75t484_x1 ships fifo writes to interface fields the
        # header doesn't declare — patcher must comment them.
        assert result["cfg_assigns_commented"] is True

        fifo = (staging_root / "src" / "pcileech_fifo.sv").read_text()
        assert "rw[175:160] <= 16'h8086;" in fifo

    def test_undefined_cfg_id_assigns_commented_out(self, staged_board):
        staging_root = staged_board("pcileech_75t484_x1")
        _apply_patch(staging_root)

        fifo = (staging_root / "src" / "pcileech_fifo.sv").read_text()
        for field in (
            "pcie_cfg_subsys_vend_id",
            "pcie_cfg_subsys_id",
            "pcie_cfg_vend_id",
            "pcie_cfg_dev_id",
            "pcie_cfg_rev_id",
        ):
            for line in fifo.splitlines():
                if f"dpcie.{field}" in line and "assign" in line:
                    assert line.lstrip().startswith("//"), (
                        f"line for {field} should be commented after patch: "
                        f"{line!r}"
                    )

        # Sanity: pcie_rst_core / pcie_rst_subsys assigns must remain active.
        rst_lines = [
            line
            for line in fifo.splitlines()
            if "dpcie.pcie_rst_core" in line and "assign" in line
        ]
        assert rst_lines and not rst_lines[0].lstrip().startswith("//"), (
            "pcie_rst_core assign was incorrectly commented out"
        )

    def test_balanced_blocks_after_patch(self, staged_board):
        staging_root = staged_board("pcileech_75t484_x1")
        _apply_patch(staging_root)

        fifo = (staging_root / "src" / "pcileech_fifo.sv").read_text()
        # The patcher does textual rewrites; parens/brackets must remain
        # exactly balanced (a cheap canary against truncating mid-expression).
        assert fifo.count("(") == fifo.count(")")
        assert fifo.count("[") == fifo.count("]")
