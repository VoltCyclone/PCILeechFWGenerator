#!/usr/bin/env python3
"""BAR register model data structures and persistence.

Defines the schema for learned BAR register models and provides
load/save functionality with validation.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from src.log_config import get_logger
from src.string_utils import log_error_safe, log_info_safe, safe_format

logger = get_logger(__name__)


@dataclass
class RegisterSpec:
    """Specification for a single register in a BAR."""

    offset: int  # Byte offset within BAR
    width: int  # Register width in bytes (1, 2, or 4)
    reset: int  # Default/reset value
    rw_mask: int  # Bits that are writable (1 = RW, 0 = RO)
    hints: Dict[str, Any]  # Additional metadata (e.g., {"maybe_rw1c": bool})

    def __post_init__(self):
        """Validate register spec after construction."""
        if self.width not in (1, 2, 4):
            raise ValueError(
                safe_format(
                    "Invalid register width {width} at offset 0x{offset:X}",
                    width=self.width,
                    offset=self.offset,
                )
            )

        max_val = (1 << (8 * self.width)) - 1
        if self.reset > max_val:
            raise ValueError(
                safe_format(
                    "Reset value 0x{reset:X} exceeds width {width} at 0x{offset:X}",
                    reset=self.reset,
                    width=self.width,
                    offset=self.offset,
                )
            )

        if self.rw_mask > max_val:
            raise ValueError(
                safe_format(
                    "RW mask 0x{mask:X} exceeds width {width} at 0x{offset:X}",
                    mask=self.rw_mask,
                    width=self.width,
                    offset=self.offset,
                )
            )


@dataclass
class BarModel:
    """Complete model of a BAR's register map."""

    size: int  # BAR size in bytes
    registers: Dict[int, RegisterSpec]  # offset -> register spec

    def __post_init__(self):
        """Validate BAR model after construction."""
        if self.size <= 0:
            raise ValueError("BAR size must be positive")

        # Validate all register offsets are within BAR
        for offset, reg in self.registers.items():
            if offset < 0 or offset >= self.size:
                raise ValueError(
                    safe_format(
                        "Register offset 0x{offset:X} outside BAR size 0x{size:X}",
                        offset=offset,
                        size=self.size,
                    )
                )

            # Check that register doesn't exceed BAR boundary
            if offset + reg.width > self.size:
                raise ValueError(
                    safe_format(
                        "Register at 0x{offset:X} (width={width}) "
                        "exceeds BAR size 0x{size:X}",
                        offset=offset,
                        width=reg.width,
                        size=self.size,
                    )
                )


def load_bar_model(path: Path) -> BarModel:
    """Load and validate BAR model from JSON file.

    Args:
        path: Path to JSON model file

    Returns:
        Validated BarModel

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If model is invalid
        json.JSONDecodeError: If JSON is malformed
    """
    if not path.exists():
        raise FileNotFoundError(safe_format(
            "Model file not found: {path}", path=path))

    try:
        with open(path, "r") as f:
            data = json.load(f)

        # Validate required top-level keys
        if "size" not in data:
            raise ValueError("Missing required key: size")
        if "regs" not in data:
            raise ValueError("Missing required key: regs")

        size = int(data["size"])
        regs_dict = data["regs"]

        # Parse registers
        registers = {}
        for offset_hex, reg_data in regs_dict.items():
            try:
                offset = int(offset_hex, 16) if isinstance(
                    offset_hex, str) else int(offset_hex)
            except ValueError:
                raise ValueError(
                    safe_format("Invalid offset format: {off}", off=offset_hex)
                )

            # Validate required register fields
            if "width" not in reg_data:
                raise ValueError(
                    safe_format(
                        "Missing 'width' for register at 0x{offset:X}",
                        offset=offset
                    )
                )
            if "reset" not in reg_data:
                raise ValueError(
                    safe_format(
                        "Missing 'reset' for register at 0x{offset:X}",
                        offset=offset
                    )
                )
            if "rw_mask" not in reg_data:
                raise ValueError(
                    safe_format(
                        "Missing 'rw_mask' for register at 0x{offset:X}",
                        offset=offset
                    )
                )

            registers[offset] = RegisterSpec(
                offset=offset,
                width=int(reg_data["width"]),
                reset=int(reg_data["reset"]),
                rw_mask=int(reg_data["rw_mask"]),
                hints=reg_data.get("hints", {}),
            )

        model = BarModel(size=size, registers=registers)

        log_info_safe(
            logger,
            safe_format(
                "Loaded BAR model from {path}: {nregs} registers, size=0x{size:X}",
                path=path.name,
                nregs=len(registers),
                size=size,
            ),
            prefix="MODEL",
        )

        return model

    except json.JSONDecodeError as e:
        log_error_safe(
            logger,
            safe_format("Invalid JSON in {path}: {err}", path=path, err=str(e)),
        )
        raise
    except (ValueError, KeyError) as e:
        log_error_safe(
            logger,
            safe_format("Invalid model schema in {path}: {err}",
                        path=path, err=str(e)),
        )
        raise


def save_bar_model(model: BarModel, path: Path) -> None:
    """Save BAR model to JSON file.

    Args:
        model: BarModel to save
        path: Destination path

    Raises:
        IOError: If file cannot be written
    """
    # Convert to JSON-serializable dict
    data = {
        "size": model.size,
        "regs": {
            f"0x{offset:X}": {
                "width": reg.width,
                "reset": reg.reset,
                "rw_mask": reg.rw_mask,
                "hints": reg.hints,
            }
            for offset, reg in model.registers.items()
        },
    }

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        log_info_safe(
            logger,
            safe_format(
                "Saved BAR model to {path}: {nregs} registers",
                path=path,
                nregs=len(model.registers),
            ),
            prefix="MODEL",
        )

    except IOError as e:
        log_error_safe(
            logger,
            safe_format("Failed to save model to {path}: {err}",
                        path=path, err=str(e)),
        )
        raise


def serialize_bar_model(model: BarModel) -> Dict[str, Any]:
    """Serialize BAR model to JSON-compatible dict.

    Args:
        model: BarModel to serialize

    Returns:
        JSON-compatible dict representation
    """
    return {
        "size": model.size,
        "regs": {
            f"0x{offset:X}": {
                "width": reg.width,
                "reset": reg.reset,
                "rw_mask": reg.rw_mask,
                "hints": reg.hints,
            }
            for offset, reg in model.registers.items()
        },
    }


def deserialize_bar_model(data: Dict[str, Any]) -> BarModel:
    """Deserialize BAR model from JSON-compatible dict.

    Args:
        data: Serialized model dict

    Returns:
        Reconstructed BarModel

    Raises:
        ValueError: If data is invalid
    """
    size = data.get("size")
    if not isinstance(size, int) or size <= 0:
        raise ValueError(f"Invalid BAR size: {size}")

    regs_data = data.get("regs", {})
    if not isinstance(regs_data, dict):
        raise ValueError("Missing or invalid 'regs' field")

    registers = {}
    for offset_str, reg_data in regs_data.items():
        offset = int(offset_str, 16)
        registers[offset] = RegisterSpec(
            width=reg_data["width"],
            reset=reg_data["reset"],
            rw_mask=reg_data["rw_mask"],
            hints=reg_data.get("hints", []),
        )

    return BarModel(size=size, registers=registers)
