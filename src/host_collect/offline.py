"""Pure validation and immutable import of complete offline donor captures."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .snapshot import (
    CONTEXT_NAME,
    MSIX_NAME,
    DonorSnapshot,
    SnapshotError,
    _read_capture,
    load_snapshot,
    publish_snapshot,
)

if TYPE_CHECKING:
    from pcileechfwgenerator.device_clone.config_space_manager import BarInfo

MAX_RENDER_BAR_BYTES = 16 * 1024 * 1024


class OfflineResources(BaseModel):
    """Measured BAR lengths; config bytes alone cannot establish these sizes."""

    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    source: Literal["linux_sysfs_resource"]
    bar_sizes: list[int] = Field(min_length=6, max_length=6)


def capture_bar_sizes(resource_text: str) -> list[int]:
    """Decode the first six Linux PCI resource records without probing BARs."""
    rows = resource_text.splitlines()
    if len(rows) < 6:
        raise SnapshotError("PCI resource file has fewer than six BAR records")
    sizes = []
    for row in rows[:6]:
        try:
            start, end, flags = (int(value, 16) for value in row.split())
        except ValueError:
            raise SnapshotError("malformed PCI resource record") from None
        if min(start, end, flags) < 0 or end < start:
            raise SnapshotError("invalid PCI resource range")
        if start == end == 0:
            # Unassigned or unimplemented BAR; flags alone carry no size.
            sizes.append(0)
        elif not flags & 0x300:
            raise SnapshotError("BAR resource is neither memory nor I/O")
        else:
            sizes.append(end - start + 1)
    return sizes


def _msix_from_config(config: bytes) -> dict[str, int | bool]:
    """Read standard capabilities strictly; never search extended IDs as MSI-X."""
    pointer = config[0x34]
    if not config[6] & 0x10:
        if pointer:
            raise SnapshotError(
                "capability pointer exists without status capability bit"
            )
        return {}
    seen = set()
    msix: dict[str, int | bool] = {}
    while pointer:
        if pointer < 0x40 or pointer > 0xFC or pointer % 4 or pointer in seen:
            raise SnapshotError("invalid or cyclic PCI capability chain")
        seen.add(pointer)
        if config[pointer] == 0x11:
            if msix or pointer + 12 > 256:
                raise SnapshotError("duplicate or truncated MSI-X capability")
            control = int.from_bytes(config[pointer + 2 : pointer + 4], "little")
            table = int.from_bytes(config[pointer + 4 : pointer + 8], "little")
            pba = int.from_bytes(config[pointer + 8 : pointer + 12], "little")
            msix = {
                "table_size": (control & 0x7FF) + 1,
                "table_bir": table & 7,
                "table_offset": table & ~7,
                "pba_bir": pba & 7,
                "pba_offset": pba & ~7,
                "enabled": bool(control & 0x8000),
                "function_mask": bool(control & 0x4000),
            }
        pointer = config[pointer + 1]
    return msix


def captured_bars(snapshot: DonorSnapshot) -> list[BarInfo]:
    """Build BAR records only from validated config bytes and measured sizes."""
    from pcileechfwgenerator.device_clone.config_space_manager import BarInfo

    try:
        resources = OfflineResources.model_validate(
            snapshot.context["offline_resources"]
        )
    except (KeyError, ValidationError):
        raise SnapshotError(
            "missing or invalid offline_resources; use capture collect"
        ) from None
    if type(snapshot.context["offline_resources"].get("schema_version")) is not int:
        raise SnapshotError("offline resource schema version must be an integer")
    sizes = resources.bar_sizes
    bars = []
    index = 0
    while index < 6:
        value = int.from_bytes(
            snapshot.config_space[16 + index * 4 : 20 + index * 4], "little"
        )
        size = sizes[index]
        io = bool(value & 1)
        is64 = not io and (value & 6) == 4
        if size < 0 or (size and (size & (size - 1))):
            raise SnapshotError(
                f"BAR{index} length must be zero or a positive power of two"
            )
        if not io and value & 6 not in (0, 4):
            raise SnapshotError(f"BAR{index} uses an unsupported memory type")
        if is64 and (index == 5 or sizes[index + 1] != 0):
            raise SnapshotError(f"BAR{index} has an invalid 64-bit BAR pair")
        if not size and value:
            raise SnapshotError(
                f"BAR{index} advertises a BAR without a measured length"
            )
        if size:
            if size < (4 if io else 16) or size > (1 << (64 if is64 else 32)):
                raise SnapshotError(f"BAR{index} length is outside its address width")
            address = value & (0xFFFFFFFC if io else 0xFFFFFFF0)
            if is64:
                address |= (
                    int.from_bytes(
                        snapshot.config_space[20 + index * 4 : 24 + index * 4], "little"
                    )
                    << 32
                )
            if address % size:
                raise SnapshotError(f"BAR{index} address is not aligned to its length")
            bars.append(
                BarInfo(
                    index,
                    "io" if io else "memory",
                    address,
                    size,
                    bool(value & 8) if not io else False,
                    is64,
                )
            )
        index += 2 if is64 else 1
    return bars


def validate_offline_snapshot(snapshot: DonorSnapshot) -> DonorSnapshot:
    """Validate structural consistency before any output or hardware access."""
    from pcileechfwgenerator.device_clone.constants import is_placeholder_donor_id

    config = snapshot.config_space
    if len(config) not in (256, 4096):
        raise SnapshotError("offline config space must be exactly 256 or 4096 bytes")
    if config[0x0E] & 0x7F:
        raise SnapshotError(
            "offline captures currently require a type-0 PCI endpoint header"
        )
    identity = snapshot.identity
    if (
        identity.vendor_id in (0, 0xFFFF)
        or identity.device_id in (0, 0xFFFF)
        or not identity.class_code
    ):
        raise SnapshotError("offline capture has missing or invalid device IDs")
    if os.environ.get(
        "PCILEECH_ALLOW_PLACEHOLDER_IDS"
    ) != "1" and is_placeholder_donor_id(identity.vendor_id, identity.device_id):
        raise SnapshotError("offline capture contains placeholder donor IDs")
    bars = {bar.index: bar for bar in captured_bars(snapshot)}
    expected = _msix_from_config(config)
    actual = snapshot.msix["msix_info"]
    if not expected:
        # The existing collector represents absence as {} or all-zero fields.
        absent = {
            "table_size": 0,
            "table_bir": 0,
            "table_offset": 0,
            "pba_bir": 0,
            "pba_offset": 0,
            "enabled": False,
            "function_mask": False,
        }
        # Extra collector fields are ignored; only known fields must be neutral.
        if any(
            key in actual
            and (type(actual[key]) is not type(value) or actual[key] != value)
            for key, value in absent.items()
        ):
            raise SnapshotError("MSI-X metadata advertises an absent capability")
        return snapshot
    for name, value in expected.items():
        if type(actual.get(name)) is not type(value) or actual[name] != value:
            raise SnapshotError(f"MSI-X {name} conflicts with captured capability")
    ranges = []
    for kind, length in (
        ("table", expected["table_size"] * 16),
        ("pba", ((expected["table_size"] + 63) // 64) * 8),
    ):
        bir, offset = expected[f"{kind}_bir"], expected[f"{kind}_offset"]
        bar = bars.get(bir)
        if bar is None or not bar.is_memory or offset + length > bar.size:
            raise SnapshotError(f"MSI-X {kind} exceeds its implemented memory BAR")
        ranges.append((bir, offset, offset + length))
    a, b = ranges
    if a[0] == b[0] and max(a[1], b[1]) < min(a[2], b[2]):
        raise SnapshotError("MSI-X table overlaps its pending-bit array")
    # Only the capability layout fields above are consumed; extra collector
    # fields (e.g. vector_count) are ignored rather than rejected.
    return snapshot


def load_offline_capture(
    directory: Path, expected_bdf: str | None = None
) -> DonorSnapshot:
    """Load the versioned pair and its required resource extension."""
    directory = Path(directory)
    if expected_bdf is None:
        _, identity, _ = _read_capture(directory / CONTEXT_NAME)
        expected_bdf = identity.bdf
    snapshot = load_snapshot(
        directory / CONTEXT_NAME, directory / MSIX_NAME, expected_bdf
    )
    from .host_binding import verify_host_binding

    validate_offline_snapshot(snapshot)
    verify_host_binding(snapshot)
    return snapshot


def import_capture(
    source: Path, destination: Path, expected_bdf: str
) -> DonorSnapshot:
    """Publish a validated copy, retaining capture ID/time and preserving inputs."""
    snapshot = load_offline_capture(source, expected_bdf)
    publish_snapshot(destination, snapshot)
    return snapshot


def validate_render_request(
    snapshot: DonorSnapshot,
    source: Path,
    output: Path,
    template_output: str | Path | None = None,
) -> None:
    """Check renderer limits and input preservation before creating output."""
    from pcileechfwgenerator.exceptions import ConfigurationError

    source, output = Path(source).resolve(), Path(output).resolve()
    # A subdirectory of the capture (the default <datastore>/output) cannot
    # replace the top-level capture files; the capture itself or an ancestor can.
    if source == output or output in source.parents:
        raise ConfigurationError(
            "Offline output must not be the capture directory or contain it"
        )
    if template_output:
        target = Path(template_output).expanduser().resolve()
        if target == source or source in target.parents:
            raise ConfigurationError(
                "Template export must not modify the input capture"
            )
    bars = captured_bars(snapshot)
    if not any(bar.is_memory for bar in bars):
        raise ConfigurationError("Offline rendering requires an implemented memory BAR")
    if sum(bar.size for bar in bars) > MAX_RENDER_BAR_BYTES:
        raise ConfigurationError(
            "Offline rendering currently supports at most 16 MiB of total BAR "
            "content; sparse aperture support is not yet available"
        )


def capture_provenance(
    snapshot: DonorSnapshot, mode: Literal["offline", "container"] = "offline"
) -> dict[str, Any]:
    """Describe saved inputs without implying measured BAR or interrupt behavior.

    ``offline`` renders are unverified; ``container`` renders use a capture the
    host verified (or collected) in the same build.
    """
    return {
        **snapshot.identity.model_dump(mode="json"),
        "mode": mode,
        "resource_schema_version": 1,
        "bar_content_source": "derived, not sampled",
        "behavior_profile_captured": False,
        "msix_table_entries_captured": False,
    }
