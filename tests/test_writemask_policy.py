"""Literal policy and masked-write regressions for the final PCI config image."""

from itertools import product

import pytest
from pcileechfwgenerator.device_clone.writemask_generator import WritemaskGenerator

from tests.test_extended_config import _i350_donor_hex
from tests.test_extended_config_coe import _render


def _image(*capabilities):
    """Build a DWORD map from (byte offset, ID, control, payload) entries."""
    image = {i: 0 for i in range(1024)}
    image[0] = 0x15218086
    image[2] = 0x02000001
    image[11] = 0x00018086
    if capabilities:
        image[13] = capabilities[0][0]
    for i, (offset, cap_id, control, payload) in enumerate(capabilities):
        next_ptr = capabilities[i + 1][0] if i + 1 < len(capabilities) else 0
        image[offset // 4] = control << 16 | next_ptr << 8 | cap_id
        for j, word in enumerate(payload, 1):
            image[offset // 4 + j] = word
    return image


@pytest.fixture
def generate(tmp_path):
    def run(image, device_config=None):
        cfg = tmp_path / "cfg.coe"
        out = tmp_path / "mask.coe"
        cfg.write_text(
            "memory_initialization_radix=16;\nmemory_initialization_vector=\n"
            + ",".join(f"{image.get(i, 0):08x}" for i in range(1024))
            + ";\n"
        )
        generator = WritemaskGenerator()
        generator.generate_writemask(cfg, out, device_config=device_config)
        words = generator.read_cfg_space(out)
        assert len(words) == 1024
        return [words[i] for i in range(1024)]

    return run


def _write(old, data, mask, be):
    lanes = sum(0xFF << (8 * lane) for lane in range(4) if be & (1 << lane))
    m = mask & lanes
    return (old & ~m) | (data & m)


def test_header_and_reserved_default_read_only(generate):
    mask = generate(_image())
    assert mask[:16] == [
        0,
        0x00000546,
        0,
        0x000000FF,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0x000000FF,
    ]
    assert mask[16:] == [0] * 1008


def test_immutable_fields_survive_every_byte_enable(generate):
    image = _image((0x40, 0x11, 0x8007, [0x00002004, 0x00003004]))
    mask = generate(image)
    for offset in (0x00, 0x08, 0x2C, 0x34, 0x44, 0x48):
        for data, be in product((0, 0xFFFFFFFF, 0xAAAAAAAA, 0x55555555), range(16)):
            i = offset // 4
            assert _write(image[i], data, mask[i], be) == image[i]
    for data, be in product((0, 0xFFFFFFFF, 0xAAAAAAAA, 0x55555555), range(16)):
        assert _write(image[16], data, mask[16], be) == _write(
            image[16], data, 0xC0000000, be
        )


@pytest.mark.parametrize(
    "is64,pvm,enabled,mmc",
    product((False, True), (False, True), (False, True), (0, 3, 5)),
)
def test_msi_layout_ignores_enable_and_hints(generate, is64, pvm, enabled, mmc):
    control = (is64 << 7) | (pvm << 8) | (mmc << 1) | enabled
    image = _image((0x40, 0x05, control, []))
    expected = [0x00710000, 0xFFFFFFFC]
    if is64:
        expected += [0xFFFFFFFF]
    expected += [0x0000FFFF]
    if pvm:
        expected += [{0: 0x00000001, 3: 0x000000FF, 5: 0xFFFFFFFF}[mmc], 0]
    hints = {"msi_config": {"enabled": not enabled, "64bit_capable": not is64}}
    mask = generate(image, hints)
    assert mask[16 : 16 + len(expected)] == expected
    assert mask[16 + len(expected) :] == [0] * (1008 - len(expected))
    helper = WritemaskGenerator().get_msi_writemask(
        {
            "enabled": enabled,
            "64bit_capable": is64,
            "per_vector_masking": pvm,
            "multiple_message_capable": mmc,
        }
    )
    assert tuple(int(word, 16) for word in helper) == tuple(expected)


@pytest.mark.parametrize("table_size", (1, 8, 64, 2048))
def test_msix_has_three_dwords_and_preserves_following_pm(generate, table_size):
    image = _image(
        (0x40, 0x11, table_size - 1, [0x2000, 0x3000]), (0x4C, 0x01, 0, [0xFFFFFFFF])
    )
    mask = generate(image)
    assert mask[16:22] == [0xC0000000, 0, 0, 0, 0x00000103, 0]
    assert WritemaskGenerator().get_msix_writemask({"table_size": table_size}) == (
        "c0000000",
        "00000000",
        "00000000",
    )


@pytest.mark.parametrize("slot", (0, 2, 4))
def test_mem32_served_bar_at_any_slot(generate, slot):
    image = _image()
    image[4 + slot] = 0xFFFFE008
    expected = [0] * 6
    expected[slot] = 0xFFFFE000
    mask = generate(image)
    assert mask[4:10] == expected
    assert mask[1] == 0x00000546


@pytest.mark.parametrize("slot,upper", product((0, 4), (0, 0xFFFFFFFF)))
def test_mem64_upper_half_is_size_encoding(generate, slot, upper):
    image = _image()
    image[4 + slot] = 0xFFFFC004
    image[5 + slot] = upper
    expected = [0] * 6
    expected[slot : slot + 2] = [0xFFFFC000, upper]
    mask = generate(image)
    assert mask[4:10] == expected
    assert mask[1] == 0x00000546


def test_io_bar_enables_command_io(generate):
    image = _image()
    image[6] = 0xFFFFFF01
    mask = generate(image)
    assert mask[4:10] == [0, 0, 0xFFFFFF00, 0, 0, 0]
    assert mask[1] == 0x00000547


def test_mem64_in_last_slot_is_read_only(generate, caplog):
    image = _image()
    image[9] = 0xFFFFC004
    assert generate(image)[4:10] == [0] * 6
    assert any(record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.parametrize(
    "word,expected", ((0, 0), (0xFFF80000, 0xFFF80001), (0xFFF807FF, 0xFFF80001))
)
def test_rom_aperture(generate, word, expected):
    image = _image()
    image[12] = word
    assert generate(image)[12] == expected


@pytest.mark.parametrize(
    "devcap2,expected",
    (
        (0, 0),
        (1, 0xF),
        (8, 0xF),
        (0x10, 0x10),
        (0x800, 0x400),
        (0x400, 0),
        (0x81F, 0x41F),
        (0xFFFFFFFF, 0x41F),
    ),
)
def test_pcie_control_masks_and_devcap2_gating(generate, devcap2, expected):
    image = _image((0x40, 0x10, 2, [0xFFFFFFFF] * 14))
    image[(0x40 + 0x24) // 4] = devcap2
    assert generate(image)[16:31] == [
        0,
        0,
        0x00007FFF,
        0,
        0x000000CB,
        0,
        0,
        0,
        0,
        0,
        expected,
        0,
        0,
        0,
        0,
    ]


def test_pcie_clips_at_standard_space_boundary(generate):
    image = _image((0xF4, 0x10, 2, [0xFFFFFFFF] * 14))
    mask = generate(image)
    assert mask[61:64] == [0, 0, 0x00007FFF]
    assert mask[64:] == [0] * 960


@pytest.mark.parametrize("cap_id", (0x03, 0x09, 0x7F))
def test_unknown_and_side_effect_capabilities_are_read_only(generate, cap_id):
    image = _image((0x40, cap_id, 0xFFFF, [0xFFFFFFFF] * 8))
    assert generate(image)[16:] == [0] * 1008


def test_pure_builder_and_rendering_match_file_api(generate, tmp_path):
    generator = WritemaskGenerator()
    image = _image((0x40, 0x11, 7, [0x2000, 0x3000]))
    before = image.copy()
    built = generator.build_writemask(image)
    assert len(built) == 1024
    assert all(type(word) is int for word in built)
    assert built == generate(image)
    assert image == before
    coe = generator.render_writemask_coe(image)
    assert "; word_order=bram_msb_first" in coe
    # BRAM order: Command mask 0x00000546 -> bytes 46 05 00 00 (byte +0 MSB);
    # Cache Line Size 0x000000ff -> ff000000.
    assert "00000000,46050000,00000000,ff000000," in coe
    path = tmp_path / "rendered.coe"
    path.write_text(coe)
    assert list(generator.read_cfg_space(path).values()) == built


def test_i350_final_image_policy(generate, tmp_path):
    cfg = tmp_path / "i350.coe"
    cfg.write_text(_render(_i350_donor_hex()))
    generator = WritemaskGenerator()
    image = generator.read_cfg_space(cfg)
    mask = generate(image)
    assert [mask[i] for i in (0, 1, 2, 3, 11, 13, 15)] == [
        0,
        0x546,
        0,
        0xFF,
        0,
        0,
        0xFF,
    ]
    caps = generator.locate_capabilities(image)
    known_words = set()
    for cap_id, offset in caps.items():
        start = offset // 4
        if offset >= 0x100:
            assert mask[start] == 0
            continue
        if cap_id == "0x01":
            expected = [0, 0x103]
        elif cap_id == "0x11":
            expected = [0xC0000000, 0, 0]
        elif cap_id == "0x10":
            devcap2 = image[start + 9]
            gated = 0xF if devcap2 & 0xF else 0
            gated |= 0x10 if devcap2 & 0x10 else 0
            gated |= 0x400 if devcap2 & 0x800 else 0
            expected = [0, 0, 0x7FFF, 0, 0xCB, 0, 0, 0, 0, 0, gated, 0, 0, 0, 0]
        else:
            assert mask[start] == 0
            continue
        assert mask[start : start + len(expected)] == expected
        known_words.update(range(start, start + len(expected)))
    assert {"0x01", "0x10", "0x11"} <= caps.keys()
    assert all(mask[i] == 0 for i in range(16, 64) if i not in known_words)
    assert mask[64:] == [0] * 960


def test_pcie_v1_capability_is_nine_dwords_and_ignores_devcap2(generate):
    # PCIe v1 at 0x60 (ends +0x20), PM placed right after at 0x84.
    image = _image(
        (0x60, 0x10, 0x0001, [0] * 8),
        (0x84, 0x01, 0x0003, [0]),
    )
    mask = generate(image)
    assert mask[0x68 // 4] == 0x00007FFF
    assert mask[0x70 // 4] == 0x000000CB
    assert mask[0x84 // 4 : 0x84 // 4 + 2] == [0, 0x00000103]


def test_capability_grants_do_not_spill_into_next_capability(generate):
    # 64-bit MSI is 4 DWORDs; a PM capability placed at +8 must keep its policy.
    image = _image(
        (0x48, 0x05, 0x0080, [0]),
        (0x50, 0x01, 0x0003, [0]),
    )
    mask = generate(image)
    assert mask[0x48 // 4 : 0x50 // 4] == [0x00710000, 0xFFFFFFFC]
    assert mask[0x50 // 4 : 0x58 // 4] == [0, 0x00000103]


def test_reserved_pointer_low_bits_are_ignored(generate):
    image = _image((0x40, 0x01, 0x0003, [0]))
    image[13] = 0x43  # cap pointer with reserved bits set
    assert generate(image)[0x44 // 4] == 0x00000103
