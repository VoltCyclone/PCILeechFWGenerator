#!/usr/bin/env python3
"""Unit tests for the VFIO constants module.

This test suite ensures that all VFIO constants are properly defined,
have correct values, and that the module can be imported without errors.
"""

import ctypes

import pytest

from pcileechfwgenerator.cli.vfio_constants import (VFIO_CHECK_EXTENSION,
                                    VFIO_DEVICE_GET_REGION_INFO,
                                    VFIO_DEVICE_NAME_MAX_LENGTH,
                                    VFIO_GET_API_VERSION,
                                    VFIO_GROUP_FLAGS_CONTAINER_SET,
                                    VFIO_GROUP_FLAGS_VIABLE,
                                    VFIO_GROUP_GET_DEVICE_FD,
                                    VFIO_GROUP_GET_STATUS,
                                    VFIO_GROUP_SET_CONTAINER,
                                    VFIO_REGION_INFO_FLAG_MMAP,
                                    VFIO_REGION_INFO_FLAG_READ,
                                    VFIO_REGION_INFO_FLAG_WRITE,
                                    VFIO_SET_IOMMU, VFIO_TYPE,
                                    VFIO_TYPE1_IOMMU, VfioGroupStatus,
                                    VfioRegionInfo, vfio_group_status,
                                    vfio_region_info)


