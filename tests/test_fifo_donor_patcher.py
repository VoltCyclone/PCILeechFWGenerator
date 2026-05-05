"""Tests for FIFO donor-ID patcher (issue #593).

The patcher rewrites donor vendor/device/subsys/rev IDs into the upstream
``pcileech_fifo.sv`` reset block and ``_pcie_core_config`` initializer so the
host-visible IDs reflect the donor instead of Xilinx defaults.
"""
import re
import sys
import textwrap
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import (
    DonorIDs,
    FifoPatchError,
    apply_fifo_donor_patch,
    donor_ids_from_template_context,
    patch_pcileech_fifo,
)


# ---------------------------------------------------------------------------
# Fixture FIFO content
# ---------------------------------------------------------------------------

# Minimal upstream-shaped FIFO block carrying every anchor the patcher targets.
# Includes the EnigmaX1-style assigns (only rst_*) so the assign-comment branch
# is exercised separately by the captain-style fixture below.
ENIGMAX1_FIFO = textwrap.dedent(
    """\
    module pcileech_fifo;
        reg     [79:0]      _pcie_core_config = { 4'hf, 1'b1, 1'b1, 1'b0, 1'b0, 8'h02, 16'h0666, 16'h10EE, 16'h0007, 16'h10EE };

        always @ ( posedge clk )
            if ( rst ) begin
                rw[127:96]  <= 32'h00000000;
                rw[143:128] <= 16'h10EE;                    // +010: CFG_SUBSYS_VEND_ID (NOT IMPLEMENTED)
                rw[159:144] <= 16'h0007;                    // +012: CFG_SUBSYS_ID      (NOT IMPLEMENTED)
                rw[175:160] <= 16'h10EE;                    // +014: CFG_VEND_ID        (NOT IMPLEMENTED)
                rw[191:176] <= 16'h0666;                    // +016: CFG_DEV_ID         (NOT IMPLEMENTED)
                rw[199:192] <= 8'h02;                       // +018: CFG_REV_ID         (NOT IMPLEMENTED)
                rw[200]     <= 1'b1;
            end

        assign dpcie.pcie_rst_core    = _pcie_core_config[72];
        assign dpcie.pcie_rst_subsys  = _pcie_core_config[73];
    endmodule
    """
)

# CaptainDMA/75t484_x1 shape: same reset block + extra cfg-id assigns that the
# upstream IfPCIeFifoCore doesn't declare. The patcher must comment those out
# when given a header that lacks the fields.
CAPTAIN_75T_FIFO = ENIGMAX1_FIFO.replace(
    "    assign dpcie.pcie_rst_core    = _pcie_core_config[72];",
    textwrap.dedent(
        """\
            assign dpcie.pcie_cfg_subsys_vend_id = _pcie_core_config[0+:16];
            assign dpcie.pcie_cfg_subsys_id      = _pcie_core_config[16+:16];
            assign dpcie.pcie_cfg_vend_id        = _pcie_core_config[32+:16];
            assign dpcie.pcie_cfg_dev_id         = _pcie_core_config[48+:16];
            assign dpcie.pcie_cfg_rev_id         = _pcie_core_config[64+:8];
            assign dpcie.pcie_rst_core    = _pcie_core_config[72];"""
    ),
)

# 78-bit packed initializer used by NeTV2/pciescreamer/acorn_ft2232h.
NETV2_FIFO = ENIGMAX1_FIFO.replace(
    "reg     [79:0]      _pcie_core_config = { 4'hf, 1'b1, 1'b1, 1'b0, 1'b0, 8'h02, 16'h0666, 16'h10EE, 16'h0007, 16'h10EE };",
    "reg     [77:0]      _pcie_core_config = { 1'b0, 1'b1, 1'b1, 1'b1, 1'b0, 1'b0, 8'h02, 16'h0666, 16'h10EE, 16'h0007, 16'h10EE };",
)

# Header content used to gate the cfg-id assign-comment branch.
HEADER_WITH_CFG_FIELDS = textwrap.dedent(
    """\
    interface IfPCIeFifoCore;
        wire        pcie_rst_core;
        wire        pcie_rst_subsys;
        wire [15:0] pcie_cfg_subsys_vend_id;
        wire [15:0] pcie_cfg_subsys_id;
        wire [15:0] pcie_cfg_vend_id;
        wire [15:0] pcie_cfg_dev_id;
        wire [7:0]  pcie_cfg_rev_id;
    endinterface
    """
)

