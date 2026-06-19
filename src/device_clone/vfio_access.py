"""VFIO access port for the device-clone domain.

The domain needs to bind a device to vfio-pci and open its VFIO file
descriptors, but it should not depend on the concrete CLI implementation of
those operations. This module defines the abstraction (``VFIOAccess`` /
``BinderHandle``) that the domain owns, plus a default factory that resolves
the concrete adapter at runtime.

Dependency direction: domain code depends on the ``VFIOAccess`` Protocol defined
here. Only ``get_default_vfio_access`` reaches into the CLI layer, and it does so
lazily behind this abstraction — so the *type* dependency points inward
(CLI → domain port), not outward.
"""

from __future__ import annotations

from typing import Protocol, Tuple, runtime_checkable


@runtime_checkable
class BinderHandle(Protocol):
    """A session-scoped binding of a device to vfio-pci."""

    @property
    def is_bound(self) -> bool: ...

    def bind(self) -> None: ...

    def unbind(self) -> None: ...


@runtime_checkable
class VFIOAccess(Protocol):
    """Port the domain uses to bind/open a device's VFIO interface."""

    def ensure_binding(self, bdf: str) -> str:
        """Ensure ``bdf`` is bound to vfio-pci. Returns the IOMMU group id."""
        ...

    def open_device(self, bdf: str) -> Tuple[int, int]:
        """Open the device. Returns ``(device_fd, container_fd)``."""
        ...

    def bind_for_session(self, bdf: str, attach: bool = True) -> BinderHandle:
        """Return a binder handle that keeps ``bdf`` bound for the session."""
        ...


class _CliVFIOAccess:
    """Default adapter wrapping the CLI VFIO helpers.

    Kept tiny and lazy: the CLI imports happen inside methods so importing this
    module never drags in the CLI layer, and the domain only ever sees the
    ``VFIOAccess`` Protocol.
    """

    def ensure_binding(self, bdf: str) -> str:
        from pcileechfwgenerator.cli.vfio_helpers import ensure_device_vfio_binding

        return ensure_device_vfio_binding(bdf)

    def open_device(self, bdf: str) -> Tuple[int, int]:
        from pcileechfwgenerator.cli.vfio_helpers import get_device_fd

        return get_device_fd(bdf)

    def bind_for_session(self, bdf: str, attach: bool = True) -> BinderHandle:
        from pcileechfwgenerator.cli.vfio_handler import VFIOBinder

        return VFIOBinder(bdf, attach=attach)


def get_default_vfio_access() -> VFIOAccess:
    """Return the default (CLI-backed) VFIO access adapter."""
    return _CliVFIOAccess()
