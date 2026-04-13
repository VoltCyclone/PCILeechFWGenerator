#!/usr/bin/env python3
"""
Unit tests for pcileech_main.py entry point.

Verifies that all console script entry points correctly route commands
to pcileechfwgenerator submodules.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_pcileech_main_routes_version_command():
    """Test that pcileech_main routes the version command correctly."""
    from pcileechfwgenerator import pcileech_main

    with patch.object(sys, "argv", ["pcileech", "version"]):
        with patch(
            "pcileechfwgenerator.pcileech_main._handle_version", return_value=0
        ) as mock_handler:
            result = pcileech_main.main()
            assert result == 0
            mock_handler.assert_called_once()


def test_pcileech_main_returns_exit_code():
    """Test that pcileech_main returns handler exit codes."""
    from pcileechfwgenerator import pcileech_main

    with patch.object(sys, "argv", ["pcileech", "version"]):
        with patch(
            "pcileechfwgenerator.pcileech_main._handle_version", return_value=42
        ):
            result = pcileech_main.main()
            assert result == 42


def test_pcileech_main_handles_import_error():
    """Test that pcileech_main handles missing core modules gracefully."""
    from pcileechfwgenerator import pcileech_main

    # Simulate the core log_config import failing inside main()
    with patch.object(sys, "argv", ["pcileech", "version"]):
        with patch.dict(
            "sys.modules", {"pcileechfwgenerator.log_config": None}
        ):
            # main() catches ImportError from the log_config import and returns 1
            result = pcileech_main.main()
            assert result == 1


def test_pcileech_tui_auto_command():
    """Test that pcileech-tui auto-routes to the TUI handler."""
    from pcileechfwgenerator import pcileech_main

    with patch.object(sys, "argv", ["pcileech-tui"]):
        with patch(
            "pcileechfwgenerator.pcileech_main._handle_tui", return_value=0
        ) as mock_handler:
            result = pcileech_main.main()
            assert result == 0
            mock_handler.assert_called_once()


def test_pcileech_build_auto_command():
    """Test that pcileech-build auto-routes to the build handler."""
    from pcileechfwgenerator import pcileech_main

    # pcileech-build requires --bdf and --board, but the handler is mocked
    with patch.object(sys, "argv", ["pcileech-build"]):
        with patch(
            "pcileechfwgenerator.pcileech_main._handle_build", return_value=0
        ) as mock_handler:
            result = pcileech_main.main()
            assert result == 0
            mock_handler.assert_called_once()


def test_pcileech_main_does_not_import_pcileech_module():
    """Verify pcileech_main does NOT depend on the top-level pcileech module."""
    from pcileechfwgenerator import pcileech_main
    import inspect

    source = inspect.getsource(pcileech_main)
    # Should not import from the top-level pcileech module
    assert "from pcileech import" not in source
    # Check for bare "import pcileech" (not substring match on "pcileechfwgenerator")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import pcileech") and "pcileechfwgenerator" not in stripped:
            pytest.fail(f"Found forbidden import: {stripped}")


class TestPCILeechMainIntegration:
    """Integration tests for pcileech_main entry point."""

    def test_main_can_import_and_run(self):
        """Smoke test: verify pcileech_main can be imported and run."""
        from pcileechfwgenerator import pcileech_main

        assert hasattr(pcileech_main, "main")
        assert callable(pcileech_main.main)

    def test_console_script_entry_point_format(self):
        """Verify the entry point follows the correct format."""
        from pcileechfwgenerator import pcileech_main
        import inspect

        sig = inspect.signature(pcileech_main.main)
        assert sig.return_annotation == int or sig.return_annotation == "int"

    def test_no_command_shows_help(self):
        """Test that running with no command shows help and exits 1."""
        from pcileechfwgenerator import pcileech_main

        with patch.object(sys, "argv", ["pcileech"]):
            result = pcileech_main.main()
            assert result == 1
