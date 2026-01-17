#!/usr/bin/env python3
"""
Test script for BAR Size Conversion functionality.
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pcileechfwgenerator.device_clone.bar_size_converter import BarSizeConverter
from pcileechfwgenerator.device_clone.constants import BAR_SIZE_CONSTANTS


def test_size_to_encoding():
    """Test BAR size to encoding conversion."""
    print("Testing BAR size to encoding conversion...")

    test_cases = [
        # (size, bar_type, is_64bit, prefetchable, expected_encoding)
        (4096, "memory", False, False, 0xFFFFF000),  # 4KB memory BAR
        (65536, "memory", False, False, 0xFFFF0000),  # 64KB memory BAR
        (1048576, "memory", False, False, 0xFFF00000),  # 1MB memory BAR
        (256, "io", False, False, 0xFFFFFF01),  # 256B I/O BAR
        (16, "io", False, False, 0xFFFFFFF1),  # 16B I/O BAR
        (4096, "memory", False, True, 0xFFFFF008),  # 4KB prefetchable memory
        (65536, "memory", True, False, 0xFFFF0004),  # 64KB 64-bit memory
        (0, "memory", False, False, 0x00000000),  # Disabled BAR
    ]

    for size, bar_type, is_64bit, prefetchable, expected in test_cases:
        try:
            encoding = BarSizeConverter.size_to_encoding(
                size, bar_type, is_64bit, prefetchable
            )
            status = "PASS" if encoding == expected else "FAIL"
            print(
                f"  {status}: Size={size}, Type={bar_type}, 64bit={is_64bit}, "
                f"Prefetch={prefetchable} -> 0x{encoding:08X} (expected 0x{expected:08X})"
            )
        except Exception as e:
            print(f"  ERROR: Size={size}, Type={bar_type} -> {e}")


def test_validate_bar_size():
    """Test BAR size validation."""
    print("\nTesting BAR size validation...")

    test_cases = [
        # (size, bar_type, expected_valid)
        (0, "memory", True),  # Disabled is valid
        (128, "memory", True),  # Minimum memory size
        (64, "memory", False),  # Below minimum
        (4096, "memory", True),  # Valid power of 2
        (5000, "memory", False),  # Not power of 2
        (16, "io", True),  # Minimum I/O size
        (256, "io", True),  # Maximum I/O size
        (512, "io", False),  # Above maximum I/O
        (8, "io", False),  # Below minimum I/O
    ]

    for size, bar_type, expected in test_cases:
        valid = BarSizeConverter.validate_bar_size(size, bar_type)
        status = "PASS" if valid == expected else "FAIL"
        print(
            f"  {status}: Size={size}, Type={bar_type} -> "
            f"Valid={valid} (expected {expected})"
        )


def test_format_size():
    """Test size formatting."""
    print("\nTesting size formatting...")

    test_cases = [
        (0, "Disabled"),
        (256, "256 bytes"),
        (1024, "1KB"),
        (4096, "4KB"),
        (65536, "64KB"),
        (1048576, "1MB"),
        (16777216, "16MB"),
        (268435456, "256MB"),
        (1073741824, "1GB"),
    ]

    for size, expected in test_cases:
        formatted = BarSizeConverter.format_size(size)
        status = "PASS" if formatted == expected else "FAIL"
        print(f"  {status}: {size} -> '{formatted}' (expected '{expected}')")


def test_decode_bar_register():
    """Test BAR register decoding."""
    print("\nTesting BAR register decoding...")

    test_cases = [
        # (bar_value, expected_type, expected_addr, expected_64bit, expected_prefetch)
        (0xF0000000, "memory", 0xF0000000, False, False),  # 32-bit memory
        (0xF0000004, "memory", 0xF0000000, True, False),  # 64-bit memory
        (0xF0000008, "memory", 0xF0000000, False, True),  # Prefetchable
        (0xF000000C, "memory", 0xF0000000, True, True),  # 64-bit prefetchable
        (0x0000E001, "io", 0x0000E000, False, False),  # I/O BAR
    ]

    for bar_value, exp_type, exp_addr, exp_64bit, exp_prefetch in test_cases:
        bar_type, address, is_64bit, prefetchable = (
            BarSizeConverter.decode_bar_register(bar_value)
        )

        all_match = (
            bar_type == exp_type
            and address == exp_addr
            and is_64bit == exp_64bit
            and prefetchable == exp_prefetch
        )
        status = "PASS" if all_match else "FAIL"

        print(
            f"  {status}: 0x{bar_value:08X} -> Type={bar_type}, Addr=0x{address:08X}, "
            f"64bit={is_64bit}, Prefetch={prefetchable}"
        )


def test_convert_bar_for_shadow_space():
    """Test complete BAR conversion for shadow space."""
    print("\nTesting BAR conversion for shadow space...")

    test_bars = [
        {
            "base_address": 0xF0000000,
            "size": 65536,  # 64KB
            "bar_type": "memory",
            "is_64bit": False,
            "prefetchable": False,
        },
        {
            "base_address": 0xE0000000,
            "size": 1048576,  # 1MB
            "bar_type": "memory",
            "is_64bit": True,
            "prefetchable": True,
        },
        {
            "base_address": 0x0000E000,
            "size": 256,  # 256 bytes
            "bar_type": "io",
            "is_64bit": False,
            "prefetchable": False,
        },
    ]

    for bar_info in test_bars:
        result = BarSizeConverter.convert_bar_for_shadow_space(bar_info)
        print(
            f"  BAR: {bar_info['bar_type']} @ 0x{bar_info['base_address']:08X}, "
            f"size={bar_info['size']}"
        )
        print(
            f"    -> Encoded: 0x{result['encoded_value']:08X}, "
            f"Size: {result['size_str']}"
        )


def main():
    """Run all tests."""
    print("BAR Size Conversion Test Suite")
    print("=" * 50)

    test_size_to_encoding()
    test_validate_bar_size()
    test_format_size()
    test_decode_bar_register()
    test_convert_bar_for_shadow_space()

    print("\nAll tests completed!")


if __name__ == "__main__":
    main()
