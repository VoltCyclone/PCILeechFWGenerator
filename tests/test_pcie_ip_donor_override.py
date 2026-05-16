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
        assert "CONFIG.Vendor_ID 0x8086" in tcl
        assert "CONFIG.Device_ID 0x1533" in tcl
        assert "CONFIG.Subsystem_Vendor_ID 0x8086" in tcl
        assert "CONFIG.Subsystem_ID 0x0001" in tcl
        assert "CONFIG.Revision_ID 0x03" in tcl

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

    def test_skips_silently_when_ip_absent(self):
        # If the IP can't be found (e.g. running on a board variant that
        # ships a different PCIe IP), the script must not abort the build.
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        assert "if {" in tcl  # guarded by an existence check
        assert "[llength" in tcl or "[get_ips -quiet" in tcl

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


_FAKE_GENERATE_PROJECT = textwrap.dedent(
    """\
    # Upstream vivado_generate_project_captaindma_75t.tcl (truncated)
    create_project pcileech ./vivado_project -part xc7a75tfgg484-2

    # Import IP
    set files [list \\
     [file normalize "${origin_dir}/ip/pcie_7x_0.xci"]\\
    ]
    import_files -fileset sources_1 $files
    """
)


class TestApplyPcieIpDonorOverride:
    """apply_pcie_ip_donor_override writes the override and wires it in."""

    def test_writes_override_file_and_appends_source(self, tmp_path):
        (tmp_path / "vivado_generate_project_captaindma_75t.tcl").write_text(
            _FAKE_GENERATE_PROJECT
        )

        result = apply_pcie_ip_donor_override(tmp_path, _intel_donor())

        assert result["override_path"].name == "pcileech_donor_ip_overrides.tcl"
        assert result["override_path"].is_file()

        override = result["override_path"].read_text()
        assert "CONFIG.Vendor_ID 0x8086" in override

        # The upstream generate-project script must source our override.
        gp = (tmp_path / "vivado_generate_project_captaindma_75t.tcl").read_text()
        assert "source" in gp
        assert "pcileech_donor_ip_overrides.tcl" in gp

    def test_idempotent(self, tmp_path):
        (tmp_path / "vivado_generate_project.tcl").write_text(_FAKE_GENERATE_PROJECT)

        apply_pcie_ip_donor_override(tmp_path, _intel_donor())
        before = (tmp_path / "vivado_generate_project.tcl").read_text()

        apply_pcie_ip_donor_override(tmp_path, _intel_donor())
        after = (tmp_path / "vivado_generate_project.tcl").read_text()

        # Source line appended exactly once even on a second application.
        assert before == after
        assert after.count("pcileech_donor_ip_overrides.tcl") == 1

    def test_raises_when_no_generate_project_script(self, tmp_path):
        # No vivado_generate_project*.tcl staged — caller's mistake, fail loud.
        with pytest.raises(PcieIpOverrideError) as exc:
            apply_pcie_ip_donor_override(tmp_path, _intel_donor())
        assert "vivado_generate_project" in str(exc.value)

    def test_wires_into_all_matching_scripts(self, tmp_path):
        # Some boards ship multiple variants (e.g. _35t and _100t). We can't
        # know which one vivado_build.tcl will actually source, so wire the
        # override into every match instead of guessing.
        (tmp_path / "vivado_generate_project_35t.tcl").write_text(_FAKE_GENERATE_PROJECT)
        (tmp_path / "vivado_generate_project_100t.tcl").write_text(_FAKE_GENERATE_PROJECT)

        result = apply_pcie_ip_donor_override(tmp_path, _intel_donor())

        for name in ("vivado_generate_project_35t.tcl", "vivado_generate_project_100t.tcl"):
            assert "pcileech_donor_ip_overrides.tcl" in (tmp_path / name).read_text()

        assert result["override_path"].is_file()
        assert len(result["wired_scripts"]) == 2


# ---------------------------------------------------------------------------
# Build wiring
# ---------------------------------------------------------------------------


class TestBuildWiringIpOverride:
    """FirmwareBuilder._patch_fifo_with_donor_ids also wires the IP override."""

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
        if builder_cls is None or not hasattr(builder_cls, "_patch_fifo_with_donor_ids"):
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

    def test_writes_override_and_wires_source(self, tmp_path):
        # FIFO present so the existing patch step doesn't return early.
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        from tests.test_fifo_donor_patcher import (  # noqa: PLC0415
            ENIGMAX1_FIFO,
        )
        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)
        (tmp_path / "vivado_generate_project.tcl").write_text(
            _FAKE_GENERATE_PROJECT
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
        builder._patch_fifo_with_donor_ids(result)

        override = tmp_path / "pcileech_donor_ip_overrides.tcl"
        assert override.is_file()
        assert "CONFIG.Vendor_ID 0x8086" in override.read_text()

        gp = (tmp_path / "vivado_generate_project.tcl").read_text()
        assert "pcileech_donor_ip_overrides.tcl" in gp

    def test_warns_when_no_generate_project_script(self, tmp_path, caplog):
        # FIFO patch can run, but the IP override step must not break the
        # build when the generate-project script hasn't been staged yet.
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        from tests.test_fifo_donor_patcher import (  # noqa: PLC0415
            ENIGMAX1_FIFO,
        )
        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)

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
        with caplog.at_level("WARNING"):
            builder._patch_fifo_with_donor_ids(result)

        assert any(
            "PCIe IP donor override skipped" in rec.message
            for rec in caplog.records
        )

    def test_forwards_donor_pcie_ip_config_to_override_file(self, tmp_path):
        """The build wires extracted DonorPCIeIPConfig fields into the override TCL."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        from tests.test_fifo_donor_patcher import (  # noqa: PLC0415
            ENIGMAX1_FIFO,
        )
        (src_dir / "pcileech_fifo.sv").write_text(ENIGMAX1_FIFO)
        (tmp_path / "vivado_generate_project.tcl").write_text(
            _FAKE_GENERATE_PROJECT
        )

        builder = self._make_builder(tmp_path)
        result = {
            "template_context": {
                "max_payload_size": 256,
                "device_serial_number_int": (0xDEADBEEF << 32) | 0x01001B21,
                "device_config": {
                    "vendor_id_int": 0x8086,
                    "device_id_int": 0x1533,
                    "subsystem_vendor_id_int": 0x8086,
                    "subsystem_device_id_int": 0x0001,
                    "revision_id_int": 0x03,
                    "class_code": 0x020000,
                    "link_speed": 2,
                    "link_width": 4,
                    "supports_aer": True,
                },
            },
        }
        builder._patch_fifo_with_donor_ids(result)

        override_text = (tmp_path / "pcileech_donor_ip_overrides.tcl").read_text()
        # Core IDs still emitted.
        assert "CONFIG.Vendor_ID 0x8086" in override_text
        # Extras are now emitted too.
        assert "CONFIG.Class_Code_Base 02" in override_text
        assert "CONFIG.Max_Payload_Size 256_bytes" in override_text
        assert "CONFIG.LINK_CAP_MAX_LINK_SPEED 2" in override_text
        assert "CONFIG.LINK_CAP_MAX_LINK_WIDTH 4" in override_text
        assert "CONFIG.AER_Enabled true" in override_text
        assert "CONFIG.DSN_HEX1 01001B21" in override_text
        assert "CONFIG.DSN_HEX2 DEADBEEF" in override_text
