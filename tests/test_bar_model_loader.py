#!/usr/bin/env python3
"""Unit tests for BAR model loader."""

import json
import tempfile
from pathlib import Path

import pytest

from src.device_clone.bar_model_loader import (
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

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
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
                0x50: RegisterSpec(
                    0x50, 2, 0x1234, 0xFFFF, {"maybe_rw1c": True}
                ),
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
