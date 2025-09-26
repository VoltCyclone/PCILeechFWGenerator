#!/usr/bin/env python3
"""
Unit tests for src.pci_capability.patches (BinaryPatch and PatchEngine).

These tests focus on critical paths: validation, overlap/conflict handling,
apply/rollback flows, and metadata helpers.
"""

from typing import List, Optional

import pytest

from src.pci_capability.core import ConfigSpace
from src.pci_capability.patches import BinaryPatch, PatchEngine
from src.pci_capability.types import PatchInfo


def make_config_space_with_bytes(
    size: int = 256, mutations: Optional[List[tuple]] = None
) -> ConfigSpace:
    """Create a ConfigSpace of given size with optional (offset, byte) mutations."""
    data = bytearray([0x00] * size)
    if mutations:
        for off, val in mutations:
            data[off] = val & 0xFF
    return ConfigSpace(data.hex())


class TestBinaryPatch:
    def test_init_validates_lengths_and_offset(self):
        # Mismatched lengths
        with pytest.raises(ValueError):
            BinaryPatch(0, b"\x00", b"\x00\x01")

        # Negative offset
        with pytest.raises(ValueError):
            BinaryPatch(-1, b"\x00", b"\x00")

    def test_overlap_logic(self):
        a = BinaryPatch(10, b"\xaa\xbb", b"\x00\x00")
        b = BinaryPatch(12, b"\xcc", b"\x01")  # touches a's end -> no overlap
        c = BinaryPatch(11, b"\xdd", b"\x02")  # inside a -> overlap

        assert a.overlaps_with(b) is False
        assert b.overlaps_with(a) is False
        assert a.overlaps_with(c) is True
        assert c.overlaps_with(a) is True

    def test_can_apply_and_apply_success(self):
        # Prepare config with bytes 0xAA 0xBB at offset 10
        cfg = make_config_space_with_bytes(mutations=[(10, 0xAA), (11, 0xBB)])
        patch = BinaryPatch(10, b"\xaa\xbb", b"\x01\x02")
        assert patch.can_apply_to(cfg) is True
        assert patch.apply_to(cfg) is True
        assert bytes(cfg[10:12]) == b"\x01\x02"

    def test_apply_fails_when_original_data_mismatch(self):
        cfg = make_config_space_with_bytes()  # all zeros
        patch = BinaryPatch(5, b"\x99", b"\x00")
        assert patch.can_apply_to(cfg) is False
        assert patch.apply_to(cfg) is False

    def test_rollback_success_and_guards(self):
        cfg = make_config_space_with_bytes(mutations=[(20, 0x10), (21, 0x11)])
        patch = BinaryPatch(20, b"\x10\x11", b"\x22\x33")

        # Guard: cannot rollback before apply
        assert patch.rollback_from(cfg) is False

        # Apply and then rollback
        assert patch.apply_to(cfg) is True
        assert bytes(cfg[20:22]) == b"\x22\x33"
        assert patch.rollback_from(cfg) is True
        assert bytes(cfg[20:22]) == b"\x10\x11"

    def test_to_patch_info(self):
        patch = BinaryPatch(0x30, b"\xde\xad", b"\xbe\xef")
        info = patch.to_patch_info("modify_test")
        assert isinstance(info, PatchInfo)
        assert info.offset == 0x30
        assert info.action == "modify_test"
        assert info.before_bytes == "dead"
        assert info.after_bytes == "beef"