HEADER_WITHOUT_CFG_FIELDS = textwrap.dedent(
    """\
    interface IfPCIeFifoCore;
        wire        pcie_rst_core;
        wire        pcie_rst_subsys;
        wire [15:0] drp_di;
    endinterface
    """
)


def _intel_donor() -> DonorIDs:
    """Donor matching an Intel I210 NIC; non-Xilinx in every field."""
    return DonorIDs(
        vendor_id=0x8086,
        device_id=0x1533,
        subsystem_vendor_id=0x8086,
        subsystem_id=0x0001,
        revision_id=0x03,
    )


# ---------------------------------------------------------------------------
# Pure-transform tests: patch_pcileech_fifo
# ---------------------------------------------------------------------------


class TestPatchPcileechFifo:
    """patch_pcileech_fifo rewrites the RW reset block and packed initializer."""

    def test_rewrites_rw_reset_block(self):
        out = patch_pcileech_fifo(ENIGMAX1_FIFO, _intel_donor())

        assert "rw[143:128] <= 16'h8086;" in out  # subsys_vendor
        assert "rw[159:144] <= 16'h0001;" in out  # subsys_id
        assert "rw[175:160] <= 16'h8086;" in out  # vend
        assert "rw[191:176] <= 16'h1533;" in out  # dev
        assert "rw[199:192] <= 8'h03;" in out     # rev

    def test_preserves_anchor_comments(self):
        out = patch_pcileech_fifo(ENIGMAX1_FIFO, _intel_donor())

        for tag in (
            "+010: CFG_SUBSYS_VEND_ID",
            "+012: CFG_SUBSYS_ID",
            "+014: CFG_VEND_ID",
            "+016: CFG_DEV_ID",
            "+018: CFG_REV_ID",
        ):
            assert tag in out, f"comment anchor {tag!r} lost during patch"

    def test_rewrites_80bit_packed_initializer(self):
        out = patch_pcileech_fifo(ENIGMAX1_FIFO, _intel_donor())

        # Trailing 5 fields are rev, dev, vend, subsys_id, subsys_vend.
        assert (
            "{ 4'hf, 1'b1, 1'b1, 1'b0, 1'b0, 8'h03, 16'h1533, 16'h8086, 16'h0001, 16'h8086 }"
            in out
        )
        assert "8'h02, 16'h0666, 16'h10EE, 16'h0007, 16'h10EE" not in out

    def test_rewrites_78bit_packed_initializer(self):
        out = patch_pcileech_fifo(NETV2_FIFO, _intel_donor())

        assert (
            "{ 1'b0, 1'b1, 1'b1, 1'b1, 1'b0, 1'b0, 8'h03, 16'h1533, 16'h8086, 16'h0001, 16'h8086 }"
            in out
        )
        assert "16'h0666" not in out
        assert "16'h10EE" not in out

    def test_preserves_unrelated_lines(self):
        out = patch_pcileech_fifo(ENIGMAX1_FIFO, _intel_donor())

        # rw[200] sits below the patched block and must survive untouched.
        assert "rw[200]     <= 1'b1;" in out
        # Reset assigns that already work must be untouched.
        assert "assign dpcie.pcie_rst_core    = _pcie_core_config[72];" in out

    def test_xilinx_donor_yields_xilinx_defaults(self):
        # Donor that happens to match Xilinx defaults should round-trip.
        same = DonorIDs(
            vendor_id=0x10EE,
            device_id=0x0666,
            subsystem_vendor_id=0x10EE,
            subsystem_id=0x0007,
            revision_id=0x02,
        )
        out = patch_pcileech_fifo(ENIGMAX1_FIFO, same)
        assert out == ENIGMAX1_FIFO

    def test_missing_anchor_raises(self):
        # Strip the +014: CFG_VEND_ID line entirely — patcher must raise.
        broken = re.sub(
            r".*\+014: CFG_VEND_ID.*\n", "", ENIGMAX1_FIFO
        )
        with pytest.raises(FifoPatchError) as exc:
            patch_pcileech_fifo(broken, _intel_donor())
        assert "+014" in str(exc.value) or "CFG_VEND_ID" in str(exc.value)

    def test_missing_initializer_raises(self):
        broken = re.sub(
            r"reg\s+\[\d+:0\]\s+_pcie_core_config\s*=.*?;\n",
            "",
            ENIGMAX1_FIFO,
        )
        with pytest.raises(FifoPatchError) as exc:
            patch_pcileech_fifo(broken, _intel_donor())
        assert "_pcie_core_config" in str(exc.value)

    def test_donor_value_out_of_range_raises(self):
        bad = DonorIDs(
            vendor_id=0x8086,
            device_id=0x1533,
            subsystem_vendor_id=0x8086,
            subsystem_id=0x0001,
            revision_id=0x100,  # 9 bits — too wide for 8'h
        )
        with pytest.raises(FifoPatchError):
            patch_pcileech_fifo(ENIGMAX1_FIFO, bad)


