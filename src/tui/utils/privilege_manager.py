"""Backward-compatibility shim for ``PrivilegeManager``.

The canonical home is :mod:`pcileechfwgenerator.utils.privilege_manager`.
``PrivilegeManager`` is a shared (CLI + TUI) utility with no TUI dependencies,
so it was relocated to the ``utils`` layer to remove a CLI->TUI import
inversion. This shim keeps the old import path working; prefer importing from
``pcileechfwgenerator.utils.privilege_manager`` directly.
"""

from pcileechfwgenerator.utils.privilege_manager import (  # noqa: F401
    PrivilegeManager,
    PrivilegeRequest,
)

__all__ = ["PrivilegeManager", "PrivilegeRequest"]
