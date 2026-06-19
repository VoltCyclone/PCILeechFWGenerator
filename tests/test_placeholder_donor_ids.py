"""Tests for the synthetic/placeholder donor-ID rejection helper.

The project rejects synthetic donor data (AGENTS.md). The fail-fast guards
historically only caught missing/zero IDs; ``is_placeholder_donor_id`` closes
the gap for populated-but-fabricated *pairs* (e.g. the Intel I210 default
0x8086:0x1533) without rejecting a vendor on its own.
"""

import pytest
from pcileechfwgenerator.device_clone.constants import (
    KNOWN_PLACEHOLDER_IDS,
    is_placeholder_donor_id,
)


class TestIsPlaceholderDonorId:
    @pytest.mark.parametrize(
        "vid,did",
        [
            (0x8086, 0x1234),  # generic fallback
            (0x8086, 0x1533),  # synthetic Intel I210
            (0x8086, 0x2522),  # synthetic Intel NVMe
            (0x10EC, 0x8168),  # tcl/j2 default RTL8168
            (0x10DE, 0x2204),  # synthetic NVIDIA GPU
            (0x10EE, 0x0666),  # Xilinx FIFO default
            (0x10EE, 0x0007),  # Xilinx FIFO default
        ],
    )
    def test_known_placeholders_rejected(self, vid, did):
        assert is_placeholder_donor_id(vid, did) is True

    @pytest.mark.parametrize(
        "vid,did",
        [
            (0x1AF4, 0x1041),  # real virtio-net
            (0x8086, 0x10D3),  # real Intel 82574L (not a synthetic default)
            (0x10EC, 0x8139),  # real Realtek RTL8139
            (0x10DE, 0x1B80),  # real NVIDIA GTX 1080
            (0x10EE, 0x7024),  # real Xilinx device
        ],
    )
    def test_real_pairs_accepted(self, vid, did):
        assert is_placeholder_donor_id(vid, did) is False

    def test_vendor_alone_is_not_a_placeholder(self):
        # A real vendor paired with an unrelated device must never be rejected.
        assert is_placeholder_donor_id(0x8086, 0x9999) is False
        assert is_placeholder_donor_id(0x10EC, 0x1234) is False

    def test_accepts_int_and_enum_inputs(self):
        # The set is built from VendorID enum members coerced to int; the
        # function must work for plain ints regardless.
        assert is_placeholder_donor_id(int(0x8086), int(0x1533)) is True

    def test_set_contains_only_pairs(self):
        assert all(
            isinstance(entry, tuple) and len(entry) == 2
            for entry in KNOWN_PLACEHOLDER_IDS
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
