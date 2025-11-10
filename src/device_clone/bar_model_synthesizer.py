#!/usr/bin/env python3
"""Synthesize BAR models from captured MMIO traces.

Converts runtime MMIO access patterns into static register models
for deterministic BAR content generation.
"""

from collections import defaultdict
from typing import Dict, List

from src.device_clone.bar_model_loader import BarModel, RegisterSpec
from src.device_clone.mmio_tracer import MmioAccess, MmioTrace
from src.log_config import get_logger
from src.string_utils import log_debug_safe, log_info_safe, safe_format

logger = get_logger(__name__)


def synthesize_model(trace: MmioTrace) -> BarModel:
    """Synthesize BAR model from captured MMIO trace.

    Algorithm:
    1. Group accesses by offset
    2. Infer width from most common access width per offset
    3. Reset value = first read value OR first write value (fallback)
    4. RW mask = OR of all written values (conservative estimate)
    5. Detect RW1C hint: write(1) followed by read(0) on same bit

    Args:
        trace: Captured MMIO trace

    Returns:
        BarModel ready for content generation

    Raises:
        ValueError: If trace is invalid or empty
    """
    if trace.bar_size <= 0:
        raise ValueError("Invalid BAR size in trace")

    if not trace.accesses:
        log_info_safe(
            logger,
            safe_format(
                "Empty trace for BAR{idx}, creating minimal model",
                idx=trace.bar_index
            ),
            prefix="SYNTH",
        )
        # Return minimal model for empty trace
        return BarModel(size=trace.bar_size, registers={})

    # Group accesses by offset
    by_offset: Dict[int, List[MmioAccess]] = defaultdict(list)
    for access in trace.accesses:
        by_offset[access.offset].append(access)

    registers: Dict[int, RegisterSpec] = {}

    for offset, accesses in sorted(by_offset.items()):
        # Infer width (most common)
        widths = [a.width for a in accesses]
        width = max(set(widths), key=widths.count)

        # Find reset value
        reset = 0
        reads = [a for a in accesses if a.operation ==
                 "R" and a.value is not None]
        writes = [a for a in accesses if a.operation == "W"]

        if reads:
            # First read typically represents reset/power-on state
            reset = reads[0].value
            log_debug_safe(
                logger,
                safe_format(
                    "BAR{bar} offset 0x{off:04X}: reset=0x{val:0{w}X} (from read)",
                    bar=trace.bar_index,
                    off=offset,
                    val=reset,
                    w=width * 2,
                ),
                prefix="SYNTH",
            )
        elif writes:
            # Fallback: use first write value as reset approximation
            reset = writes[0].value if writes[0].value is not None else 0
            log_debug_safe(
                logger,
                safe_format(
                    "BAR{bar} offset 0x{off:04X}: reset=0x{val:0{w}X} (from write)",
                    bar=trace.bar_index,
                    off=offset,
                    val=reset,
                    w=width * 2,
                ),
                prefix="SYNTH",
            )

        # Compute RW mask (conservative: OR all written values)
        rw_mask = 0
        for write in writes:
            if write.value is not None:
                rw_mask |= write.value

        # If no writes observed, assume fully writable
        if rw_mask == 0:
            rw_mask = (1 << (8 * width)) - 1
            log_debug_safe(
                logger,
                safe_format(
                    "BAR{bar} offset 0x{off:04X}: no writes, assuming fully RW",
                    bar=trace.bar_index,
                    off=offset,
                ),
                prefix="SYNTH",
            )

        # Detect RW1C behavior (simple heuristic)
        maybe_rw1c = _detect_rw1c(accesses)

        # Clamp values to width
        max_val = (1 << (8 * width)) - 1
        reset &= max_val
        rw_mask &= max_val

        registers[offset] = RegisterSpec(
            offset=offset,
            width=width,
            reset=reset,
            rw_mask=rw_mask,
            hints={"maybe_rw1c": maybe_rw1c},
        )

    log_info_safe(
        logger,
        safe_format(
            "Synthesized model for BAR{idx}: {nregs} registers from {naccess} accesses",
            idx=trace.bar_index,
            nregs=len(registers),
            naccess=len(trace.accesses),
        ),
        prefix="SYNTH",
    )

    return BarModel(size=trace.bar_size, registers=registers)


def _detect_rw1c(accesses: List[MmioAccess]) -> bool:
    """Detect potential RW1C (read/write-1-to-clear) behavior.

    Heuristic: If we see a write of non-zero value followed by
    a read that has fewer bits set, it may be RW1C.

    Args:
        accesses: List of accesses to same offset

    Returns:
        True if RW1C behavior detected
    """
    for i, access in enumerate(accesses[:-1]):
        next_access = accesses[i + 1]

        # Look for write followed by read
        if (
            access.operation == "W"
            and access.value is not None
            and access.value != 0
            and next_access.operation == "R"
            and next_access.value is not None
        ):
            # Check if any bits were cleared
            cleared_bits = access.value & ~next_access.value
            if cleared_bits:
                return True

    return False
