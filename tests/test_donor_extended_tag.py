"""DevCap Extended Tag reaches the requester policy from the donor config space.

Without this, Stage 2 fails because nothing copies the capability
processor's ``pcie_extended_tag_supported`` into the template context, so the
requester policy saw None.
"""

import pytest
from pcileechfwgenerator.device_clone.pcileech_context import (
    pcie_caps_from_device_context,
)
from pcileechfwgenerator.pci_capability import core, processor
from pcileechfwgenerator.vivado_handling.requester_plan import (
    REQUESTER_SOURCE,
    requester_policy_from_result,
)

PCIE_CAP = 0x40


def _config_hex(devcap: int) -> str:
    """Type 0 header (I350 IDs) with one PCI Express capability at 0x40."""
    cfg = bytearray(256)
    cfg[0:4] = (0x8086).to_bytes(2, "little") + (0x1521).to_bytes(2, "little")
    cfg[0x06] = 0x10  # Status: capabilities list
    cfg[0x0B] = 0x02  # class: network
    cfg[0x34] = PCIE_CAP
    cfg[PCIE_CAP] = 0x10  # PCI Express capability ID, next = 0
    cfg[PCIE_CAP + 2 : PCIE_CAP + 4] = (0x0002).to_bytes(2, "little")  # v2 endpoint
    cfg[PCIE_CAP + 4 : PCIE_CAP + 8] = devcap.to_bytes(4, "little")
    cfg[PCIE_CAP + 0x0C : PCIE_CAP + 0x10] = (0x00000011).to_bytes(4, "little")
    return cfg.hex()


def _caps(devcap: int) -> dict:
    cp = processor.CapabilityProcessor(core.ConfigSpace(_config_hex(devcap)))
    return pcie_caps_from_device_context(cp._get_device_context())


@pytest.mark.parametrize("devcap,expected", [(0x00000020, True), (0x00000000, False)])
def test_devcap_extended_tag_bit_reaches_context(devcap, expected):
    assert _caps(devcap)["pcie_extended_tag_supported"] is expected


def test_absent_pcie_capability_reports_nothing():
    assert "pcie_extended_tag_supported" not in pcie_caps_from_device_context({})


def _result(extended_tag):
    context = {
        "device_config": {"cpl_timeout_ranges": "ABCD", "cpl_timeout_disable_sup": True}
    }
    if extended_tag is not None:
        context["pcie_extended_tag_supported"] = extended_tag
    return {"template_context": context}


def test_requester_policy_refuses_missing_extended_tag(tmp_path):
    with pytest.raises(ValueError, match="Extended Tag"):
        requester_policy_from_result(
            tmp_path, _result(None), sources=[REQUESTER_SOURCE]
        )


def test_requester_policy_takes_donor_extended_tag(tmp_path):
    policy = requester_policy_from_result(
        tmp_path, _result(True), sources=[REQUESTER_SOURCE]
    )
    assert policy.extended_tag_supported is True
