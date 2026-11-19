"""Regression coverage for final-image writemasks and strict donor inputs."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pcileechfwgenerator.device_clone.coe_word_order import logical_coe_to_bram
from pcileechfwgenerator.device_clone.writemask_generator import WritemaskGenerator
from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator
from pcileechfwgenerator.templating.template_renderer import TemplateRenderError

from tests.test_extended_config import _i350_donor_hex
from tests.test_extended_config_coe import _render


@pytest.fixture
def rendered_coe():
    """Render the I350 donor through the actual configuration template."""
    return _render(_i350_donor_hex())


@pytest.fixture
def image(rendered_coe, tmp_path):
    """Recover logical little-endian bytes from the final configuration COE."""
    cfg_path = tmp_path / "cfg.coe"
    cfg_path.write_text(rendered_coe, encoding="utf-8")
    words = WritemaskGenerator().read_cfg_space(cfg_path)
    return b"".join(words[i].to_bytes(4, "little") for i in range(1024))


@pytest.fixture
def overlay():
    """Use the real overlay conversion and writemask generator."""
    return SVOverlayGenerator(None, logging.getLogger(__name__))


def test_raw_data_equivalent_to_bytes(overlay, image):
    """A raw_data dictionary must preserve the same capabilities as bytes."""
    expected = overlay._generate_writemask_coe({"config_space": image})
    for payload in ({"raw_data": image.hex()}, {"data": image}, bytearray(image)):
        assert overlay._generate_writemask_coe({"config_space": payload}) == expected


@pytest.mark.parametrize(
    "representation",
    ["hex_data", "bytearray_data", "raw_object", "data_object", "both", "hex_space"],
)
def test_supported_payload_representations(overlay, image, representation):
    """Structured bytes, hex, and attribute-based inputs normalize equally."""
    payloads = {
        "hex_data": {"data": image.hex()},
        "bytearray_data": {"data": bytearray(image)},
        "raw_object": SimpleNamespace(raw_data=image.hex()),
        "data_object": SimpleNamespace(data=image),
        "both": {"raw_data": image.hex(), "data": image},
        "hex_space": {"raw_data": " 0x\n" + image.hex(" ") + "\t"},
    }
    expected = overlay._generate_writemask_coe({"config_space": image})
    assert (
        overlay._generate_writemask_coe({"config_space": payloads[representation]})
        == expected
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"raw_data": ""},
        {"raw_data": "zz"},
        {"raw_data": "abc"},
        12345,
        "not-a-dict",
        bytes(8192),
        {"raw_data": "00" * 64, "data": bytes([1]) * 64},
        bytes(60),
        bytes(65),
        b"",
        None,
        {"data": "zz"},
        {"data": [0] * 64},
        {"raw_data": bytes(64)},
        SimpleNamespace(),
        SimpleNamespace(raw_data="00" * 64, data=bytes([1]) * 64),
    ],
    ids=[
        "missing",
        "empty-hex",
        "malformed-hex",
        "odd-hex",
        "integer",
        "string",
        "oversized",
        "conflicting",
        "short",
        "unaligned",
        "empty-bytes",
        "none",
        "malformed-data",
        "list-data",
        "nonhex-raw-data",
        "empty-object",
        "conflicting-object",
    ],
)
def test_invalid_payloads_raise(overlay, payload, caplog):
    """Invalid payloads must fail visibly instead of generating a zero image."""
    with caplog.at_level(logging.ERROR):
        with pytest.raises(TemplateRenderError, match="config_space"):
            overlay._generate_writemask_coe({"config_space": payload})
    assert caplog.records


@pytest.mark.parametrize("size", [64, 256, 4092])
def test_short_aligned_payload_is_zero_padded(overlay, image, size):
    """Only valid DWORD-aligned payloads may omit the end of configuration space."""
    payload = image[:size]
    assert overlay._generate_writemask_coe(
        {"config_space": {"raw_data": payload.hex()}}
    ) == overlay._generate_writemask_coe({"config_space": payload.ljust(4096, b"\x00")})


def test_rendered_coe_matches_public_generator(overlay, rendered_coe, tmp_path):
    """Rendered COE takes precedence even when the context payload is invalid."""
    cfg_path = tmp_path / "cfg.coe"
    wm_path = tmp_path / "mask.coe"
    cfg_path.write_text(rendered_coe, encoding="utf-8")
    WritemaskGenerator().generate_writemask(cfg_path, wm_path)
    assert overlay._generate_writemask_coe(
        {"config_space": {"raw_data": "invalid"}}, config_space_coe=rendered_coe
    ) == wm_path.read_text(encoding="utf-8")


def test_raw_data_preserves_capability_policy(overlay, image, tmp_path):
    """The donor capability region must not acquire the all-zero image policy."""
    generator = WritemaskGenerator()
    masks = []
    for payload in ({"raw_data": image.hex()}, bytes(4096)):
        mask_path = tmp_path / "mask.coe"
        mask_path.write_text(
            overlay._generate_writemask_coe({"config_space": payload}),
            encoding="utf-8",
        )
        masks.append(generator.read_cfg_space(mask_path))
    assert any(masks[0][i] != masks[1][i] for i in range(0x40 // 4, 0x100 // 4))


def test_overlay_uses_exact_rendered_coe(overlay, rendered_coe, tmp_path, monkeypatch):
    """The mask input must be the emitted COE, even if the donor image differs."""
    overlay.renderer = Mock()
    overlay.renderer.render_template.return_value = rendered_coe
    original = WritemaskGenerator.generate_writemask
    cfg_inputs = []

    def capture_input(self, cfg_space_path, output_path, device_config=None):
        cfg_inputs.append(cfg_space_path.read_text(encoding="utf-8"))
        return original(self, cfg_space_path, output_path, device_config)

    monkeypatch.setattr(WritemaskGenerator, "generate_writemask", capture_input)
    result = overlay.generate_config_space_overlay(
        {
            "device_config": {"vendor_id": "0x8086", "device_id": "0x1521"},
            "config_space": bytes(4096),
        }
    )
    assert cfg_inputs == [logical_coe_to_bram(rendered_coe)]
    cfg_path = tmp_path / "cfg.coe"
    wm_path = tmp_path / "mask.coe"
    cfg_path.write_text(result["pcileech_cfgspace.coe"], encoding="utf-8")
    original(WritemaskGenerator(), cfg_path, wm_path)
    assert result["pcileech_cfgspace_writemask.coe"] == wm_path.read_text(
        encoding="utf-8"
    )


def test_raw_donor_fallback_leaves_bars_and_rom_read_only():
    import logging

    from pcileechfwgenerator.templating.sv_overlay_generator import SVOverlayGenerator

    image = bytearray(256)
    image[0:4] = (0x15218086).to_bytes(4, "little")
    image[0x10:0x14] = (0xF7C00000).to_bytes(4, "little")  # assigned base, 32-bit mem
    image[0x18:0x1C] = (0xF7E0000C).to_bytes(4, "little")  # assigned base, 64-bit mem
    image[0x1C:0x20] = (0x00000000).to_bytes(4, "little")
    image[0x30:0x34] = (0xF7F00001).to_bytes(4, "little")  # ROM base + enable
    overlay = SVOverlayGenerator(renderer=None, logger=logging.getLogger("t"))
    text = overlay._generate_writemask_coe({"config_space": bytes(image)})
    body = text.split("memory_initialization_vector=", 1)[1]
    tokens = body.replace(";", "").replace("\n", "").split(",")
    words = [int(t, 16) for t in tokens if t]
    assert words[0x10 // 4 : 0x28 // 4] == [0] * 6
    assert words[0x30 // 4] == 0
