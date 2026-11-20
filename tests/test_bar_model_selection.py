"""the implementation and controller make one model-selection decision.

Both must agree for plain dicts and TemplateObject-normalized contexts, empty
and missing models, other-BAR models, and integer or string keys. A controller
that instantiates pcileech_bar_impl_device without the module being generated
is an incomplete source set.
"""

import logging
import re
from copy import deepcopy

import pytest
from pcileechfwgenerator.device_clone.bar_model_loader import serialize_bar_model
from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator
from pcileechfwgenerator.templating.template_renderer import TemplateRenderer
from pcileechfwgenerator.utils.unified_context import ensure_template_compatibility

from tests.test_bar_storage_rtl import model_for

DEVICE_INST = re.compile(r"pcileech_bar_impl_device\s*#")


def _context(models, served=0, fallback=None):
    bar_config = {
        "served_bar_index": served,
        "aperture_size": 4096,
        "bars": [{"size": 4096}],
        "bar_models": models,
    }
    if fallback:
        bar_config["bar_fallback"] = fallback
    return {
        "header": "// selection",
        "device_signature": "fixture",
        "bar_config": bar_config,
    }


def _decisions(context):
    """(implementation generated, controller instantiates it, is read-only)."""
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))
    impl = generator._generate_bar_implementation(deepcopy(context))
    controller = generator._generate_bar_controller(deepcopy(context))
    readonly = impl is not None and "READ-ONLY FALLBACK" in impl
    return impl is not None, bool(DEVICE_INST.search(controller)), readonly


def _nonempty():
    return serialize_bar_model(model_for("rw"))


def _empty():
    data = _nonempty()
    data["regs"] = {}
    return data


CASES = {
    "int_key": (lambda: {0: _nonempty()}, 0, True),
    "str_key": (lambda: {"0": _nonempty()}, 0, True),
    "empty_regs": (lambda: {0: _empty()}, 0, False),
    "other_bar_only": (lambda: {0: _nonempty()}, 2, False),
    "served_bar_2": (lambda: {2: _nonempty()}, 2, True),
}


@pytest.mark.parametrize("fallback", [None, "readonly_zero", "generic_ram"])
@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("case", sorted(CASES))
def test_generator_and_controller_agree(case, normalized, fallback):
    build, served, learned = CASES[case]
    context = _context(build(), served, fallback)
    if normalized:
        # Production wraps bar_config in TemplateObject, which is always truthy.
        context = ensure_template_compatibility(context)
    impl, controller, readonly = _decisions(context)
    assert impl == controller
    if learned:
        assert (impl, readonly) == (True, False)
    elif fallback == "generic_ram":
        assert impl is False  # opt-out: board's writable zerowrite4k
    else:
        assert (impl, readonly) == (True, True)  # default: read-only device impl


@pytest.mark.parametrize(
    "fallback,expected", [(None, (True, True, True)), ("generic_ram", (False,) * 3)]
)
def test_no_models_fallback(fallback, expected):
    context = _context({}, fallback=fallback)
    del context["bar_config"]["bar_models"]
    assert _decisions(ensure_template_compatibility(context)) == expected


def test_writable_ram_only_when_opted_out():
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))
    default = generator._generate_bar_controller(_context({}))
    assert "pcileech_bar_impl_zerowrite4k" not in default
    opted = generator._generate_bar_controller(_context({}, fallback="generic_ram"))
    assert "pcileech_bar_impl_zerowrite4k" in opted


def test_unknown_fallback_mode_is_rejected():
    from pcileechfwgenerator.exceptions import PCILeechGenerationError

    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))
    with pytest.raises(PCILeechGenerationError, match="bar_fallback"):
        generator._generate_bar_implementation(_context({}, fallback="ram"))


def test_primary_bar_list_position_is_not_a_served_index():
    """primary_bar is a list position; only served_bar_index names a BAR."""
    context = _context({2: _nonempty()})
    del context["bar_config"]["served_bar_index"]
    context["bar_config"]["primary_bar"] = 2
    assert _decisions(context) == (True, True, True)  # BAR0 read-only, not BAR2


@pytest.mark.parametrize("served", [2, "2"])
def test_string_served_index_places_device_in_that_slot(served):
    context = _context({2: _nonempty()}, served=served)
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))
    assert generator._generate_bar_implementation(deepcopy(context)) is not None
    controller = generator._generate_bar_controller(deepcopy(context))
    assert re.search(
        r"pcileech_bar_impl_device\s*#.*?rd_req_bar\[2\]", controller, re.S
    )


@pytest.mark.parametrize("aperture", [16, 4096, 1 << 33, 1 << 34])
def test_readonly_fallback_builds_for_any_bar_size(aperture):
    """an 8/16 GiB 64-bit BAR must not break the default build."""
    context = _context({})
    context["bar_config"]["aperture_size"] = aperture
    generator = SVOverlayGenerator(TemplateRenderer(), logging.getLogger("t"))
    impl = generator._generate_bar_implementation(context)
    assert "READ-ONLY FALLBACK" in impl
    expected = min(aperture, 1 << 32)
    assert f"OFFSET_MASK = 32'h{((expected - 1) // 4) * 4:08X}" in impl