class TestPatchEngine:
    def test_add_patch_conflict_and_duplicate_suppression(self):
        engine = PatchEngine()

        p1 = BinaryPatch(10, b"\x00\x00", b"\x01\x02")
        assert engine.add_patch(p1) is True

        # Overlap with different data -> conflict
        p_conflict = BinaryPatch(11, b"\x00", b"\xff")
        assert engine.add_patch(p_conflict) is False

        # Identical duplicate -> suppressed but treated as success
        p_dup = BinaryPatch(10, b"\x00\x00", b"\x01\x02")
        assert engine.add_patch(p_dup) is True

        assert len(engine) == 1  # only p1 stored

    def test_create_helpers_validation(self, capsys):
        engine = PatchEngine()

        # Byte out of range
        assert engine.create_byte_patch(0, -1, 0) is None
        assert engine.create_byte_patch(0, 0, 256) is None

        # Word out of range
        assert engine.create_word_patch(0, -1, 0) is None
        assert engine.create_word_patch(0, 0, 0x1_0000) is None

        # Dword out of range
        assert engine.create_dword_patch(0, -1, 0) is None
        assert engine.create_dword_patch(0, 0, 0x1_0000_0000) is None

        # Valid helpers
        assert engine.create_byte_patch(5, 0x00, 0xFF) is not None
        assert engine.create_word_patch(6, 0x0000, 0xABCD) is not None
        assert engine.create_dword_patch(8, 0x00000000, 0x12345678) is not None

    def test_validate_patches_and_apply_with_validation(self):
        # Config has 0xAA 0xBB at offset 32
        cfg = make_config_space_with_bytes(mutations=[(32, 0xAA), (33, 0xBB)])
        engine = PatchEngine()

        # Valid patch matches original data
        p_valid = engine.create_patch(32, b"\xaa\xbb", b"\x01\x02", "valid")
        assert p_valid is not None

        # Invalid patch (original doesn't match)
        p_invalid = engine.create_patch(40, b"\xff", b"\x00", "invalid")
        assert p_invalid is not None  # creation succeeds; will fail validation

        valid, errors = engine.validate_patches(cfg)
        assert p_valid in valid
        assert any("validation failed" in e.lower() for e in errors)

        applied_count, apply_errors = engine.apply_all_patches(cfg, validate_first=True)
        assert applied_count == 1
        assert len(apply_errors) == len(errors)
        # Ensure memory was changed for valid patch
        assert bytes(cfg[32:34]) == b"\x01\x02"

    def test_apply_without_validation_and_error_collection(self):
        cfg = make_config_space_with_bytes(mutations=[(50, 0x10)])
        engine = PatchEngine()

        # One valid and one invalid (mismatch) patch
        engine.create_patch(50, b"\x10", b"\x20", "ok")
        engine.create_patch(60, b"\xff", b"\x00", "bad")

        applied_count, errors = engine.apply_all_patches(cfg, validate_first=False)
        assert applied_count == 1
        assert any("Failed to apply patch" in e for e in errors)
        assert cfg.read_byte(50) == 0x20

    def test_rollback_all_patches_success_and_failure_paths(self):
        # Prepare config with two regions to patch
        cfg = make_config_space_with_bytes(mutations=[(70, 0x01), (80, 0x02)])
        engine = PatchEngine()

        p_a = engine.create_patch(70, b"\x01", b"\xa1", "A")
        p_b = engine.create_patch(80, b"\x02", b"\xb2", "B")
        assert p_a and p_b

        # Apply both
        applied_count, _ = engine.apply_all_patches(cfg, validate_first=True)
        assert applied_count == 2
        assert cfg.read_byte(70) == 0xA1 and cfg.read_byte(80) == 0xB2

        # Simulate a bug: mark one as not applied to trigger warning path
        engine.applied_patches[0].applied = False

        rolled_back_count, rb_errors = engine.rollback_all_patches(cfg)
        # One rollback should fail (applied flag False), one should succeed
        assert rolled_back_count == 1
        assert any("Failed to rollback patch" in e for e in rb_errors)

        # Verify data restored for the successfully rolled-back patch
        # We set applied=False on the first patch (offset 70), so it won't rollback
        # Reverse-order rollback means the second patch (offset 80) is rolled back successfully
        assert cfg.read_byte(70) == 0xA1  # remained patched
        assert cfg.read_byte(80) == 0x02  # rolled back to original

    def test_patch_info_list_clear_coverage_and_stats(self):
        cfg = make_config_space_with_bytes(mutations=[(100, 0x55), (101, 0x66)])
        engine = PatchEngine()

        p = engine.create_patch(100, b"\x55\x66", b"\x77\x88")
        assert p is not None

        infos = engine.get_patch_info_list(action_prefix="act")
        assert isinstance(infos, list) and len(infos) == 1
        assert isinstance(infos[0], PatchInfo)
        assert infos[0].action == "act_000"

        # Coverage map reflects bytes 100 and 101
        coverage = engine.get_coverage_map()
        assert set(coverage.keys()) == {100, 101}

        # Stats before apply
        stats = engine.get_statistics()
        assert stats["total_patches"] == 1
        assert stats["applied_patches"] == 0
        assert stats["total_bytes_modified"] == 2

        # Apply and recheck stats and repr
        engine.apply_all_patches(cfg, validate_first=True)
        stats2 = engine.get_statistics()
        assert stats2["applied_patches"] == 1
        rep = repr(engine)
        assert "PatchEngine(" in rep and "bytes=" in rep

        # Clear patches
        engine.clear_patches()
        assert len(engine) == 0
        assert engine.get_statistics()["total_patches"] == 0