# ---------------------------------------------------------------------------
# Cfg-id assign comment-out branch (issue #593 symptom B)
# ---------------------------------------------------------------------------


class TestCfgIdAssignsCommentBranch:
    """When the header lacks the cfg-id fields, comment out the assigns."""

    def test_comments_out_when_header_missing_fields(self):
        out = patch_pcileech_fifo(
            CAPTAIN_75T_FIFO, _intel_donor(), header_text=HEADER_WITHOUT_CFG_FIELDS
        )

        for field in (
            "pcie_cfg_subsys_vend_id",
            "pcie_cfg_subsys_id",
            "pcie_cfg_vend_id",
            "pcie_cfg_dev_id",
            "pcie_cfg_rev_id",
        ):
            assigns = [
                line
                for line in out.splitlines()
                if f"dpcie.{field}" in line and "assign" in line
            ]
            assert assigns, f"missing line referencing {field}"
            for line in assigns:
                stripped = line.lstrip()
                assert stripped.startswith("//"), (
                    f"expected line to be commented out, got: {line!r}"
                )

        # Reset assigns must NOT be commented out.
        rst_lines = [
            line
            for line in out.splitlines()
            if "dpcie.pcie_rst_core" in line and "assign" in line
        ]
        assert rst_lines and not rst_lines[0].lstrip().startswith("//")

    def test_keeps_assigns_when_header_declares_fields(self):
        out = patch_pcileech_fifo(
            CAPTAIN_75T_FIFO, _intel_donor(), header_text=HEADER_WITH_CFG_FIELDS
        )

        for field in (
            "pcie_cfg_subsys_vend_id",
            "pcie_cfg_subsys_id",
            "pcie_cfg_vend_id",
            "pcie_cfg_dev_id",
            "pcie_cfg_rev_id",
        ):
            assigns = [
                line
                for line in out.splitlines()
                if f"dpcie.{field}" in line and "assign" in line
            ]
            assert assigns
            for line in assigns:
                assert not line.lstrip().startswith("//"), (
                    f"line should be left active, got: {line!r}"
                )

    def test_no_header_keeps_assigns(self):
        # Backward-compatible default: no header passed → no commenting.
        out = patch_pcileech_fifo(CAPTAIN_75T_FIFO, _intel_donor())
        assigns = [
            line
            for line in out.splitlines()
            if "dpcie.pcie_cfg_subsys_vend_id" in line and "assign" in line
        ]
        assert assigns and not assigns[0].lstrip().startswith("//")


# ---------------------------------------------------------------------------
# Filesystem orchestration: apply_fifo_donor_patch
# ---------------------------------------------------------------------------


