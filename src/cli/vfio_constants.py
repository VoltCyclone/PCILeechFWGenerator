#!/usr/bin/env python3
"""VFIO Constants Module
Provides kernel-compatible VFIO constants and structures.
"""

import ctypes

# VFIO IOCTL command generation (Linux kernel style)
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(dir, type, nr, size):
    return (
        (dir << _IOC_DIRSHIFT)
        | (type << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IO(type, nr):
    return _IOC(_IOC_NONE, type, nr, 0)


def _IOR(type, nr, size):
    return _IOC(_IOC_READ, type, nr, size)


def _IOW(type, nr, size):
    return _IOC(_IOC_WRITE, type, nr, size)


def _IOWR(type, nr, size):
    return _IOC(_IOC_READ | _IOC_WRITE, type, nr, size)


# VFIO magic number
VFIO_TYPE = ord(";")

# VFIO constants
VFIO_DEVICE_NAME_MAX_LENGTH = 256  # Maximum device name length in VFIO, defined in Linux kernel headers (e.g., include/uapi/linux/vfio.h)
VFIO_TYPE1_IOMMU = 1


# VFIO structures (defined early to avoid forward reference issues)
class vfio_group_status(ctypes.Structure):
    _fields_ = [
        ("argsz", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class vfio_region_info(ctypes.Structure):
    _fields_ = [
        ("argsz", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("index", ctypes.c_uint32),
        ("cap_offset", ctypes.c_uint32),
        ("size", ctypes.c_uint64),
        ("offset", ctypes.c_uint64),
    ]


# VFIO API and extension IOCTLs
VFIO_GET_API_VERSION = _IO(VFIO_TYPE, 0)
VFIO_CHECK_EXTENSION = _IOW(VFIO_TYPE, 1, ctypes.sizeof(ctypes.c_int))

# VFIO Container IOCTLs
VFIO_SET_IOMMU = _IOW(VFIO_TYPE, 2, ctypes.sizeof(ctypes.c_int))

# VFIO Group IOCTLs
# Use the actual struct size for group status (vfio_group_status)
VFIO_GROUP_GET_STATUS = _IOR(VFIO_TYPE, 3, ctypes.sizeof(vfio_group_status))
VFIO_GROUP_SET_CONTAINER = _IOW(VFIO_TYPE, 4, ctypes.sizeof(ctypes.c_int))
# The VFIO_GROUP_GET_DEVICE_FD ioctl does not encode a write buffer size in the
# ioctl number; it is an _IO() command. Using _IOW() with an artificial size
# produced a mismatched ioctl number and caused ENOTTY (Inappropriate ioctl)
# errors on some kernels. Use _IO to match the kernel uapi definition.
VFIO_GROUP_GET_DEVICE_FD = _IO(VFIO_TYPE, 6)

# VFIO Device IOCTLs
VFIO_DEVICE_GET_REGION_INFO = _IOWR(
    VFIO_TYPE, 8, ctypes.sizeof(vfio_region_info)
)  # vfio_region_info size

# VFIO Region flags
VFIO_REGION_INFO_FLAG_READ = 1 << 0
VFIO_REGION_INFO_FLAG_WRITE = 1 << 1
VFIO_REGION_INFO_FLAG_MMAP = 1 << 2

# VFIO Group flags
VFIO_GROUP_FLAGS_VIABLE = 1 << 0
VFIO_GROUP_FLAGS_CONTAINER_SET = 1 << 1


# Legacy aliases for compatibility with existing code
VfioGroupStatus = vfio_group_status
VfioRegionInfo = vfio_region_info

# Export all the constants that vfio_handler.py expects
__all__ = [
    "VFIO_GET_API_VERSION",
    "VFIO_CHECK_EXTENSION",
    "VFIO_GROUP_GET_STATUS",
    "VFIO_GROUP_SET_CONTAINER",
    "VFIO_GROUP_GET_DEVICE_FD",
    "VFIO_SET_IOMMU",
    "VFIO_DEVICE_GET_REGION_INFO",
    "VFIO_TYPE1_IOMMU",
    "VFIO_DEVICE_NAME_MAX_LENGTH",
    "VFIO_REGION_INFO_FLAG_READ",
    "VFIO_REGION_INFO_FLAG_WRITE",
    "VFIO_REGION_INFO_FLAG_MMAP",
    "VFIO_GROUP_FLAGS_VIABLE",
    "VFIO_GROUP_FLAGS_CONTAINER_SET",
    "vfio_group_status",
    "vfio_region_info",
    "VfioGroupStatus",
    "VfioRegionInfo",
]
