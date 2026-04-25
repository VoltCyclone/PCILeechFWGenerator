"""Smoke test: every public top-level package and key submodule imports
cleanly against the installed ``pcileechfwgenerator`` distribution.

Catches the kind of regression that turned the legacy
``scripts/e2e_test_github_actions.py`` runner into 1400 lines of false
negatives — module renames, missing ``__init__.py`` exports, circular
imports introduced by refactors.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.e2e


# Public top-level packages — these are the user-facing surface that
# external scripts and the installed CLI consume. They must always import.
PUBLIC_PACKAGES = [
    "pcileechfwgenerator",
    "pcileechfwgenerator.cli",
    "pcileechfwgenerator.cli.cli",
    "pcileechfwgenerator.cli.config",
    "pcileechfwgenerator.cli.flash",
    "pcileechfwgenerator.cli.vfio",
    "pcileechfwgenerator.cli.vfio_handler",
    "pcileechfwgenerator.cli.version_checker",
    "pcileechfwgenerator.device_clone",
    "pcileechfwgenerator.device_clone.config_space_manager",
    "pcileechfwgenerator.device_clone.pcileech_context",
    "pcileechfwgenerator.device_clone.pcileech_generator",
    "pcileechfwgenerator.device_clone.donor_info_template",
    "pcileechfwgenerator.file_management",
    "pcileechfwgenerator.file_management.donor_dump_manager",
    "pcileechfwgenerator.file_management.file_manager",
    "pcileechfwgenerator.file_management.template_discovery",
    "pcileechfwgenerator.pci_capability",
    "pcileechfwgenerator.pci_capability.core",
    "pcileechfwgenerator.pci_capability.processor",
    "pcileechfwgenerator.pci_capability.rules",
    "pcileechfwgenerator.pci_capability.types",
    "pcileechfwgenerator.templating",
    "pcileechfwgenerator.templating.sv_module_generator",
    "pcileechfwgenerator.templating.sv_context_builder",
    "pcileechfwgenerator.templating.advanced_sv_perf",
    "pcileechfwgenerator.utils.version_resolver",
    "pcileechfwgenerator.string_utils",
    "pcileechfwgenerator.exceptions",
    "pcileechfwgenerator.__version__",
]


# TUI imports are guarded because they require optional ``[tui]`` extras
# (textual, rich). We test them only when textual is importable.
TUI_PACKAGES = [
    "pcileechfwgenerator.tui",
    "pcileechfwgenerator.tui.main",
    "pcileechfwgenerator.tui.models.config",
    "pcileechfwgenerator.tui.models.device",
    "pcileechfwgenerator.tui.utils.privilege_manager",
    "pcileechfwgenerator.tui.commands.command_manager",
]


@pytest.mark.parametrize("module_name", PUBLIC_PACKAGES)
def test_public_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", TUI_PACKAGES)
def test_tui_module_imports(module_name: str) -> None:
    pytest.importorskip("textual")
    importlib.import_module(module_name)


def test_version_attribute_present() -> None:
    """``pcileechfwgenerator.__version__`` is the canonical version
    accessor; every release artifact must expose a non-empty value."""
    pkg = importlib.import_module("pcileechfwgenerator")
    assert getattr(pkg, "__version__", "")
    assert pkg.__version__ != ""


def test_version_resolver_returns_value() -> None:
    """The runtime resolver should always return *something*, even on a
    bare source checkout. Falls back to ``0.0.0+unknown`` only if every
    other path fails."""
    from pcileechfwgenerator.utils.version_resolver import get_package_version

    version = get_package_version()
    assert isinstance(version, str)
    assert version
