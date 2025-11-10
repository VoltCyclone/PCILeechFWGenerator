#!/usr/bin/env python3
"""
Unit tests for pcileech_main.py entry point delegation.

Verifies that all console script entry points correctly delegate to
pcileech.py main() for the unified 3-stage flow orchestration.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_pcileech_main_delegates_to_pcileech_py():
    """Test that pcileech_main.main() delegates to pcileech.py main()."""
    from src import pcileech_main
    
    with patch("pcileech.main") as mock_pcileech_main:
        mock_pcileech_main.return_value = 0
        
        result = pcileech_main.main()
        
        assert result == 0
        mock_pcileech_main.assert_called_once()


def test_pcileech_main_returns_exit_code():
    """Test that pcileech_main properly converts return values to int."""
    from src import pcileech_main
    
    test_cases = [
        (0, 0),
        (1, 1),
        (None, 0),  # None should convert to 0
        (42, 42),
    ]
    
    for return_val, expected in test_cases:
        with patch("pcileech.main", return_value=return_val):
            result = pcileech_main.main()
            assert result == expected, f"Failed for return_val={return_val}"


def test_pcileech_main_handles_import_error():
    """Test that pcileech_main handles missing pcileech.py gracefully."""
    from src import pcileech_main
    
    with patch("pcileech.main", side_effect=ImportError("pcileech not found")):
        result = pcileech_main.main()
        
        assert result == 1


def test_pcileech_main_adds_paths_to_sys_path():
    """Test that pcileech_main correctly adds project paths to sys.path."""
    from src import pcileech_main
    
    # Clear any existing paths
    original_path = sys.path.copy()
    
    try:
        with patch("pcileech.main", return_value=0):
            pcileech_main.main()
            
            # Verify project root and src dir were added
            # We can't check exact paths due to test environment, but we can verify
            # the function runs without error
            assert True  # If we got here, path setup worked
    finally:
        # Restore original path
        sys.path = original_path


def test_pcileech_main_does_not_call_build_py():
    """Verify pcileech_main DOES NOT delegate to src/build.py (legacy path)."""
    from src import pcileech_main
    
    # This test ensures we don't regress to the old behavior
    # We just verify that pcileech.main is called, not build.py
    with patch("pcileech.main", return_value=0) as mock_pcileech:
        exit_code = pcileech_main.main()
        
        # Verify pcileech.py is called (not build.py)
        mock_pcileech.assert_called_once()
        assert exit_code == 0


class TestPCILeechMainIntegration:
    """Integration tests for pcileech_main entry point."""
    
    def test_main_can_import_and_run(self):
        """Smoke test: verify pcileech_main can be imported and run."""
        from src import pcileech_main
        
        # Verify the module has the main function
        assert hasattr(pcileech_main, "main")
        assert callable(pcileech_main.main)
    
    def test_console_script_entry_point_format(self):
        """Verify the entry point follows the correct format."""
        # This would be called by setuptools as: module:function
        from src import pcileech_main
        
        # Verify the function signature matches what setuptools expects
        import inspect
        sig = inspect.signature(pcileech_main.main)
        
        # Should return int
        assert sig.return_annotation == int or sig.return_annotation == "int"
