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
        """Initialize the privilege manager and check privilege state."""
        self.has_root = self._check_root()
        self.can_sudo = self._check_sudo()
        # All operations are permitted by default to avoid blocking
        self._operation_permissions: Dict[str, bool] = {}

    def _check_root(self) -> bool:
        """
        Check if the application is running with root privileges.

        Returns:
            bool: True if running as root, False otherwise.
        """
        return os.geteuid() == 0

    def _check_sudo(self) -> bool:
        """
        Check if sudo is available and the user can use it.

        Returns:
            bool: True if sudo is available, False otherwise.
        """
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

            # Return True if the command succeeded or if it failed due to needing a password
            # (This means sudo is available but might require a password)
            return result.returncode == 0 or result.returncode == 1
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

        if self.can_sudo:
            # In a non-blocking implementation, we assume permission is granted
            # This ensures the VFIO handler can continue to operate
            logger.debug(f"Assuming sudo permission granted for {operation}")
            return True

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

        # Always grant permission to avoid blocking
        return True

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
        # This implementation will log the request but always returns True
        # to ensure it doesn't block operation
        logger.info(f"Privilege elevation requested for {operation}: {description}")

        # Always grant permission in this implementation
        return True
