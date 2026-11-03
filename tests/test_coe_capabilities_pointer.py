"""The standard-header Capabilities Pointer must land in byte 0x34.

COE DWORDs are the big-endian text of a little-endian config-space DWORD, so
the byte at offset 0x34 (the Capabilities Pointer) is the DWORD's least
significant byte. Emitting it as the most significant byte leaves 0x34 = 0x00,
which tells the host the device has no capability list at all.
"""

from tests.test_coe_error_injection import (
    _minimal_base_context,
    _render_cfgspace_coe,
)
from tests.test_extended_config_coe import _parse_coe_dwords

# Matches CAP_OFFSET_PM in pcileech_cfgspace.coe.j2 (first capability = PM).
CAP_PTR_OFFSET = 0x34
FIRST_CAP_OFFSET = 0x40


def test_capabilities_pointer_in_byte_0x34():
    coe = _render_cfgspace_coe(_minimal_base_context())
    dwords = _parse_coe_dwords(coe)

    dword = dwords[CAP_PTR_OFFSET // 4]
    # Byte 0x34 is the DWORD's LSB; bytes 0x35-0x37 are reserved (0).
    assert dword & 0xFF == FIRST_CAP_OFFSET, (
        f"byte 0x34 = 0x{dword & 0xFF:02X}, expected 0x{FIRST_CAP_OFFSET:02X}; "
        f"full DWORD 0x{dword:08X}"
    )
    assert dword == FIRST_CAP_OFFSET, (
        f"cap-pointer DWORD 0x{dword:08X}, expected 0x{FIRST_CAP_OFFSET:08X}"
    )
