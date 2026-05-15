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
