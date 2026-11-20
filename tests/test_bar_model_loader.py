#!/usr/bin/env python3
"""Unit tests for BAR model loader."""

import json
import tempfile
from pathlib import Path

import pytest
from pcileechfwgenerator.device_clone.bar_model_loader import (
    BarModel,
    RegisterSpec,
    load_bar_model,
    save_bar_model,
)


class TestRegisterSpec:
    """Test RegisterSpec dataclass validation."""

    def test_valid_register_spec(self):
        """Test creating valid register spec."""
        reg = RegisterSpec(
            offset=0x100, width=4, reset=0xDEADBEEF, rw_mask=0xFFFFFFFF, hints={}
        )
        assert reg.offset == 0x100
        assert reg.width == 4
        assert reg.reset == 0xDEADBEEF

    def test_invalid_width(self):
        """Test that invalid widths raise ValueError."""
        with pytest.raises(ValueError, match="Invalid register width"):
            RegisterSpec(offset=0, width=8, reset=0, rw_mask=0, hints={})

    def test_reset_value_exceeds_width(self):
        """Test that reset value exceeding width raises ValueError."""
        with pytest.raises(ValueError, match="Reset value.*exceeds width"):
            RegisterSpec(offset=0, width=1, reset=0x1FF, rw_mask=0, hints={})

    def test_rw_mask_exceeds_width(self):
        """Test that RW mask exceeding width raises ValueError."""
        with pytest.raises(ValueError, match="RW mask.*exceeds width"):
            RegisterSpec(offset=0, width=2, reset=0, rw_mask=0x1FFFF, hints={})


class TestBarModel:
    """Test BarModel dataclass validation."""

    def test_valid_bar_model(self):
        """Test creating valid BAR model."""
        model = BarModel(
            size=4096,
            registers={
                0x0: RegisterSpec(0x0, 4, 0x10EC8168, 0x0, {}),
                0x4: RegisterSpec(0x4, 4, 0x00000080, 0xFFFFFFFF, {}),
            },
        )
        assert model.size == 4096
        assert len(model.registers) == 2

    def test_invalid_bar_size(self):
        """Test that invalid BAR size raises ValueError."""
        with pytest.raises(ValueError, match="BAR size must be positive"):
            BarModel(size=0, registers={})

    def test_register_offset_outside_bar(self):
        """Test that register offset outside BAR raises ValueError."""
        with pytest.raises(ValueError, match="offset.*outside BAR size"):
            BarModel(
                size=256,
                registers={
                    0x1000: RegisterSpec(0x1000, 4, 0, 0, {}),
                },
            )

    def test_register_exceeds_bar_boundary(self):
        """Test that register width exceeding BAR raises ValueError."""
        with pytest.raises(ValueError, match="exceeds BAR size"):
            BarModel(
                size=256,
                registers={
                    0xFE: RegisterSpec(0xFE, 4, 0, 0, {}),  # 0xFE + 4 = 0x102
                },
            )


class TestLoadBarModel:
    """Test load_bar_model function."""

    def test_load_valid_model(self):
        """Test loading a valid BAR model from JSON."""
        model_data = {
            "size": 4096,
            "regs": {
                "0x0": {"width": 4, "reset": 0x10EC8168, "rw_mask": 0x0, "hints": {}},
                "0x4": {
                    "width": 4,
                    "reset": 0x00000080,
                    "rw_mask": 0xFFFFFFFF,
                    "hints": {"maybe_rw1c": False},
                },
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(model_data, f)
            temp_path = Path(f.name)

        try:
            model = load_bar_model(temp_path)
            assert model.size == 4096
            assert len(model.registers) == 2
            assert model.registers[0x0].reset == 0x10EC8168
            assert model.registers[0x4].hints["maybe_rw1c"] is False
        finally:
            temp_path.unlink()

    def test_load_nonexistent_file(self):
        """Test that loading nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_bar_model(Path("/nonexistent/path/model.json"))

    def test_load_invalid_json(self):
        """Test that loading invalid JSON raises JSONDecodeError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json }")
            temp_path = Path(f.name)

        try:
            with pytest.raises(json.JSONDecodeError):
                load_bar_model(temp_path)
        finally:
            temp_path.unlink()

    def test_load_missing_size_key(self):
        """Test that missing 'size' key raises ValueError."""
        model_data = {"regs": {}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(model_data, f)
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="Missing required key: size"):
                load_bar_model(temp_path)
        finally:
            temp_path.unlink()

    def test_load_missing_regs_key(self):
        """Test that missing 'regs' key raises ValueError."""
        model_data = {"size": 4096}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(model_data, f)
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="Missing required key: regs"):
                load_bar_model(temp_path)
        finally:
            temp_path.unlink()

    def test_load_missing_register_field(self):
        """Test that missing register field raises ValueError."""
        model_data = {"size": 4096, "regs": {"0x0": {"width": 4, "reset": 0}}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(model_data, f)
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="Missing 'rw_mask'"):
                load_bar_model(temp_path)
        finally:
            temp_path.unlink()


