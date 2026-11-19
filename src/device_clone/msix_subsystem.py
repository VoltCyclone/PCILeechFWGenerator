"""Decide once per build whether the MSI-X subsystem is generated (MX-07).

The decision is made at generation time, stored in the template context as
``msix_subsystem`` and read by everything that depends on it: the BAR
controller template (router, core and transmit path) and the hard IP override
(whether the MSI-X capability is advertised). A build that has no stored
decision, such as one that writes no generated BAR controller, never
advertises MSI-X.

Support is reported in three separate parts, so table emulation is never
presented as a working donor device: table and PBA service, interrupt
transport, and an event producer. No event producer exists yet, so a generated
subsystem serves the table and can send messages but never requests one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from pcileechfwgenerator.device_clone.msix_layout import (
    MsixCapability,
    MsixLayout,
    MsixLayoutError,
    derive_msix_layout_from_capability,
    read_msix_capability,
)
from pcileechfwgenerator.device_clone.served_bar import served_bars_from_config
from pydantic import BaseModel, ConfigDict, model_validator

CONTEXT_KEY = "msix_subsystem"
NOT_GENERATED = "the MSI-X subsystem is not generated for this build"

# Board files the generated subsystem relies on (see the voltcyclone-fpga
# change that passes the PCIe core status to the BAR controller).
_CONTROLLER = "pcileech_tlps128_bar_controller.sv"
_HEADER = "pcileech_header.svh"
_STATUS_MODPORT = "modport mpro"


class MsixSubsystemPlan(BaseModel):
    """Whether the donor has MSI-X and whether this build generates it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    present: bool
    generated: bool = False
    reason: Optional[str] = None
    layout: Optional[MsixLayout] = None

    @model_validator(mode="after")
    def _consistent(self) -> "MsixSubsystemPlan":
        if self.generated != (self.layout is not None):
            raise ValueError("a generated MSI-X subsystem needs exactly one layout")
        if self.generated and (not self.present or self.reason):
            raise ValueError("a generated MSI-X subsystem has no refusal reason")
        if self.present and not self.generated and not self.reason:
            raise ValueError("a refused MSI-X subsystem needs a reason")
        return self

    def support(self) -> dict[str, bool]:
        """Separate support report: never one 'MSI-X works' flag."""
        return {
            "table_and_pba": self.generated,
            "interrupt_transport": self.generated,
            "event_producer": False,
        }

    def rtl_parameters(self) -> dict[str, int]:
        if self.layout is None:
            raise MsixLayoutError(NOT_GENERATED)
        return self.layout.decode_parameters()

    def to_context(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_context(cls, template_context: Mapping[str, Any]) -> "MsixSubsystemPlan":
        """The stored decision; a context without one never generates MSI-X."""
        stored = (template_context or {}).get(CONTEXT_KEY)
        if stored is None:
            raise MsixLayoutError(NOT_GENERATED)
        if isinstance(stored, cls):
            return stored
        # Template rendering wraps nested context dicts in TemplateObject in
        # place, so the stored record can come back wrapped.
        return cls.model_validate(_plain(stored))


def board_msix_support_error(board_dir: Optional[Path]) -> Optional[str]:
    """Why a board's sources cannot carry the subsystem, or None if they can."""
    if board_dir is None:
        return "the target board's sources were not found"
    src = Path(board_dir) / "src"
    if not (src / _CONTROLLER).is_file():
        return "the target board has no BAR controller completion stream"
    header = src / _HEADER
    try:
        header_text = header.read_text(errors="replace")
    except OSError:
        return "the target board has no readable pcileech_header.svh"
    if _STATUS_MODPORT not in header_text:
        return (
            "the target board's sources do not pass the PCIe core status to the "
            "BAR controller (voltcyclone-fpga is too old)"
        )
    return None


def _plain(value: Any) -> Any:
    """Plain dicts and lists from mappings, TemplateObjects and sequences."""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict) and not isinstance(value, BaseModel):
        value = to_dict()
    if isinstance(value, Mapping) or (
        hasattr(value, "items") and callable(getattr(value, "items"))
    ):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        # Same rules as fifo_donor_patcher._coerce_int: 0x-prefixed or plain
        # integers, then bare hex.
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 16) if text.lower().startswith("0x") else int(text, 0)
        except ValueError:
            try:
                return int(text, 16)
            except ValueError:
                return None
    return None


