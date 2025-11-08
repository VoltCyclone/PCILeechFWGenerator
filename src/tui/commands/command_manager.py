"""
Command manager for tracking command history and implementing undo/redo functionality.

This module provides a command manager that maintains a history of executed commands
and supports undo/redo operations.
"""

from typing import List, Optional

from .command import Command


class CommandManager:
    """Manages commands and provides undo/redo functionality."""

    def __init__(self, max_history_size: int = 50) -> None:
        """
        Initialize the command manager.

        Args:
            max_history_size: The maximum number of commands to keep in history
        """
        self.history: List[Command] = []
        self.undone: List[Command] = []
        self.max_history_size = max_history_size

    async def execute(self, command: Command) -> bool:
        """
        Execute a command and add it to history if successful.

        Args:
            command: The command to execute

        Returns:
            bool: True if the command was executed successfully
        """
        success = await command.execute()
        if success:
            self.history.append(command)
            self.undone.clear()  # Clear redo stack on new command

            # Trim history if it exceeds max size
            if len(self.history) > self.max_history_size:
                self.history = self.history[-self.max_history_size :]

        return success

    async def undo(self) -> bool:
        """
        Undo the most recent command.

        Returns:
            bool: True if a command was undone successfully
        """
        if not self.history:
            return False

        command = self.history.pop()
        success = await command.undo()

        if success:
            self.undone.append(command)

        return success

    async def redo(self) -> bool:
        """
        Redo the most recently undone command.

        Returns:
            bool: True if a command was redone successfully
        """
        if not self.undone:
            return False

        command = self.undone.pop()
        success = await command.execute()

        if success:
            self.history.append(command)

        return success

    def can_undo(self) -> bool:
        """
        Check if there are commands that can be undone.

        Returns:
            bool: True if there are commands in the history
        """
        return len(self.history) > 0

    def can_redo(self) -> bool:
        """
        Check if there are commands that can be redone.

        Returns:
            bool: True if there are undone commands
        """
        return len(self.undone) > 0

    def clear_history(self) -> None:
        """Clear the command history and undone commands."""
        self.history.clear()
        self.undone.clear()