class TestSaveBarModel:
    """Test save_bar_model function."""

    def test_save_and_reload(self):
        """Test saving a model and reloading it."""
        original = BarModel(
            size=4096,
            registers={
                0x0: RegisterSpec(0x0, 4, 0x10EC8168, 0x0, {}),
                0x50: RegisterSpec(0x50, 2, 0x1234, 0xFFFF, {"maybe_rw1c": True}),
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir) / "test_model.json"
            save_bar_model(original, temp_path)

            # Reload and verify
            loaded = load_bar_model(temp_path)
            assert loaded.size == original.size
            assert len(loaded.registers) == len(original.registers)
            assert loaded.registers[0x0].reset == 0x10EC8168
            assert loaded.registers[0x50].hints["maybe_rw1c"] is True

    def test_save_creates_parent_dirs(self):
        """Test that save creates parent directories if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_path = Path(tmpdir) / "subdir1" / "subdir2" / "model.json"

            model = BarModel(size=1024, registers={})
            save_bar_model(model, nested_path)

            assert nested_path.exists()
            assert nested_path.parent.is_dir()


@pytest.mark.parametrize("absent", [False, True])
def test_evidence_round_trip_preserves_initial_value_and_provenance(tmp_path, absent):
    from pcileechfwgenerator.device_clone.bar_model_loader import (
        deserialize_bar_model,
        serialize_bar_model,
    )
    from pcileechfwgenerator.device_clone.register_evidence import (
        RegisterClassification,
        RegisterRef,
    )
    from pcileechfwgenerator.device_clone.register_policy import compile_bar_model

    classification = RegisterClassification(
        ref=RegisterRef(
            space="mem_bar", bar_index=0, offset=0x14, width=2, field_mask=0xFFFF
        ),
        observed_initial_value=None if absent else 0x539A,
    )
    model = (
        _null_initial_model(0x14, 2)
        if absent
        else compile_bar_model([classification], 0, 4096).model
    )
    serialized = serialize_bar_model(model)
    assert serialized["regs"]["0x14"]["reset"] == classification.observed_initial_value
    decoded = deserialize_bar_model(serialized)
    path = tmp_path / "model.json"
    save_bar_model(model, path)
    for result in (decoded, load_bar_model(path)):
        register = result.registers[0x14]
        assert register.offset == 0x14
        assert register.reset == classification.observed_initial_value
        assert register.hints["unknown_mask"] == 0xFFFF
        assert register.hints["provenance_status"] == "evidence_backed"
        assert register.hints["evidence"] == model.registers[0x14].hints["evidence"]
        if absent:
            assert register.hints["initial_value_absent_reason"]
            assert serialize_bar_model(result)["regs"]["0x14"]["reset"] is None


@pytest.mark.parametrize("hints", [{}, {"initial_value_absent_reason": ""}])
def test_absent_initial_value_requires_reason_in_both_loaders(tmp_path, hints):
    from pcileechfwgenerator.device_clone.bar_model_loader import deserialize_bar_model

    data = {
        "size": 16,
        "regs": {"0x0": {"width": 1, "reset": None, "rw_mask": 0, "hints": hints}},
    }
    with pytest.raises(ValueError, match="requires a reason"):
        deserialize_bar_model(data)
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="requires a reason"):
        load_bar_model(path)


@pytest.mark.parametrize(
    "hints",
    [
        {},
        {"maybe_rw1c": False},
        {"maybe_rw1c": True, "evidence": {"classifications": [{"legacy": True}]}},
        {"evidence": {}},
        {"provenance_status": "measured"},
    ],
)
def test_legacy_cache_stays_loadable_but_unverified(tmp_path, hints):
    from pcileechfwgenerator.device_clone.bar_model_loader import (
        deserialize_bar_model,
        serialize_bar_model,
    )

    data = {
        "size": 16,
        "regs": {"0x4": {"width": 4, "reset": 0x9573, "rw_mask": 0xFF, "hints": hints}},
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data))
    for model in (deserialize_bar_model(data), load_bar_model(path)):
        assert model.registers[4].hints["provenance_status"] == "legacy_unverified"
        assert model.registers[4].offset == 4
        assert model.registers[4].reset == 0x9573
        assert model.registers[4].rw_mask == 0xFF
        assert (
            serialize_bar_model(model)["regs"]["0x4"]["hints"]["provenance_status"]
            == "legacy_unverified"
        )
    # Loading must not mutate the supplied dictionary.
    assert data["regs"]["0x4"]["hints"] == hints


@pytest.mark.parametrize(
    "registers, message",
    [
        ({0: RegisterSpec(4, 1, 0, 0, {})}, "key and offset"),
        ({2: RegisterSpec(2, 4, 0, 0, {})}, "aligned"),
        (
            {0: RegisterSpec(0, 4, 0, 0, {}), 2: RegisterSpec(2, 2, 0, 0, {})},
            "Overlapping",
        ),
    ],
)
def test_invalid_register_layout_is_rejected(registers, message):
    with pytest.raises(ValueError, match=message):
        BarModel(16, registers)


@pytest.mark.parametrize("field", ["reset", "rw_mask"])
def test_negative_register_bits_rejected(field):
    values = dict(offset=0, width=4, reset=0, rw_mask=0, hints={})
    values[field] = -1
    with pytest.raises(ValueError):
        RegisterSpec(**values)


def test_legacy_decimal_string_size_remains_loadable(tmp_path):
    from pcileechfwgenerator.device_clone.bar_model_loader import deserialize_bar_model

    data = {"size": "4096", "regs": {}}
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data))
    assert load_bar_model(path).size == 4096
    assert deserialize_bar_model(data).size == 4096


def _null_initial_model(offset, width):
    """compile_bar_model omits registers without an initial value; build one
    directly so the defensive loader/template guards stay covered."""
    from pcileechfwgenerator.device_clone.bar_model_loader import BarModel
    from pcileechfwgenerator.device_clone.register_policy import EvidenceRegisterSpec

    reason = "No successful read or explicit reset value"
    return BarModel(
        size=4096,
        registers={
            offset: EvidenceRegisterSpec(
                offset=offset,
                width=width,
                reset=None,
                rw_mask=0,
                hints={
                    "evidence": {
                        "classifications": [{"observed_initial_value": None}],
                        "state_epoch": None,
                        "initial_value_source": None,
                    },
                    "initial_value_absent_reason": reason,
                    "unknown_mask": (1 << (8 * width)) - 1,
                    "unsupported": [],
                },
            )
        },
    )
