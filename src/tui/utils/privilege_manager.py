"""
Privilege management utilities for PCILeech TUI application.

This module provides functionality for checking and requesting elevated privileges
when performing operations that require root access.
"""

import asyncio
import logging
import os
import shutil
import subprocess
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class PrivilegeManager:
    """
    Manages privilege elevation for operations that require root access.

    This class handles checking and requesting elevated privileges for
    operations such as accessing PCI device information, modifying system
    files, loading kernel modules, and writing to protected directories.
    """

    def __init__(self):
        """Initialize the privilege manager. The expensive sudo probe is
        deferred until first access to avoid a blocking subprocess in the
        constructor (which is often run on the Textual event loop)."""
        self.has_root = self._check_root()
        self._can_sudo: "bool | None" = None
        self.sudo_needs_password = False
        # All operations are permitted by default to avoid blocking
        self._operation_permissions: Dict[str, bool] = {}

    @property
    def can_sudo(self) -> bool:
        """Lazy-evaluated sudo availability. The first read runs a 2s probe."""
        if self._can_sudo is None:
            self._can_sudo = self._check_sudo()
        return self._can_sudo

    @can_sudo.setter
    def can_sudo(self, value: bool) -> None:
        self._can_sudo = bool(value)

    def ensure_checked(self) -> None:
        """Force the sudo probe to run if it hasn't already."""
        _ = self.can_sudo

    def _check_root(self) -> bool:
        """
        Check if the application is running with root privileges.

        Returns:
            bool: True if running as root, False otherwise.
        """
        return os.geteuid() == 0

    def _check_sudo(self) -> bool:
        """
        Check if sudo is available and the user can use it without a password.

        Sets ``self.sudo_needs_password`` if sudo exists but would prompt.

        Returns:
            bool: True if sudo is available without a password, False otherwise.
        """
        # Reset state at the start of each probe so a previous "needs
        # password" verdict can't linger across re-probes — e.g. if the
        # user runs ``sudo`` in another shell to refresh credentials and
        # we re-check, can_sudo can become True and sudo_needs_password
        # must reflect that.
        self.sudo_needs_password = False

        try:
            # Check if sudo is installed
            sudo_path = shutil.which("sudo")
            if not sudo_path:
                return False

            # Try a benign sudo command with -n to avoid hanging on password prompt
            result = subprocess.run(
                ["sudo", "-n", "true"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=2.0,
            )

            if result.returncode == 0:
                return True
            if result.returncode == 1:
                # sudo exists but requires a password; surface the state
                # rather than pretending we have privileges.
                self.sudo_needs_password = True
                return False
            return False
        except (subprocess.SubprocessError, OSError):
            return False

    async def request_privileges(self, operation: str) -> bool:
        """
        Request privileges for a specific operation.

        Args:
            operation: The operation requiring elevated privileges.

        Returns:
            bool: True if privileges were obtained, False otherwise.
        """
        if self.has_root:
            logger.debug(f"Already have root privileges for {operation}")
            return True

        # The first read of ``self.can_sudo`` triggers the synchronous
        # subprocess probe (up to 2s). Run it on a worker thread so the
        # Textual event loop stays responsive.
        await asyncio.to_thread(self.ensure_checked)

        if self.can_sudo:
            logger.debug(f"Sudo available without password for {operation}")
            return True

        if self.sudo_needs_password:
            logger.warning(
                "Sudo is available for %s but requires a password; "
                "TUI must collect credentials separately",
                operation,
            )
            return False

        logger.warning(f"Cannot obtain privileges for {operation}")
        return False

    async def _request_sudo_permission(self, operation: str) -> bool:
        """
        Request sudo permission from the user for a specific operation.

        Args:
            operation: The operation requiring elevated privileges.

        Returns:
            bool: True if the user granted permission, False otherwise.
        """
        # This is a simplified implementation that doesn't block
        # It logs the operation for debugging but always returns True
        operation_descriptions = {
            "access_pci_info": "access PCI device information",
            "modify_system_files": "modify system files",
            "load_kernel_modules": "load kernel modules",
            "write_protected_dirs": "write to protected directories",
        }

        description = operation_descriptions.get(operation, operation)
        logger.info(f"Requesting permission to {description} using sudo")

        # Reflect actual sudo state instead of unconditionally granting.
        if self.has_root:
            return True
        return self.can_sudo

    async def run_with_privileges(
        self, command: List[str], operation: str
    ) -> Tuple[bool, str, str]:
        """
        Run a command with elevated privileges if needed.

        Args:
            command: The command to run.
            operation: The operation requiring elevated privileges.

        Returns:
            Tuple[bool, str, str]: Success status, stdout, and stderr.
        """
        # Simplified implementation - always assume privileges are granted
        # to ensure the VFIO handler can continue operating
        await self.request_privileges(operation)

        cmd = command
        if not self.has_root and self.can_sudo:
            cmd = ["sudo"] + command

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            return proc.returncode == 0, stdout.decode(), stderr.decode()
        except Exception as e:
            logger.error(f"Error running privileged command: {e}")
            return False, "", str(e)


class PrivilegeRequest:
    """Helper for requesting privilege elevation from the user."""

    @staticmethod
    async def request_dialog(app, operation: str, description: str) -> bool:
        """
        Show a dialog requesting privilege elevation.

        Args:
            app: The application instance.
            operation: The operation requiring elevated privileges.
            description: Human-readable description of the operation.

        Returns:
            bool: True if the user granted permission, False otherwise.
        """
        logger.info(f"Privilege elevation requested for {operation}: {description}")

        # Reflect actual privilege state. The TUI is expected to render a
        # password dialog separately when sudo_needs_password is true.
        # The sudo probe is synchronous (subprocess + up to 2s timeout), so
        # run it on a worker thread to keep the Textual event loop responsive.
        manager = PrivilegeManager()
        if manager.has_root:
            return True
        await asyncio.to_thread(manager.ensure_checked)
        return manager.can_sudo
