"""Controller renders the device BAR impl at the donor's real BAR index (gap C).

The PCILeech BAR controller historically hard-wired the device register
implementation to slot 0 (i_bar0, hit bit 0). For donors whose primary register
BAR is not index 0, the device impl must be gated on rd_req_bar[N]/wr_bar[N] for
the served donor index N, and every other slot must be a non-responding stub
(plus loopaddr at slot 1 when free). A 64-bit served BAR consumes slot N+1, so
that partner slot must be `none` — never `loopaddr` — to avoid double-driving
the response mux.
"""
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.templating.template_renderer import TemplateRenderer

TEMPLATE = "sv/pcileech_tlps128_bar_controller.sv.j2"


def _ctx(served_index, *, is_64bit=False, aperture=65536, with_model=True):
    bar_config = {
        "served_bar_index": served_index,
        "served_is_64bit": is_64bit,
        "aperture_size": aperture,
        "primary_bar": 0,
        "bars": [{"size": aperture}],
    }
    if with_model:
        bar_config["bar_models"] = {str(served_index): {"size": aperture}}
    return {
        "header": "// test",
        "device_signature": "1234:5678",
        "bar_config": bar_config,
    }


def _device_inst_slot(rendered):
    """Return the BAR slot index the device/zerowrite register impl is gated on."""
    # The served impl is pcileech_bar_impl_device (model) or _zerowrite4k.
    m = re.search(
        r"pcileech_bar_impl_(?:device|zerowrite4k)\b.*?rd_req_bar\[(\d+)\]",
        rendered,
        re.DOTALL,
    )
    return int(m.group(1)) if m else None


def _impl_for_slot(rendered, k):
    """Return the impl module name instantiated for rd_req_bar[k]."""
    m = re.search(
        r"(pcileech_bar_impl_\w+)\b[^;]*?rd_req_bar\[" + str(k) + r"\]",
        rendered,
        re.DOTALL,
    )
    return m.group(1) if m else None


@pytest.fixture
def renderer():
    return TemplateRenderer()


class TestControllerIndexServing:
    def test_served_index_2_gates_device_on_bit_2(self, renderer):
        out = renderer.render_template(TEMPLATE, _ctx(2))
        assert _device_inst_slot(out) == 2
        # slot 0 must be a non-responding stub now (not the device impl)
        assert _impl_for_slot(out, 0) == "pcileech_bar_impl_none"

    def test_served_index_2_uses_aperture_for_bar_size(self, renderer):
        out = renderer.render_template(TEMPLATE, _ctx(2, aperture=65536))
        assert re.search(r"BAR_SIZE\s*\(\s*65536\s*\)", out)

    def test_index_0_backcompat_device_on_bit0_loopaddr_on_bit1(self, renderer):
        out = renderer.render_template(TEMPLATE, _ctx(0))
        assert _device_inst_slot(out) == 0
        assert _impl_for_slot(out, 1) == "pcileech_bar_impl_loopaddr"
        assert _impl_for_slot(out, 2) == "pcileech_bar_impl_none"

    def test_64bit_served_at_0_makes_partner_slot1_none_not_loopaddr(self, renderer):
        # Latent-bug fix: a 64-bit BAR0 lights hit bits 0 and 1; slot 1 must not
        # respond, so it must be `none`, not the default loopaddr.
        out = renderer.render_template(TEMPLATE, _ctx(0, is_64bit=True))
        assert _impl_for_slot(out, 1) == "pcileech_bar_impl_none"

    def test_64bit_served_at_2_makes_partner_slot3_none(self, renderer):
        out = renderer.render_template(TEMPLATE, _ctx(2, is_64bit=True))
        assert _impl_for_slot(out, 3) == "pcileech_bar_impl_none"

    def test_served_index_1_replaces_loopaddr(self, renderer):
        # Slot 1 is the only slot with a non-`none` default (loopaddr); when the
        # served BAR IS index 1, the device impl must take it and loopaddr must
        # not also be emitted there (single driver).
        out = renderer.render_template(TEMPLATE, _ctx(1))
        assert _device_inst_slot(out) == 1
        assert _impl_for_slot(out, 1) != "pcileech_bar_impl_loopaddr"
        assert "pcileech_bar_impl_loopaddr" not in out  # slot 1 was its only home

    def test_64bit_served_index_1_makes_partner_slot2_none(self, renderer):
        out = renderer.render_template(TEMPLATE, _ctx(1, is_64bit=True))
        assert _device_inst_slot(out) == 1
        assert _impl_for_slot(out, 2) == "pcileech_bar_impl_none"

    def test_all_seven_slots_present_exactly_once(self, renderer):
        out = renderer.render_template(TEMPLATE, _ctx(2))
        for k in range(7):
            assert len(re.findall(r"rd_req_bar\[" + str(k) + r"\]", out)) == 1
