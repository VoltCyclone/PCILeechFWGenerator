"""Tests for PCIe IP donor-ID override TCL emission (issue #593, T2).

Donor IDs need to override the Vivado PCIe 7-series IP's CONFIG properties so
the host sees donor vendor/device/subsys/rev during enumeration. The upstream
``vivado_generate_project_*.tcl`` imports ``pcie_7x_0.xci`` verbatim with its
Xilinx defaults (``Vendor_ID=10EE``, ``Device_ID=0666``, etc.). These helpers
emit a TCL fragment that runs after the IP is imported and patches its CONFIG.
"""

import re
import sys
import textwrap
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import DonorIDs
from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
    PcieIpOverrideError,
    apply_pcie_ip_donor_override,
    generate_pcie_ip_override_tcl,
)


def _intel_donor() -> DonorIDs:
    return DonorIDs(
        vendor_id=0x8086,
        device_id=0x1533,
        subsystem_vendor_id=0x8086,
        subsystem_id=0x0001,
        revision_id=0x03,
    )


# ---------------------------------------------------------------------------
# Pure TCL emission
# ---------------------------------------------------------------------------


class TestGeneratePcieIpOverrideTcl:
    """generate_pcie_ip_override_tcl renders a Vivado set_property block."""

    def test_emits_all_five_config_properties(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor())

        # Vivado property names are case-sensitive; assert exact spellings.
        assert "CONFIG.Vendor_ID 8086" in tcl
        assert "CONFIG.Device_ID 1533" in tcl
        assert "CONFIG.Subsystem_Vendor_ID 8086" in tcl
        assert "CONFIG.Subsystem_ID 0001" in tcl
        assert "CONFIG.Revision_ID 03" in tcl

    def test_targets_pcie_7x_0_by_default(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        assert "[get_ips pcie_7x_0]" in tcl

    def test_accepts_custom_ip_name(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), ip_name="pcie_us_0")
        assert "[get_ips pcie_us_0]" in tcl
        assert "[get_ips pcie_7x_0]" not in tcl

    def test_regenerates_target_after_property_change(self):
        # Without generate_target the CONFIG change won't propagate to the
        # generated HDL — IP must be re-generated after override.
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        assert "generate_target" in tcl
        assert re.search(r"generate_target.*\[get_ips pcie_7x_0\]", tcl)

    def test_generate_target_uses_force_flag(self):
        # -force bypasses Vivado's up-to-date heuristic per UG835. Without it
        # there are known cases where DCP timestamp drift causes Vivado to
        # skip regeneration even when CONFIG.* properties changed. Cheap
        # belt-and-suspenders against the failure mode.
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        assert "generate_target -force" in tcl

    def test_aborts_when_ip_absent_or_ambiguous(self):
        # A missing or ambiguous IP would ship stock Xilinx IDs, so the script
        # must abort (behavior covered in test_msix_ip_contract.py).
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        assert "[llength $_pcileech_ips] != 1" in tcl
        assert "[get_ips -quiet pcie_7x_0]" in tcl

    def test_balanced_braces(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        # Balanced braces are a basic well-formedness check; Vivado will
        # otherwise abort with a generic syntax error.
        assert tcl.count("{") == tcl.count("}")
        assert tcl.count("[") == tcl.count("]")

    def test_includes_issue_marker(self):
        # Future debuggers should be able to grep for the origin of this code.
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        assert "#593" in tcl or "issue 593" in tcl.lower()


# ---------------------------------------------------------------------------
# Filesystem orchestration
# ---------------------------------------------------------------------------


_FAKE_GENERATE_PROJECT = textwrap.dedent("""\
    # Upstream vivado_generate_project_captaindma_75t.tcl (truncated)
    create_project pcileech ./vivado_project -part xc7a75tfgg484-2

    # Import IP
    set files [list \\
     [file normalize "${origin_dir}/ip/pcie_7x_0.xci"]\\
    ]
    import_files -fileset sources_1 $files
    """)


class TestApplyPcieIpDonorOverride:
    """apply_pcie_ip_donor_override writes the override file only.

    The unified build (build_all.tcl) sources it after a manifest hash check;
    the board's own project scripts are not staged, so nothing is wired.
    """

    def test_writes_override_file(self, tmp_path):
        result = apply_pcie_ip_donor_override(tmp_path, _intel_donor())

        assert result == {"override_path": tmp_path / "pcileech_donor_ip_overrides.tcl"}
        assert "CONFIG.Vendor_ID 8086" in result["override_path"].read_text()

    def test_idempotent(self, tmp_path):
        first = apply_pcie_ip_donor_override(tmp_path, _intel_donor())
        before = first["override_path"].read_text()
        apply_pcie_ip_donor_override(tmp_path, _intel_donor())
        assert first["override_path"].read_text() == before

    def test_leaves_board_project_scripts_alone(self, tmp_path):
        script = tmp_path / "vivado_generate_project.tcl"
        script.write_text(_FAKE_GENERATE_PROJECT)
        apply_pcie_ip_donor_override(tmp_path, _intel_donor())
        assert script.read_text() == _FAKE_GENERATE_PROJECT


# ---------------------------------------------------------------------------
# Build wiring
# ---------------------------------------------------------------------------


class TestBuildWiringIpOverride:
    """FirmwareBuilder._patch_fifo_with_donor_ids also wires the IP override.

    Wiring fixtures use 8086:10D3 so the existing placeholder-pair policy
    accepts them. The pure emitter tests above intentionally use 8086:1533;
    those do not pass through the production donor-context validation gate.
    """

    @pytest.fixture(autouse=True)
    def _enforce_production_donor_policy(self, monkeypatch):
        """Do not let a developer's escape hatch hide invalid test fixtures."""
        monkeypatch.delenv("PCILEECH_ALLOW_PLACEHOLDER_IDS", raising=False)

    def _make_builder(self, output_dir):
        # Some other tests stub `pcileechfwgenerator.build` via sys.modules
        # (test_container_unit, test_orchestration_local) and never restore it.
        # Force a reimport only when the cached FirmwareBuilder is missing
        # the method under test — popping an already-loaded real module would
        # invalidate other tests' patches against it.
        import importlib
        import sys as _sys

        cached = _sys.modules.get("pcileechfwgenerator.build")
        builder_cls = getattr(cached, "FirmwareBuilder", None)
        if builder_cls is None or not hasattr(
            builder_cls, "_patch_fifo_with_donor_ids"
        ):
            _sys.modules.pop("pcileechfwgenerator.build", None)
            build_module = importlib.import_module("pcileechfwgenerator.build")
        else:
            build_module = cached
        FirmwareBuilder = build_module.FirmwareBuilder

        builder = FirmwareBuilder.__new__(FirmwareBuilder)
        builder.config = type(
            "C", (), {"output_dir": Path(output_dir), "board": "75t"}
        )()
        builder.logger = __import__("logging").getLogger("test")
        return builder

    def test_writes_override(self, tmp_path):
        # FIFO present so the existing patch step doesn't return early.
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        from tests.test_fifo_donor_patcher import (  # noqa: PLC0415
            ENIGMAX1_FIFO,
        )

        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)

        builder = self._make_builder(tmp_path)
        result = {
            "template_context": {
                "bar_config": {"bars": [{"index": 0, "size": 4096, "is_memory": True}]},
                "device_config": {
                    "class_code": 0x020000,
                    "vendor_id_int": 0x8086,
                    "device_id_int": 0x10D3,
                    "subsystem_vendor_id_int": 0x8086,
                    "subsystem_device_id_int": 0x0001,
                    "revision_id_int": 0x03,
                },
            }
        }
        builder._patch_fifo_with_donor_ids(result)

        override = tmp_path / "pcileech_donor_ip_overrides.tcl"
        assert override.is_file()
        assert "CONFIG.Vendor_ID 8086" in override.read_text()
        assert not list(tmp_path.glob("vivado_*.tcl"))

    def test_aborts_when_override_cannot_be_written(
        self, tmp_path, caplog, monkeypatch
    ):
        # An incomplete donor override must not silently produce stock identity.
        from pcileechfwgenerator.vivado_handling import pcie_ip_donor_override

        def _fail(*_a, **_k):
            raise PcieIpOverrideError("cannot write override")

        monkeypatch.setattr(
            pcie_ip_donor_override, "apply_pcie_ip_donor_override", _fail
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        from tests.test_fifo_donor_patcher import (  # noqa: PLC0415
            ENIGMAX1_FIFO,
        )

        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)

        builder = self._make_builder(tmp_path)
        result = {
            "template_context": {
                "bar_config": {"bars": [{"index": 0, "size": 4096, "is_memory": True}]},
                "device_config": {
                    "class_code": 0x020000,
                    "vendor_id_int": 0x8086,
                    "device_id_int": 0x10D3,
                    "subsystem_vendor_id_int": 0x8086,
                    "subsystem_device_id_int": 0x0001,
                    "revision_id_int": 0x03,
                },
            }
        }
        with pytest.raises(PcieIpOverrideError):
            builder._patch_fifo_with_donor_ids(result)

        assert any(
            "PCIe IP donor override failed" in rec.message for rec in caplog.records
        )

    def test_forwards_donor_pcie_ip_config_to_override_file(self, tmp_path):
        """End-to-end: build wires all 8 donor fields into the override TCL.

        Fixture mirrors what PCILeechContextBuilder.build_context() produces
        AND simulates the Step 3 producers that flow from ext-cap parsing
        (DSN at template_context top-level; AER/ARI/Cpl_Timeout inside
        device_config).
        """
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        from tests.test_fifo_donor_patcher import (  # noqa: PLC0415
            ENIGMAX1_FIFO,
        )

        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)
        (tmp_path / "vivado_generate_project.tcl").write_text(_FAKE_GENERATE_PROJECT)

        builder = self._make_builder(tmp_path)
        result = {
            "template_context": {
                "bar_config": {"bars": [{"index": 0, "size": 4096, "is_memory": True}]},
                "pcie_max_link_speed": 3,  # Gen3 (LinkCap encoding == generation)
                "pcie_max_link_width": 8,
                "pcileech_config": {"max_payload_size": 512},
                "device_config": {
                    "class_code": 0x020000,
                    "vendor_id_int": 0x8086,
                    "device_id_int": 0x10D3,
                    "subsystem_vendor_id_int": 0x8086,
                    "subsystem_device_id_int": 0x0001,
                    "revision_id_int": 0x03,
                    "supports_aer": True,
                    "ari_capable": True,
                    "cpl_timeout_ranges": "ABCD",
                    "cpl_timeout_disable_sup": True,
                },
                "device_serial_number_int": (0xDEADBEEF << 32) | 0x01001B21,
            },
            "msix_data": {
                "enabled": True,
                "table_size": 16,
                "table_bir": 2,
                "table_offset": 0x2000,
                "pba_bir": 2,
                "pba_offset": 0x3000,
                "is_valid": True,
            },
        }
        builder._patch_fifo_with_donor_ids(result)

        override_text = (tmp_path / "pcileech_donor_ip_overrides.tcl").read_text()
        assert "CONFIG.Vendor_ID 8086" in override_text
        assert "CONFIG.Class_Code_Base 02" in override_text  # A4
        # A6: capture-time Enable cannot advertise an ungenerated subsystem.
        assert "CONFIG.MSIx_Enabled false" in override_text
        assert "CONFIG.MSIx_Table_Size" not in override_text
        # Gen3 (3) → Xilinx-encoded 4 via _LINK_SPEED_ENCODING.
        assert "CONFIG.LINK_CAP_MAX_LINK_SPEED 4" in override_text  # A7
        assert "CONFIG.LINK_CAP_MAX_LINK_WIDTH 8" in override_text
        assert "CONFIG.Max_Payload_Size 512_bytes" in override_text  # A8
        assert "CONFIG.AER_Enabled true" in override_text  # C1
        assert "CONFIG.DSN_HEX1 01001B21" in override_text  # C2
        assert "CONFIG.DSN_HEX2 DEADBEEF" in override_text
        assert "CONFIG.ARI_Forwarding_Supported true" in override_text  # C3
        # D2: the spelling Vivado 2026.1 accepts (it rejects "Range_ABCD").
        assert "CONFIG.Cpl_Timeout_Range Ranges_A,_B,_C_&_D" in override_text
        assert "Range_ABCD" not in override_text
        assert "CONFIG.Cpl_Timeout_Disable_Sup true" in override_text

    def test_valueerror_from_emitter_aborts_build(self, tmp_path, caplog):
        """A bad donor field cannot bypass the required override."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        from tests.test_fifo_donor_patcher import (  # noqa: PLC0415
            ENIGMAX1_FIFO,
        )

        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)
        (tmp_path / "vivado_generate_project.tcl").write_text(_FAKE_GENERATE_PROJECT)

        builder = self._make_builder(tmp_path)
        # cpl_timeout_ranges="ZZ" is invalid per the IP's accepted set; the
        # emitter raises ValueError. The build must propagate it.
        result = {
            "template_context": {
                "bar_config": {"bars": [{"index": 0, "size": 4096, "is_memory": True}]},
                "device_config": {
                    "class_code": 0x020000,
                    "vendor_id_int": 0x8086,
                    "device_id_int": 0x10D3,
                    "subsystem_vendor_id_int": 0x8086,
                    "subsystem_device_id_int": 0x0001,
                    "revision_id_int": 0x03,
                    "cpl_timeout_ranges": "ZZ",
                },
            },
        }

        # Sanity check that our bad-input fixture really triggers the emitter to raise.
        from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import (
            DonorIDs,
        )
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            DonorPCIeIPConfig,
            generate_pcie_ip_override_tcl,
        )

        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                DonorIDs(1, 2, 3, 4, 5),
                extra=DonorPCIeIPConfig(cpl_timeout_ranges="ZZ"),
            )

        # The caller must propagate the configuration failure.
        with pytest.raises(ValueError):
            builder._patch_fifo_with_donor_ids(result)

        assert any(
            "donor override failed" in rec.message.lower() for rec in caplog.records
        ), f"expected failure; logs were: {[rec.message for rec in caplog.records]}"
