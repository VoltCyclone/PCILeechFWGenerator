"""
Command Pattern Base Classes

This module contains the base Command classes and CommandManager implementation.
"""

from abc import ABC, abstractmethod
from typing import List


class Command(ABC):
    """
    Abstract base class for all commands.

    Commands encapsulate actions that can be executed and potentially undone.
    """

    @abstractmethod
    async def execute(self) -> bool:
        """
        Execute the command.

        Returns:
            bool: True if the command was executed successfully, False otherwise.
        """

    @abstractmethod
    async def undo(self) -> bool:
        """
        Undo the command.

        Returns:
            bool: True if the command was undone successfully, False otherwise.
        """


class CommandManager:
    """
    Manages command execution and history for undo/redo functionality.
    """

    def __init__(self) -> None:
        """Initialize the command manager with empty history."""
        self._history: List[Command] = []
        self._undo_position: int = -1

    async def execute(self, command: Command) -> bool:
        """
        Execute a command and add it to the history if successful.

        Args:
            command: The command to execute.

        Returns:
            bool: True if the command was executed successfully, False otherwise.
        """
        success = await command.execute()

        if success:
            # If we've undone commands and then execute a new one,
            # we need to clear any commands after the current undo position
            if self._undo_position < len(self._history) - 1:
                self._history = self._history[: self._undo_position + 1]

            self._history.append(command)
            self._undo_position = len(self._history) - 1

        return success

    async def undo(self) -> bool:
        """
        Undo the most recently executed command.

        Returns:
            bool: True if a command was successfully undone, False otherwise.
        """
        if self._undo_position >= 0:
            command = self._history[self._undo_position]
            success = await command.undo()

            if success:
                self._undo_position -= 1
                return True

        return False

    async def redo(self) -> bool:
        """
        Redo the most recently undone command.

        Returns:
            bool: True if a command was successfully redone, False otherwise.
        """
        if self._undo_position < len(self._history) - 1:
            self._undo_position += 1
            command = self._history[self._undo_position]
            return await command.execute()

        return False

    @property
    def can_undo(self) -> bool:
        """Check if there are commands that can be undone."""
        return self._undo_position >= 0

    @property
    def can_redo(self) -> bool:
        """Check if there are commands that can be redone."""
        return self._undo_position < len(self._history) - 1

    def clear_history(self) -> None:
        """Clear the command history."""
        self._history.clear()
        self._undo_position = -1