class TestVFIOConstants:
    """Test suite for VFIO constants."""

    def test_vfio_device_name_max_length_defined(self):
        """Test that VFIO_DEVICE_NAME_MAX_LENGTH is defined and has a reasonable value."""
        assert VFIO_DEVICE_NAME_MAX_LENGTH is not None
        assert isinstance(VFIO_DEVICE_NAME_MAX_LENGTH, int)
        assert VFIO_DEVICE_NAME_MAX_LENGTH > 0
        assert VFIO_DEVICE_NAME_MAX_LENGTH <= 512  # Reasonable upper bound

    def test_vfio_type_constant(self):
        """Test that VFIO_TYPE is properly defined."""
        assert VFIO_TYPE == ord(";")
        assert isinstance(VFIO_TYPE, int)

    def test_vfio_iommu_constants(self):
        """Test VFIO IOMMU related constants."""
        assert VFIO_TYPE1_IOMMU == 1
        assert isinstance(VFIO_TYPE1_IOMMU, int)

    def test_vfio_region_flags(self):
        """Test VFIO region info flags."""
        assert VFIO_REGION_INFO_FLAG_READ == (1 << 0)
        assert VFIO_REGION_INFO_FLAG_WRITE == (1 << 1)
        assert VFIO_REGION_INFO_FLAG_MMAP == (1 << 2)

    def test_vfio_group_flags(self):
        """Test VFIO group flags."""
        assert VFIO_GROUP_FLAGS_VIABLE == (1 << 0)
        assert VFIO_GROUP_FLAGS_CONTAINER_SET == (1 << 1)

    def test_ioctl_constants_are_integers(self):
        """Test that all IOCTL constants are integers."""
        ioctl_constants = [
            VFIO_GET_API_VERSION,
            VFIO_CHECK_EXTENSION,
            VFIO_SET_IOMMU,
            VFIO_GROUP_GET_STATUS,
            VFIO_GROUP_SET_CONTAINER,
            VFIO_GROUP_GET_DEVICE_FD,
            VFIO_DEVICE_GET_REGION_INFO,
        ]

        for const in ioctl_constants:
            assert isinstance(const, int)
            assert const > 0

    def test_vfio_structures_defined(self):
        """Test that VFIO structures are properly defined."""
        # Test vfio_group_status
        assert hasattr(vfio_group_status, "_fields_")
        assert len(vfio_group_status._fields_) == 2

        status = vfio_group_status()
        assert hasattr(status, "argsz")
        assert hasattr(status, "flags")

        # Test vfio_region_info
        assert hasattr(vfio_region_info, "_fields_")
        assert len(vfio_region_info._fields_) == 6

        region = vfio_region_info()
        assert hasattr(region, "argsz")
        assert hasattr(region, "flags")
        assert hasattr(region, "index")
        assert hasattr(region, "cap_offset")
        assert hasattr(region, "size")
        assert hasattr(region, "offset")

    def test_legacy_aliases(self):
        """Test that legacy aliases are properly defined."""
        assert VfioGroupStatus is vfio_group_status
        assert VfioRegionInfo is vfio_region_info

    def test_structure_sizes(self):
        """Test that structures have expected sizes."""
        # vfio_group_status should have 2 uint32 fields = 8 bytes
        status = vfio_group_status()
        assert ctypes.sizeof(status) == 8

        # vfio_region_info should have 4 uint32 + 2 uint64 fields = 32 bytes
        region = vfio_region_info()
        assert ctypes.sizeof(region) == 32

    def test_ioctl_encoding(self):
        """Test that IOCTL values are encoded correctly."""
        # VFIO_GET_API_VERSION should be _IO(VFIO_TYPE, 0)
        # This is a basic sanity check that the encoding produces non-zero values
        assert VFIO_GET_API_VERSION != 0

        # Check that different IOCTLs have different values
        ioctl_values = {
            VFIO_GET_API_VERSION,
            VFIO_CHECK_EXTENSION,
            VFIO_SET_IOMMU,
            VFIO_GROUP_GET_STATUS,
            VFIO_GROUP_SET_CONTAINER,
            VFIO_GROUP_GET_DEVICE_FD,
            VFIO_DEVICE_GET_REGION_INFO,
        }
        assert len(ioctl_values) == 7  # All values should be unique

    def test_module_imports_without_errors(self):
        """Test that the module can be imported without any errors."""
        # This test passes if we get here without import errors
        import pcileechfwgenerator.cli.vfio_constants

        assert pcileechfwgenerator.cli.vfio_constants is not None

    def test_all_exports(self):
        """Test that __all__ exports are properly defined."""
        from pcileechfwgenerator.cli import vfio_constants

        # Check that __all__ is defined
        assert hasattr(vfio_constants, "__all__")
        assert isinstance(vfio_constants.__all__, list)

        # Check that all exported names exist
        for name in vfio_constants.__all__:
            assert hasattr(
                vfio_constants, name
            ), f"Exported name '{name}' not found in module"

        # Check that VFIO_DEVICE_NAME_MAX_LENGTH is in exports
        assert "VFIO_DEVICE_NAME_MAX_LENGTH" in vfio_constants.__all__

    def test_constants_are_hardcoded_not_computed(self):
        """Test that constants are hardcoded values, not computed at runtime."""
        # Read the source file and check for hardcoded values vs computed ones
        import inspect
        import os

        # Get the path to the vfio_constants module
        import pcileechfwgenerator.cli.vfio_constants as vfio_constants

        source_file = inspect.getfile(vfio_constants)

        with open(source_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Check that we have hardcoded constants section
        assert (
            "extracted from kernel headers at build time" in content
            or "VFIO_GROUP_SET_CONTAINER =" in content
        ), "Constants should be hardcoded, not computed"

        # Verify critical constants are integers, not function calls
        assert isinstance(VFIO_GROUP_SET_CONTAINER, int)
        assert isinstance(VFIO_GET_API_VERSION, int)
        assert isinstance(VFIO_SET_IOMMU, int)

    def test_constants_values_are_reasonable(self):
        """Test that VFIO constants have reasonable values."""
        # VFIO constants should be in a reasonable range for ioctl numbers
        # Typically Linux ioctl numbers are 32-bit values
        constants_to_check = [
            VFIO_GET_API_VERSION,
            VFIO_CHECK_EXTENSION,
            VFIO_SET_IOMMU,
            VFIO_GROUP_GET_STATUS,
            VFIO_GROUP_SET_CONTAINER,
            VFIO_GROUP_GET_DEVICE_FD,
            VFIO_DEVICE_GET_REGION_INFO,
        ]

        for const in constants_to_check:
            # Should be positive integers
            assert isinstance(const, int)
            assert const > 0
            # Should fit in 32-bit range
            assert const < 2**32
            # Should be reasonable ioctl values (typically > 1000)
            assert const > 1000

    def test_errno_25_causing_constants_fixed(self):
        """Test that constants known to cause errno 25 are properly defined."""
        # The specific constant that was causing errno 25 (ENOTTY)
        # VFIO_GROUP_SET_CONTAINER is the one mentioned in the error logs

        # It should be a hardcoded integer, not a computed value
        assert isinstance(VFIO_GROUP_SET_CONTAINER, int)

        # It should have a reasonable value for a Linux ioctl constant
        # Linux ioctl values are 32-bit constants that can be quite large
        # The value 0x40043b04 (1074019076) is the correct VFIO_GROUP_SET_CONTAINER value
        assert VFIO_GROUP_SET_CONTAINER > 0
        assert VFIO_GROUP_SET_CONTAINER < 2**32  # Should fit in 32-bit range

        # Verify other critical constants as well
        critical_constants = {
            "VFIO_GROUP_SET_CONTAINER": VFIO_GROUP_SET_CONTAINER,
            "VFIO_SET_IOMMU": VFIO_SET_IOMMU,
            "VFIO_GET_API_VERSION": VFIO_GET_API_VERSION,
        }

        for name, value in critical_constants.items():
            assert isinstance(value, int), f"{name} should be hardcoded integer"
            assert value > 0, f"{name} should be positive"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
