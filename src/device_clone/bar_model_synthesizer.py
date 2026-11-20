#!/usr/bin/env python3
"""Adapt legacy passive MMIO traces to evidence-based, read-only BAR models.

Legacy traces carry no verified completion, mediation, device-state transitions
or reset experiment. Their values are observations only, never permissions.
Epoch zero is a local grouping label, not a measured donor state. Unknown read
values stay None; unsupported generation details remain in register hints.
"""

from pcileechfwgenerator.device_clone.bar_model_loader import BarModel
from pcileechfwgenerator.device_clone.mmio_tracer import MmioTrace
from pcileechfwgenerator.device_clone.register_classifier import classify_registers
from pcileechfwgenerator.device_clone.register_evidence import (
    Observation,
    RegisterRef,
    width_mask,
)
from pcileechfwgenerator.device_clone.register_policy import compile_bar_model
from pcileechfwgenerator.log_config import get_logger
from pcileechfwgenerator.string_utils import log_info_safe, safe_format

logger = get_logger(__name__)


def synthesize_model(trace: MmioTrace) -> BarModel:
    """Retain observed initial values; passive traffic never grants writes.

    Invalid operations, values, alignment, overlapping widths and out-of-bounds
    accesses fail explicitly. Empty traces produce an empty, non-writable model.
    A register without a successful read has reset=None with an absence reason;
    the legacy renderer/loader cannot yet consume that nullable initial value.
    """
    observations = []
    for sequence_id, access in enumerate(trace.accesses):
        if access.operation not in ("R", "W"):
            raise ValueError("Unknown MMIO operation")
        if type(access.width) is not int or access.width not in (1, 2, 4):
            raise ValueError("Legacy MMIO trace requires a 1, 2 or 4 byte access")
        ref = RegisterRef(
            space="mem_bar",
            bar_index=trace.bar_index,
            offset=access.offset,
            width=access.width,
            field_mask=width_mask(access.width),
        )
        observations.append(
            Observation(
                sequence_id=sequence_id,
                monotonic_ns=access.timestamp_ns,
                operation="read" if access.operation == "R" else "write",
                ref=ref,
                attempted_value=access.value if access.operation == "W" else None,
                returned_value=access.value if access.operation == "R" else None,
                absent_reason=(
                    "Legacy trace did not record a value"
                    if access.value is None
                    else None
                ),
                completion="ok" if access.value is not None else "unsupported",
                source="native_driver",
                mediation="unknown",
                state_epoch=0,
            )
        )
    result = compile_bar_model(
        classify_registers(observations), trace.bar_index, trace.bar_size
    )
    for reg in result.model.registers.values():
        reg.hints["evidence"].update(
            source="native_driver",
            mediation="unknown",
            trace_format="legacy_mmio",
            completion_evidence="unverified",
            epoch_reason="Local grouping label; legacy trace has no state metadata",
        )
    log_info_safe(
        logger,
        safe_format(
            "Compiled passive model for BAR{idx}: {nregs} registers, "
            "{ngaps} unsupported items",
            idx=trace.bar_index,
            nregs=len(result.model.registers),
            ngaps=len(result.unsupported),
        ),
        prefix="SYNTH",
    )
    return result.model