def msix_capability_from_capture(
    result: Mapping[str, Any], template_context: Mapping[str, Any]
) -> Optional[MsixCapability]:
    """Prefer captured config bytes; adapt legacy parsed fields otherwise.

    Runtime state such as the captured Enable bit is ignored.
    """
    config_data = result.get("config_space_data") or {}
    raw = config_data.get("config_space_hex")
    if raw is None:
        raw = (template_context.get("config_space") or {}).get("raw_data")
    if raw:
        config = bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw)
        return read_msix_capability(config)

    msix = result.get("msix_data") or template_context.get("msix_data") or {}
    if not msix:
        return None
    if msix.get("is_valid") is False:
        raise MsixLayoutError("the captured MSI-X layout failed validation")
    fields = msix.get("capability_info") or msix
    return MsixCapability(
        **{
            name: _as_int(fields.get(name))
            for name in (
                "table_size",
                "table_bir",
                "table_offset",
                "pba_bir",
                "pba_offset",
            )
        }
    )


def _served_bar_index(template_context: Mapping[str, Any]) -> int:
    bar_config = template_context.get("bar_config")
    served = served_bars_from_config(bar_config)
    if served is not None:
        owners = [bar for bar in served if bar.role in ("msix", "registers+msix")]
        if len(owners) != 1:
            raise MsixLayoutError("the served set has no MSI-X BAR owner")
        return owners[0].index
    bars = _attr_or_key(bar_config, "bars") or []
    if not bars:
        raise MsixLayoutError("the served BAR aperture is unavailable")
    # Resolve exactly as the renderer does (sv_overlay_generator._served_bar_index):
    # ``served_bar_index`` names the donor BAR number and defaults to 0. The
    # renderer is the authority for which BAR the emitted RTL serves, so the
    # plan must agree with it; never fall back to ``primary_bar`` (a list
    # position), which can name a different BAR and desync the plan from the RTL.
    index = _as_int(_attr_or_key(bar_config, "served_bar_index"))
    if index is None:
        index = 0
    return index


def plan_msix_subsystem(
    result: Mapping[str, Any], *, board_error: Optional[str]
) -> MsixSubsystemPlan:
    """Decide from the capture, the served BAR and the board.

    ``result`` has the generator's ``template_context`` and, when available,
    ``config_space_data`` and ``msix_data``. ``board_error`` is the reason the
    board cannot carry the subsystem, or None.
    """
    template_context = result.get("template_context") or {}
    try:
        capability = msix_capability_from_capture(result, template_context)
        if capability is None:
            return MsixSubsystemPlan(present=False)
        bars = _attr_or_key(template_context.get("bar_config"), "bars", []) or []
        layout = derive_msix_layout_from_capability(
            capability, bars, _served_bar_index(template_context)
        )
    except (MsixLayoutError, ValueError, TypeError) as exc:
        # Pydantic errors may include input values; keep only layout diagnostics.
        reason = (
            str(exc)
            if isinstance(exc, MsixLayoutError)
            else "malformed MSI-X layout data"
        )
        return MsixSubsystemPlan(present=True, reason=reason)
    bar_config = template_context.get("bar_config")
    served = served_bars_from_config(bar_config)
    aperture = (
        next(bar.size for bar in served if bar.index == layout.served_bar.index)
        if served is not None
        else _as_int(_attr_or_key(bar_config, "aperture_size"))
    )
    if aperture is not None and aperture != layout.served_bar.size:
        # The controller sizes the served slot from aperture_size and the IP
        # override advertises it; MSI-X is only generated when they agree.
        return MsixSubsystemPlan(
            present=True, reason="the MSI-X BAR disagrees with the IP aperture"
        )
    if board_error:
        return MsixSubsystemPlan(present=True, reason=board_error)
    return MsixSubsystemPlan(present=True, generated=True, layout=layout)
