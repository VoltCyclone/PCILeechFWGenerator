#!/usr/bin/env python3
"""Shell command execution utilities with dry-run support."""

import logging

import subprocess

from typing import Optional

logger = logging.getLogger(__name__)


class Shell:
    """Wrapper around subprocess supporting dry_run mode."""

    def __init__(self, dry_run: bool = False, safe_mode: bool = True):
        """Initialize shell wrapper.

        Args:
            dry_run: If True, commands will be logged but not executed
            safe_mode: If True, enables additional safety checks for commands
        """
        self.dry_run = dry_run
        self.safe_mode = safe_mode

    def _validate_command_safety(self, cmd: str) -> None:
        """Validate command for basic safety if safe_mode is enabled.

        Args:
            cmd: Command to validate

        Raises:
            RuntimeError: If command appears unsafe
        """
        if not self.safe_mode:
            return

        # none
        dangerous_patterns = ["none"]

        cmd_lower = cmd.lower()
        for pattern in dangerous_patterns:
            if pattern in cmd_lower:
                raise RuntimeError("Command blocked for safety reasons")

        # Check for suspicious redirections to sensitive paths
        if any(path in cmd for path in ["/etc/", "/boot/", "/sys/", "/proc/"]):
            if any(op in cmd for op in ["> ", ">> ", "| dd", "| tee"]):
                logger.warning(f"Command modifies sensitive paths: {cmd}")

    def run(self, *parts: str, timeout: int = 30, cwd: Optional[str] = None) -> str:
        """Execute a shell command and return stripped output.

        Args:
            *parts: Command parts to join with spaces
            timeout: Command timeout in seconds
            cwd: Working directory for command execution

        Returns:
            Command output as string

        Raises:
            RuntimeError: If command fails or times out
        """
        cmd = " ".join(str(part) for part in parts)

        # Validate command safety
        self._validate_command_safety(cmd)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would execute: {cmd}")
            if cwd:
                logger.debug(f"[DRY RUN] Working directory: {cwd}")
            return ""

        logger.debug(f"Executing command: {cmd}")
        if cwd:
            logger.debug(f"Working directory: {cwd}")

        try:
            result = subprocess.check_output(
                cmd,
                shell=True,
                text=True,
                timeout=timeout,
                stderr=subprocess.STDOUT,
                cwd=cwd,
            ).strip()
            logger.debug(f"Command output: {result}")
            return result

        except subprocess.TimeoutExpired as e:
            error_msg = f"Command timed out after {timeout}s: {cmd}"
            if cwd:
                error_msg += f" (cwd: {cwd})"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        except subprocess.CalledProcessError as e:
            error_msg = f"Command failed (exit code {e.returncode}): {cmd}"
            if cwd:
                error_msg += f" (cwd: {cwd})"
            if e.output:
                error_msg += f"\nOutput: {e.output}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            # Catch-all to normalize unexpected subprocess errors in tests
            error_msg = f"Command execution error: {cmd} ({e})"
            if cwd:
                error_msg += f" (cwd: {cwd})"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def run_check(
        self, *parts: str, timeout: int = 30, cwd: Optional[str] = None
    ) -> bool:
        """Execute a command and return True if successful, False otherwise.

        Args:
            *parts: Command parts to join with spaces
            timeout: Command timeout in seconds
            cwd: Working directory for command execution

        Returns:
            True if command succeeded, False otherwise
        """
        try:
            self.run(*parts, timeout=timeout, cwd=cwd)
            return True
        except RuntimeError:
            return False

    def write_file(
        self,
        path: str,
        content: str,
        mode: str = "w",
        create_dirs: bool = True,
        permissions: Optional[int] = None,
    ) -> None:
        """Write content to a file (respects dry_run mode).

        Args:
            path: File path to write to
            content: Content to write
            mode: File write mode (default: "w")
            create_dirs: Create parent directories if they don't exist
            permissions: Unix file permissions (e.g., 0o600 for user-only)

        Raises:
            RuntimeError: If file operation fails
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would write to file: {path}")
            logger.debug(f"[DRY RUN] Content: {content}")
            if permissions:
                logger.debug(f"[DRY RUN] Permissions: {oct(permissions)}")
            return

        try:
            # Create parent directories if needed
            if create_dirs:
                from pathlib import Path

                Path(path).parent.mkdir(parents=True, exist_ok=True)

            with open(path, mode) as f:
                f.write(content)

            # Set file permissions if specified
            if permissions is not None:
                import os

                os.chmod(path, permissions)
                logger.debug(f"Set file permissions to {oct(permissions)}: {path}")

            logger.debug(f"Wrote content to file: {path}")
        except (OSError, IOError) as e:
            error_msg = f"Failed to write file {path}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
