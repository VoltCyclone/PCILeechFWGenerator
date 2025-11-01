#!/usr/bin/env python3
"""
Unit tests for BarContentGenerator (BAR entropy and uniqueness).
"""
import hashlib

import pytest

from src.device_clone.bar_content_generator import (BarContentGenerator,
                                                    BarContentType)


def test_bar_content_entropy_and_uniqueness():
    device_sig = "testdevsig-1234"
    gen = BarContentGenerator(device_signature=device_sig)
    # Test a range of BAR sizes and types
    bar_sizes = [32, 256, 4096, 65536, 1048576]
    for idx, size in enumerate(bar_sizes):
        for ctype in [
            BarContentType.REGISTERS,
            BarContentType.BUFFER,
            BarContentType.FIRMWARE,
            BarContentType.MIXED,
        ]:
            data = gen.generate_bar_content(size, idx, ctype)
            stats = gen.get_entropy_stats(data)
            # Entropy and uniqueness should be high for all but smallest
            if size >= 256:
                assert (
                    stats["entropy"] > 3.0
                ), f"Low entropy for BAR {idx} size {size} type {ctype}"
                assert (
                    stats["uniqueness"] > 0.5
                ), f"Low uniqueness for BAR {idx} size {size} type {ctype}"
            # Content must be deterministic for same device_signature
            data2 = gen.generate_bar_content(size, idx, ctype)
            assert (
                data == data2
            ), "BAR content not deterministic for same device_signature"
    # Content must differ for different device_signature
    gen2 = BarContentGenerator(device_signature="diffsig-5678")
    data_a = gen.generate_bar_content(4096, 0, BarContentType.MIXED)
    data_b = gen2.generate_bar_content(4096, 0, BarContentType.MIXED)
    assert data_a != data_b, "BAR content not unique for different device_signature"


def test_generate_all_bars():
    gen = BarContentGenerator(device_signature="allbars-test")
    bar_sizes = {0: 4096, 1: 65536, 2: 128}
    all_bars = gen.generate_all_bars(bar_sizes)
    assert set(all_bars.keys()) == {0, 1, 2}
    for idx, content in all_bars.items():
        assert isinstance(content, bytes)
        assert len(content) == bar_sizes[idx]


def test_entropy_stats_empty():
    gen = BarContentGenerator()
    stats = gen.get_entropy_stats(b"")
    assert stats["entropy"] == 0.0
    assert stats["uniqueness"] == 0.0


def test_invalid_params():
    gen = BarContentGenerator()
    with pytest.raises(ValueError):
        gen.generate_bar_content(0, 0)
    with pytest.raises(ValueError):
        gen.generate_bar_content(128, -1)
    with pytest.raises(ValueError):
        gen.generate_bar_content(128, 6)
    with pytest.raises(ValueError):
        gen.generate_bar_content(-10, 0)


def test_determinism_across_instances_same_signature():
    """Outputs must be identical across separate instances when using
    the same device_signature and inputs."""
    sig = "determinism-sig-001"
    gen1 = BarContentGenerator(device_signature=sig)
    gen2 = BarContentGenerator(device_signature=sig)

    cases = [
        (0, 256, BarContentType.BUFFER),
        (1, 4096, BarContentType.REGISTERS),
        (2, 8192, BarContentType.FIRMWARE),
        (3, 16384, BarContentType.MIXED),
    ]
    for idx, size, ctype in cases:
        a = gen1.generate_bar_content(size, idx, ctype)
        b = gen2.generate_bar_content(size, idx, ctype)
        assert a == b, (
            f"Mismatch across instances for idx={idx} size={size} type={ctype}"
        )


def test_buffer_prefix_property_for_shake256():
    """Given same seed/context, longer BUFFER output should prefix-extend
    shorter output (property of SHAKE-256 XOF)."""
    sig = "shake-prefix-sig-002"
    gen = BarContentGenerator(device_signature=sig)

    idx = 1
    small = 4096
    big = 16384

    data_small = gen.generate_bar_content(small, idx, BarContentType.BUFFER)
    data_big = gen.generate_bar_content(big, idx, BarContentType.BUFFER)

    assert len(data_small) == small
    assert len(data_big) == big
    assert data_big[:small] == data_small
