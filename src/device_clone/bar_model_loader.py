#!/usr/bin/env python3
"""BAR register model data structures and persistence.

Defines the schema for learned BAR register models and provides
load/save functionality with validation.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from pcileechfwgenerator.device_clone.register_evidence import width_mask
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import log_error_safe, log_info_safe, safe_format

logger = get_logger(__name__)


@dataclass
class RegisterSpec:
    """Storage specification for one BAR register.

    ``reset`` is the legacy field name for the FPGA initial value, not a claim
    about donor reset behavior. None requires ``initial_value_absent_reason``
    in hints and is inspectable/persistable but cannot be rendered as storage.
    """

    offset: int  # Byte offset within BAR
    width: int  # Register width in bytes (1, 2, or 4)
    reset: int | None  # Initial storage value; provenance lives in hints
    rw_mask: int  # Bits that are writable (1 = RW, 0 = RO)
    hints: Dict[str, Any]  # Evidence provenance and unsupported semantics

    def __post_init__(self) -> None:
        """Validate register spec after construction."""
        if self.width not in (1, 2, 4):
            raise ValueError(
                safe_format(
                    "Invalid register width {width} at offset 0x{offset:X}",
                    width=self.width,
                    offset=self.offset,
                )
            )

        if not isinstance(self.hints, dict):
            raise ValueError("Register hints must be a dictionary")
        if self.reset is None and not self.hints.get("initial_value_absent_reason"):
            raise ValueError("Absent initial value requires a reason")

        max_val = width_mask(self.width)
        if self.reset is not None and not 0 <= self.reset <= max_val:
            raise ValueError(
                safe_format(
                    "Reset value 0x{reset:X} exceeds width {width} at 0x{offset:X}",
                    reset=self.reset,
                    width=self.width,
                    offset=self.offset,
                )
            )

        if not 0 <= self.rw_mask <= max_val:
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

    def __post_init__(self) -> None:
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

        end = 0
        for offset, reg in sorted(self.registers.items()):
            if reg.offset != offset:
                raise ValueError("Register key and offset disagree")
            if offset % reg.width:
                raise ValueError("Register offset is not aligned to its width")
            if offset < end:
                raise ValueError("Overlapping BAR registers")
            end = offset + reg.width


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
        raise FileNotFoundError(safe_format("Model file not found: {path}", path=path))

    try:
        with open(path, "r") as f:
            data = json.load(f)

        model = deserialize_bar_model(data)
        registers = model.registers
        size = model.size

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
            safe_format("Invalid model schema in {path}: {err}", path=path, err=str(e)),
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
    data = serialize_bar_model(model)

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
            safe_format("Failed to save model to {path}: {err}", path=path, err=str(e)),
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

    Each loaded register receives ``hints["provenance_status"]``. A missing
    classification list or any ``maybe_rw1c`` key marks it ``legacy_unverified``;
    otherwise it is ``evidence_backed``. This is a provenance-presence marker,
    never verification of capture identity, claim strength, or raw experiments.
    Legacy masks remain inspectable; callers requiring measured policy must
    validate the evidence artifact and may reject legacy models explicitly.

    Args:
        data: Serialized model dict

    Returns:
        Reconstructed BarModel

    Raises:
        ValueError: If data is invalid
    """
    for key in ("size", "regs"):
        if key not in data:
            raise ValueError(safe_format("Missing required key: {key}", key=key))
    size = data["size"]
    # Older file loaders accepted decimal strings for the BAR size.
    if isinstance(size, str):
        size = int(size)
    if not isinstance(size, int) or size <= 0:
        raise ValueError(safe_format("Invalid BAR size: {size}", size=size))

    regs_data = data["regs"]
    if not isinstance(regs_data, dict):
        raise ValueError("Missing or invalid 'regs' field")

    registers = {}
    for offset_key, reg_data in regs_data.items():
        try:
            offset = (
                int(offset_key, 16) if isinstance(offset_key, str) else int(offset_key)
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid offset format") from exc
        if offset in registers:
            raise ValueError("Duplicate register offset")
        for key in ("width", "reset", "rw_mask"):
            if key not in reg_data:
                raise ValueError(safe_format("Missing '{key}' for register", key=key))
        hints = dict(reg_data.get("hints", {}))
        # This describes provenance presence, not verification of a capture or
        # experimental claim. BDF-only caches must never become measured evidence.
        evidence = hints.get("evidence")
        hints["provenance_status"] = (
            "evidence_backed"
            if "maybe_rw1c" not in hints
            and isinstance(evidence, dict)
            and evidence.get("classifications")
            else "legacy_unverified"
        )
        registers[offset] = RegisterSpec(
            offset=offset,
            width=int(reg_data["width"]),
            reset=None if reg_data["reset"] is None else int(reg_data["reset"]),
            rw_mask=int(reg_data["rw_mask"]),
            hints=hints,
        )

    return BarModel(size=size, registers=registers)
