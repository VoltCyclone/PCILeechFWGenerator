"""Tests for the expanded PCIe IP donor override (gaps A4/A6/A7/A8/C1/C2/C3/D2).

The original five-ID override is covered by test_pcie_ip_donor_override.py.
This module tests the additional CONFIG.* keys we emit when the donor profile
exposes class code, MPS, MSI-X layout, link speed/width, AER, ARI,
completion-timeout, and DSN values.
"""
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import DonorIDs
from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
    DonorPCIeIPConfig,
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


class TestDonorPCIeIPConfigDataclass:
    def test_all_fields_default_to_none(self):
        cfg = DonorPCIeIPConfig()
        # Every field must default to None so a partial donor profile is
        # safe — None means "don't emit a CONFIG.<key> line for this".
        assert cfg.class_code is None
        assert cfg.max_payload_size is None
        assert cfg.link_speed is None
        assert cfg.link_width is None
        assert cfg.msix_enabled is None
        assert cfg.msix_table_size is None
        assert cfg.msix_table_bir is None
        assert cfg.msix_table_offset is None
        assert cfg.msix_pba_bir is None
        assert cfg.msix_pba_offset is None
        assert cfg.aer_enabled is None
        assert cfg.ari_forwarding_supported is None
        assert cfg.cpl_timeout_ranges is None
        assert cfg.cpl_timeout_disable_supported is None
        assert cfg.dsn_value is None

    def test_is_frozen(self):
        cfg = DonorPCIeIPConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.class_code = 0x020000  # type: ignore[misc]


class TestGenerateTclWithEmptyExtra:
    """An all-None extra must produce exactly the same output as no extra at all."""

    def test_none_extra_matches_baseline(self):
        baseline = generate_pcie_ip_override_tcl(_intel_donor())
        with_empty = generate_pcie_ip_override_tcl(
            _intel_donor(), extra=DonorPCIeIPConfig()
        )
        assert baseline == with_empty


class TestExtraEmissionMechanism:
    """The TCL builder emits one CONFIG.<key> line per non-None extra field."""

    def test_omits_keys_for_none_fields(self):
        # An extra with every field None must not introduce any new CONFIG.*
        # lines beyond the original five.
        tcl = generate_pcie_ip_override_tcl(
            _intel_donor(), extra=DonorPCIeIPConfig()
        )
        config_lines = [
            line for line in tcl.splitlines()
            if line.strip().startswith("CONFIG.")
        ]
        assert len(config_lines) == 5  # Vendor, Device, SVID, SID, Rev

    def test_emits_backslash_continuation_consistently(self):
        # All CONFIG.* lines must end with " \\" so Vivado's set_property -dict
        # parses the list across newlines. Regressing this is a synthesis-time
        # syntax error that's annoying to debug.
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        for line in tcl.splitlines():
            stripped = line.rstrip()
            if "CONFIG." in stripped:
                assert stripped.endswith("\\"), (
                    f"missing line-continuation backslash: {line!r}"
                )


class TestClassCodeEmission:
    def test_emits_three_class_code_keys_when_set(self):
        # 0x020000 = Ethernet controller (Intel 82574L donor)
        extra = DonorPCIeIPConfig(class_code=0x020000)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        # Xilinx wants two-hex-digit strings, no 0x prefix.
        assert "CONFIG.Class_Code_Base 02" in tcl
        assert "CONFIG.Class_Code_Sub 00" in tcl
        assert "CONFIG.Class_Code_Interface 00" in tcl

    def test_nvme_class_code_unpacks_correctly(self):
        # 0x010802 = NVMe (base=01 mass storage, sub=08 NVM, interface=02 NVMe)
        extra = DonorPCIeIPConfig(class_code=0x010802)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Class_Code_Base 01" in tcl
        assert "CONFIG.Class_Code_Sub 08" in tcl
        assert "CONFIG.Class_Code_Interface 02" in tcl

    def test_class_code_none_emits_nothing(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "Class_Code_Base" not in tcl
        assert "Class_Code_Sub" not in tcl
        assert "Class_Code_Interface" not in tcl

    def test_class_code_out_of_range_raises(self):
        # 24-bit field; values that don't fit must surface, not silently truncate.
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(class_code=0x1000000),
            )


class TestMaxPayloadSizeEmission:
    @pytest.mark.parametrize("mps,token", [
        (128, "128_bytes"),
        (256, "256_bytes"),
        (512, "512_bytes"),
        (1024, "1024_bytes"),
        (2048, "2048_bytes"),
        (4096, "4096_bytes"),
    ])
    def test_emits_valid_mps_token(self, mps, token):
        extra = DonorPCIeIPConfig(max_payload_size=mps)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert f"CONFIG.Max_Payload_Size {token}" in tcl

    def test_invalid_mps_raises(self):
        # Anything not in the Xilinx-accepted set must fail loudly rather
        # than silently emitting a token Vivado will reject during IP elab.
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(), extra=DonorPCIeIPConfig(max_payload_size=384)
            )

    def test_none_emits_nothing(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "Max_Payload_Size" not in tcl
