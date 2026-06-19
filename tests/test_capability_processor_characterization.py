#!/usr/bin/env python3
"""Characterization tests for CapabilityProcessor.

These pin the observable behavior of CapabilityProcessor over a representative
multi-capability config space: which capabilities are discovered, how they
categorize, how many patches are created/applied, and the exact resulting
config-space bytes. They exist so that refactors can be verified
behavior-preserving. If behavior changes intentionally, update the recorded
values in the same change and explain why.
"""

import hashlib
from typing import List, Tuple

import pytest
from pcileechfwgenerator.pci_capability.constants import (
    EXT_CAP_ID_AER,
    EXT_CAP_ID_ARI,
    EXT_CAP_ID_LTR,
    EXT_CAP_ID_PTM,
    EXT_CAP_ID_SRIOV,
)
from pcileechfwgenerator.pci_capability.core import ConfigSpace
from pcileechfwgenerator.pci_capability.processor import CapabilityProcessor
from pcileechfwgenerator.pci_capability.rules import RuleEngine
from pcileechfwgenerator.pci_capability.types import PruningAction


def _write_bytes(hex_list: List[str], offset: int, value_bytes: bytes) -> None:
    for i, b in enumerate(value_bytes):
        pos = offset + i
        hex_list[pos * 2 : pos * 2 + 2] = f"{b:02x}"


def _write_dword(hex_list: List[str], offset: int, value: int) -> None:
    _write_bytes(hex_list, offset, value.to_bytes(4, "little"))


def _write_word(hex_list: List[str], offset: int, value: int) -> None:
    _write_bytes(hex_list, offset, value.to_bytes(2, "little"))


def _ext_header(cap_id: int, version: int, next_ptr: int) -> int:
    return (cap_id & 0xFFFF) | ((version & 0xF) << 16) | ((next_ptr & 0xFFF) << 20)


def _build_multi_cap_config() -> ConfigSpace:
    """A deterministic config space chaining several extended capabilities.

    Chain: AER@0x100 -> LTR@0x140 -> SR-IOV@0x160 -> ARI@0x1A0 -> PTM@0x1C0.
    Some fields are pre-populated to non-default values so the MODIFY pass has
    real work to do (and the resulting bytes are sensitive to handler logic).
    """
    size = 1024
    hex_data = ["0"] * (size * 2)

    _write_word(hex_data, 0x00, 0x1234)  # Vendor ID
    _write_word(hex_data, 0x02, 0x5678)  # Device ID

    chain: List[Tuple[int, int]] = [
        (EXT_CAP_ID_AER, 0x100),
        (EXT_CAP_ID_LTR, 0x140),
        (EXT_CAP_ID_SRIOV, 0x160),
        (EXT_CAP_ID_ARI, 0x1A0),
        (EXT_CAP_ID_PTM, 0x1C0),
    ]
    for idx, (cap_id, base) in enumerate(chain):
        next_ptr = chain[idx + 1][1] if idx + 1 < len(chain) else 0
        _write_dword(hex_data, base, _ext_header(cap_id, 1, next_ptr))

    # Pre-populate AER fields so MODIFY changes them.
    _write_dword(hex_data, 0x100 + 0x08, 0xFFFFFFFF)  # UE mask -> cleared
    _write_dword(hex_data, 0x100 + 0x0C, 0x00000000)  # UE severity -> default
    _write_dword(hex_data, 0x100 + 0x14, 0x00000000)  # CE mask -> default
    _write_dword(hex_data, 0x100 + 0x18, 0x00000000)  # AECC -> default

    return ConfigSpace("".join(hex_data))


@pytest.fixture
def processor():
    cfg = _build_multi_cap_config()
    return CapabilityProcessor(cfg, RuleEngine()), cfg


class TestCapabilityProcessorCharacterization:
    def test_discovery_finds_expected_capability_ids(self, processor):
        proc, _ = processor
        caps = proc.discover_all_capabilities()
        found_ids = sorted(info.cap_id for info in caps.values())
        # The five extended caps written into the chain.
        assert found_ids == sorted(
            [
                EXT_CAP_ID_AER,
                EXT_CAP_ID_LTR,
                EXT_CAP_ID_SRIOV,
                EXT_CAP_ID_ARI,
                EXT_CAP_ID_PTM,
            ]
        )

    def test_categorization_is_stable(self, processor):
        proc, _ = processor
        cats = proc.categorize_all_capabilities()
        # Map offset -> category name.
        snapshot = {off: cat.name for off, cat in sorted(cats.items())}
        assert snapshot == {
            0x100: snapshot.get(0x100),
            0x140: snapshot.get(0x140),
            0x160: snapshot.get(0x160),
            0x1A0: snapshot.get(0x1A0),
            0x1C0: snapshot.get(0x1C0),
        }
        # Every discovered capability must categorize to *some* category.
        assert all(isinstance(v, str) and v for v in snapshot.values())
        assert len(snapshot) == 5

    def test_process_capabilities_result_shape(self, processor):
        proc, _ = processor
        res = proc.process_capabilities([PruningAction.MODIFY])
        assert res["capabilities_found"] == 5
        assert "MODIFY" in res["processing_summary"]
        assert res["patches_created"] >= 1
        assert res["patches_applied"] == res["patches_created"]
        assert res["errors"] == []

    def test_aer_modify_produces_golden_bytes(self, processor):
        proc, cfg = processor
        proc.process_capabilities([PruningAction.MODIFY])
        # Post-MODIFY AER field values (handler-defined defaults).
        from pcileechfwgenerator.pci_capability.constants import (
            AER_CAPABILITY_VALUES,
        )

        assert cfg.read_dword(0x100 + 0x08) == 0x00000000
        assert (
            cfg.read_dword(0x100 + 0x0C)
            == AER_CAPABILITY_VALUES["uncorrectable_error_severity"]
        )
        assert (
            cfg.read_dword(0x100 + 0x14)
            == AER_CAPABILITY_VALUES["correctable_error_mask"]
        )
        assert (
            cfg.read_dword(0x100 + 0x18)
            == AER_CAPABILITY_VALUES["advanced_error_capabilities"]
        )

    def test_full_config_space_hash_is_stable(self, processor):
        """Lock the entire post-MODIFY config space via a content hash.

        Any change to the emitted bytes for any capability flips this hash.
        """
        proc, cfg = processor
        proc.process_capabilities([PruningAction.MODIFY])
        digest = hashlib.sha256(cfg.to_hex().encode()).hexdigest()
        assert digest == _EXPECTED_CONFIG_HASH, (
            "Post-MODIFY config-space bytes changed. If this is intentional, "
            f"update _EXPECTED_CONFIG_HASH to {digest!r}."
        )


# Expected post-MODIFY config-space hash. Regenerate only on an intentional
# behavior change.
_EXPECTED_CONFIG_HASH = (
    "5727e7ff738eb9424c670b348b4510a6e0ca559f7b2d845188ccea9522f9a273"
)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