class TestApplyFifoDonorPatch:
    """apply_fifo_donor_patch reads from disk, patches in place, returns summary."""

    def test_patches_files_in_place(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        fifo_path = src_dir / "pcileech_fifo.sv"
        fifo_path.write_text(CAPTAIN_75T_FIFO)
        header_path = src_dir / "pcileech_header.svh"
        header_path.write_text(HEADER_WITHOUT_CFG_FIELDS)

        result = apply_fifo_donor_patch(src_dir, _intel_donor())

        assert result["patched"] is True
        assert result["fifo_path"] == fifo_path
        assert result["cfg_assigns_commented"] is True

        patched = fifo_path.read_text()
        assert "rw[175:160] <= 16'h8086;" in patched
        # Cfg-id assigns commented out.
        for field in (
            "pcie_cfg_subsys_vend_id",
            "pcie_cfg_subsys_id",
            "pcie_cfg_vend_id",
            "pcie_cfg_dev_id",
            "pcie_cfg_rev_id",
        ):
            for line in patched.splitlines():
                if f"dpcie.{field}" in line and "assign" in line:
                    assert line.lstrip().startswith("//")

    def test_skips_when_fifo_missing(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        result = apply_fifo_donor_patch(src_dir, _intel_donor())

        assert result["patched"] is False
        assert result.get("reason") == "fifo_not_found"

    def test_leaves_assigns_when_header_declares_fields(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "pcileech_fifo.sv").write_text(CAPTAIN_75T_FIFO)
        (src_dir / "pcileech_header.svh").write_text(HEADER_WITH_CFG_FIELDS)

        result = apply_fifo_donor_patch(src_dir, _intel_donor())

        assert result["patched"] is True
        assert result["cfg_assigns_commented"] is False

    def test_raises_on_unpatchable_unknown_field(self, tmp_path):
        # A fifo that writes to an interface field neither in the header nor
        # in the patcher's known set of "comment me out" fields. Patcher must
        # not silently leave the broken assign — raise so the build aborts
        # before Vivado does.
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        rogue_fifo = CAPTAIN_75T_FIFO + textwrap.dedent(
            """
            assign dpcie.totally_made_up_field = 1'b0;
            """
        )
        (src_dir / "pcileech_fifo.sv").write_text(rogue_fifo)
        (src_dir / "pcileech_header.svh").write_text(HEADER_WITHOUT_CFG_FIELDS)

        with pytest.raises(FifoPatchError) as exc:
            apply_fifo_donor_patch(src_dir, _intel_donor())
        assert "totally_made_up_field" in str(exc.value)

    def test_unknown_field_passes_when_declared_in_header(self, tmp_path):
        # Same rogue field, but the header declares it — no error.
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        rogue_fifo = CAPTAIN_75T_FIFO + textwrap.dedent(
            """
            assign dpcie.totally_made_up_field = 1'b0;
            """
        )
        (src_dir / "pcileech_fifo.sv").write_text(rogue_fifo)
        (src_dir / "pcileech_header.svh").write_text(
            HEADER_WITH_CFG_FIELDS.replace(
                "endinterface",
                "    wire totally_made_up_field;\nendinterface",
            )
        )

        result = apply_fifo_donor_patch(src_dir, _intel_donor())
        assert result["patched"] is True

    def test_propagates_patch_errors(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        # Strip the +016 anchor to trigger FifoPatchError from the inner pass.
        (src_dir / "pcileech_fifo.sv").write_text(
            re.sub(r".*\+016: CFG_DEV_ID.*\n", "", CAPTAIN_75T_FIFO)
        )

        with pytest.raises(FifoPatchError):
            apply_fifo_donor_patch(src_dir, _intel_donor())


# ---------------------------------------------------------------------------
# Template-context adapter
# ---------------------------------------------------------------------------


class TestDonorIdsFromTemplateContext:
    """Pull DonorIDs out of the build's template_context dict."""

    def test_reads_int_suffixed_fields(self):
        ctx = {
            "device_config": {
                "vendor_id_int": 0x8086,
                "device_id_int": 0x1533,
                "subsystem_vendor_id_int": 0x8086,
                "subsystem_device_id_int": 0x0001,
                "revision_id_int": 0x03,
            }
        }
        donor = donor_ids_from_template_context(ctx)
        assert donor == DonorIDs(0x8086, 0x1533, 0x8086, 0x0001, 0x03)

    def test_falls_back_to_string_fields(self):
        ctx = {
            "device_config": {
                "vendor_id": "0x8086",
                "device_id": "0x1533",
                "subsystem_vendor_id": "0x8086",
                "subsystem_device_id": "0x0001",
                "revision_id": "0x03",
            }
        }
        donor = donor_ids_from_template_context(ctx)
        assert donor == DonorIDs(0x8086, 0x1533, 0x8086, 0x0001, 0x03)

    def test_returns_none_when_device_config_missing(self):
        assert donor_ids_from_template_context({}) is None

    def test_returns_none_when_required_field_zero(self):
        # vendor_id=0 means we never recovered donor data — refuse to patch.
        ctx = {
            "device_config": {
                "vendor_id_int": 0,
                "device_id_int": 0x1533,
                "subsystem_vendor_id_int": 0x8086,
                "subsystem_device_id_int": 0x0001,
                "revision_id_int": 0x03,
            }
        }
        assert donor_ids_from_template_context(ctx) is None

    def test_subsystem_zero_falls_back_to_main_vendor(self):
        # Real devices often report subsystem IDs of 0; mirror the helper-macro
        # behavior of falling back to the main vendor so the patch still produces
        # a valid build.
        ctx = {
            "device_config": {
                "vendor_id_int": 0x8086,
                "device_id_int": 0x1533,
                "subsystem_vendor_id_int": 0,
                "subsystem_device_id_int": 0,
                "revision_id_int": 0x03,
            }
        }
        donor = donor_ids_from_template_context(ctx)
        assert donor == DonorIDs(0x8086, 0x1533, 0x8086, 0x0000, 0x03)


# ---------------------------------------------------------------------------
# Build wiring: FirmwareBuilder._patch_fifo_with_donor_ids
# ---------------------------------------------------------------------------


class TestBuildWiring:
    """The build's _patch_fifo_with_donor_ids step calls into the patcher."""

    def _make_builder(self, output_dir):
        # Some other tests in the suite stub `pcileechfwgenerator.build`
        # via sys.modules and never restore it. Force a real reimport so
        # FirmwareBuilder resolves regardless of test ordering.
        import importlib
        import sys as _sys
        _sys.modules.pop("pcileechfwgenerator.build", None)
        build_module = importlib.import_module("pcileechfwgenerator.build")
        FirmwareBuilder = build_module.FirmwareBuilder

        builder = FirmwareBuilder.__new__(FirmwareBuilder)
        builder.config = type(
            "C", (), {"output_dir": Path(output_dir), "board": "75t"}
        )()
        builder.logger = __import__("logging").getLogger("test")
        return builder

    def test_patches_when_donor_present(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "pcileech_fifo.sv").write_text(CAPTAIN_75T_FIFO)
        (src_dir / "pcileech_header.svh").write_text(HEADER_WITHOUT_CFG_FIELDS)

        builder = self._make_builder(tmp_path)
        result = {
            "template_context": {
                "device_config": {
                    "vendor_id_int": 0x8086,
                    "device_id_int": 0x1533,
                    "subsystem_vendor_id_int": 0x8086,
                    "subsystem_device_id_int": 0x0001,
                    "revision_id_int": 0x03,
                }
            }
        }

        builder._patch_fifo_with_donor_ids(result)

        patched = (src_dir / "pcileech_fifo.sv").read_text()
        assert "16'h8086" in patched
        assert "16'h1533" in patched
        # 75t484_x1-shaped fixture had cfg-id assigns; with header missing
        # the fields, they must be commented after patching.
        for line in patched.splitlines():
            if "dpcie.pcie_cfg_subsys_vend_id" in line and "assign" in line:
                assert line.lstrip().startswith("//")

    def test_skips_silently_without_donor_data(self, tmp_path, caplog):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)

        builder = self._make_builder(tmp_path)
        # Empty template_context → no donor data → no patching, just a warning.
        with caplog.at_level("WARNING"):
            builder._patch_fifo_with_donor_ids({"template_context": {}})

        assert (src_dir / "pcileech_fifo.sv").read_text() == ENIGMAX1_FIFO
        assert any(
            "issue #593" in rec.message or "donor" in rec.message.lower()
            for rec in caplog.records
        )

    def test_raises_on_anchor_mismatch(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "pcileech_fifo.sv").write_text(
            re.sub(r".*\+010: CFG_SUBSYS_VEND_ID.*\n", "", ENIGMAX1_FIFO)
        )

        builder = self._make_builder(tmp_path)
        result = {
            "template_context": {
                "device_config": {
                    "vendor_id_int": 0x8086,
                    "device_id_int": 0x1533,
                    "subsystem_vendor_id_int": 0x8086,
                    "subsystem_device_id_int": 0x0001,
                    "revision_id_int": 0x03,
                }
            }
        }
        with pytest.raises(FifoPatchError):
            builder._patch_fifo_with_donor_ids(result)
