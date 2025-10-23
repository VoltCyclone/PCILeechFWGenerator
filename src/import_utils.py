#!/usr/bin/env python3
"""
Import utilities to handle relative vs absolute imports gracefully.

This module provides helper functions to import modules in a way that works
both when running as a package and when running as standalone scripts.
"""

import importlib
import sys
from typing import Any, Optional

from src.string_utils import safe_format


def safe_import(module_name: str, relative_name: Optional[str] = None) -> Any:
    """
    Safely import a module, trying relative import first, then absolute.

    Args:
        module_name: The absolute module name (e.g., 'repo_manager')
        relative_name: The relative module name (e.g., '.repo_manager')

    Returns:
        The imported module

    Raises:
        ImportError: If neither import method works
    """
    # Try relative import first if provided and we have a package context
    if relative_name and __package__:
        try:
            return importlib.import_module(relative_name, package=__package__)
        except (ImportError, ValueError):
            pass

    # Try absolute import
    try:
        return importlib.import_module(module_name)
    except ImportError:
        pass

    # Try importing from current package context
    if __package__:
        try:
            full_name = f"{__package__}.{module_name}"
            return importlib.import_module(full_name)
        except ImportError:
            pass

    # Try adding src to path and importing
    import os

    src_path = os.path.join(os.path.dirname(__file__))
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(
            safe_format(
                "Could not import {module_name} using any method: {e}",
                module_name=module_name,
                e=e,
            )
        )


def safe_import_class(
    module_name: str, class_name: str, relative_name: Optional[str] = None
) -> Any:
    """
    Safely import a class from a module.

    Args:
        module_name: The absolute module name
        class_name: The class name to import
        relative_name: The relative module name (optional)

    Returns:
        The imported class

    Raises:
        ImportError: If the module or class cannot be imported
    """
    module = safe_import(module_name, relative_name)
    try:
        return getattr(module, class_name)
    except AttributeError:
        raise ImportError(
            safe_format(
                "Class {class_name} not found in module {module_name}",
                class_name=class_name,
                module_name=module_name,
            )
        )


def get_repo_manager():
    """Get RepoManager class with fallback handling."""
    try:
        return safe_import_class("repo_manager", "RepoManager", ".repo_manager")
    except ImportError:
        # Return a fallback class
        class FallbackRepoManager:
            @staticmethod
            def read_xdc_constraints(board: str) -> str:
                return safe_format(
                    "# Fallback XDC constraints for board: {board}\n"
                    "# RepoManager not available",
                    board=board,
                )

            @staticmethod
            def read_combined_xdc(board: str) -> str:
                return safe_format(
                    "# Fallback XDC constraints for board: {board}\n"
                    "# RepoManager not available",
                    board=board,
                )

            @staticmethod
            def ensure_git_repo():
                raise RuntimeError(
                    "RepoManager not available - git operations disabled"
                )

        return FallbackRepoManager
