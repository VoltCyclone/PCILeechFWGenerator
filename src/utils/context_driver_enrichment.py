import platform
from typing import Any, Dict, List, Optional, Union

from src.string_utils import (log_error_safe, log_info_safe, log_warning_safe,
                              safe_format)
from src.utils.unified_context import \
    TemplateObject  # For context compatibility


def enrich_context_with_driver(
    context: Union[Dict[str, Any], TemplateObject],
    vendor_id: str,
    device_id: str,
    *,
    ensure_sources: bool = False,
    max_sources: int = 40,
    _is_linux=None,
    _resolve_driver_module=None,
    _ensure_kernel_source=None,
    _find_driver_sources=None,
) -> Union[Dict[str, Any], TemplateObject]:
    """Attach kernel driver metadata to an existing build context.

    Adds a `kernel_driver` section with:
        module: resolved module name (if found)
        vendor_id / device_id
        source_count
        source_files (truncated list)
        sources_truncated (bool)

    Behavior:
        - Fails softly: logs warnings instead of raising unless Linux check fails.
        - Does not hardcode IDs; requires caller to pass vendor/device IDs.
    """

    def _set(obj: Union[Dict[str, Any], TemplateObject], key: str, value: Any) -> None:
        if isinstance(obj, TemplateObject):
            setattr(obj, key, value if isinstance(value, TemplateObject) else value)
        else:
            obj[key] = value

    # Delayed imports to break cyclic dependency
    from src.scripts import kernel_utils

    # Dependency injection for testability
    is_linux = _is_linux if _is_linux is not None else kernel_utils.is_linux
    resolve_driver_module = (
        _resolve_driver_module
        if _resolve_driver_module is not None
        else kernel_utils.resolve_driver_module
    )
    ensure_kernel_source = (
        _ensure_kernel_source
        if _ensure_kernel_source is not None
        else kernel_utils.ensure_kernel_source
    )
    find_driver_sources = (
        _find_driver_sources
        if _find_driver_sources is not None
        else kernel_utils.find_driver_sources
    )
    logger = kernel_utils.logger
    resolve_driver_module = _resolve_driver_module
    ensure_kernel_source = _ensure_kernel_source
    find_driver_sources = _find_driver_sources

    # Always set kernel_driver, even if enrichment is skipped
    if not vendor_id or not device_id:
        log_error_safe(
            logger,
            safe_format(
                (
                    "Cannot enrich context with kernel driver metadata: "
                    "missing IDs (vendor_id={vid}, device_id={did})"
                ),
                vid=vendor_id,
                did=device_id,
            ),
            prefix="KERNEL",
        )
        data: Dict[str, Any] = {
            "vendor_id": vendor_id,
            "device_id": device_id,
            "module": None,
            "source_count": 0,
            "source_files": [],
            "sources_truncated": False,
        }
        _set(context, "kernel_driver", TemplateObject(data))
        return context

    if not is_linux():  # Soft skip on non-Linux platforms
        log_warning_safe(
            logger,
            safe_format(
                "Skipping kernel driver enrichment; non-Linux platform {plat}",
                plat=platform.system(),
            ),
            prefix="KERNEL",
        )
        data: Dict[str, Any] = {
            "vendor_id": vendor_id,
            "device_id": device_id,
            "module": None,
            "source_count": 0,
            "source_files": [],
            "sources_truncated": False,
        }
        _set(context, "kernel_driver", TemplateObject(data))
        return context

    module_name: Optional[str] = None
    source_files: List[str] = []
    truncated = False

    try:
        module_name = resolve_driver_module(vendor_id, device_id)  # type: ignore
        log_info_safe(
            logger,
            safe_format(
                "Resolved driver module {mod} for {vid}:{did}",
                mod=module_name,
                vid=vendor_id,
                did=device_id,
            ),
            prefix="KERNEL",
        )

        if ensure_sources:
            ksrc = ensure_kernel_source()  # type: ignore
            if ksrc and module_name:
                try:
                    paths = find_driver_sources(ksrc, module_name)  # type: ignore
                    if len(paths) > max_sources:
                        truncated = True
                        paths = paths[:max_sources]
                    source_files = [str(p) for p in paths]
                except Exception as e:  # pragma: no cover (defensive)
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Failed to locate driver sources for {mod}: {e}",
                            mod=module_name,
                            e=e,
                        ),
                        prefix="KERNEL",
                    )
    except Exception as e:
        log_warning_safe(
            logger,
            safe_format(
                "Driver module resolution failed for {vid}:{did}: {e}",
                vid=vendor_id,
                did=device_id,
                e=e,
            ),
            prefix="KERNEL",
        )

    data: Dict[str, Any] = {
        "vendor_id": vendor_id,
        "device_id": device_id,
        "module": module_name,
        "source_count": len(source_files),
        "source_files": source_files,
        "sources_truncated": truncated,
    }

    _set(context, "kernel_driver", TemplateObject(data))
    return context
