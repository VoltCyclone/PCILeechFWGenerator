"""
conftest.py for pcileechfwgenerator.

If you don't know what this is for, just leave it empty.
Read more about conftest.py under:
- https://docs.pytest.org/en/stable/fixture.html
- https://docs.pytest.org/en/stable/writing_plugins.html
"""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest


@pytest.fixture
def sample_pci_device():
    """Sample PCIDevice for testing"""
    try:
        from src.tui.models.device import PCIDevice
    except ImportError:
        pytest.skip("TUI models not available")

    return PCIDevice(
        bdf="0000:01:00.0",
        vendor_id="10de",
        device_id="1234",
        vendor_name="NVIDIA",
        device_name="Test GPU",
        device_class="Display controller",
        subsystem_vendor="10de",
        subsystem_device="1234",
        driver="nvidia",
        iommu_group="1",
        power_state="D0",
        link_speed="8.0 GT/s",
        bars=[],
        suitability_score=0.9,
        compatibility_issues=[],
    )


@pytest.fixture
def config_dialog():
    """Mock configuration dialog for testing"""
    try:
        from src.tui.main import ConfigurationDialog
    except ImportError:
        pytest.skip("TUI main module not available")

    dialog = ConfigurationDialog(Mock(), Mock())
    dialog.app = Mock()
    dialog.app.config_manager = Mock()
    dialog.query_one = Mock()
    dialog.dismiss = Mock()
    return dialog


@pytest.fixture
def mock_textual_app():
    """Mock Textual app for TUI testing"""
    app = Mock()
    app.notify = Mock()
    app.push_screen = Mock()
    app.query_one = Mock()
    app.config_manager = Mock()
    app.device_manager = Mock()
    app.build_orchestrator = Mock()
    app.status_monitor = Mock()
    return app


@pytest.fixture(scope="session", autouse=True)
def explicit_config_dir():
    """Configure a global DeviceConfigManager for tests.

    SECURITY: Explicitly set to None to enforce no default/generic configs.
    Tests must use live device detection or explicit in-memory fixtures only.
    This prevents insecure generic firmware generation.
    """
    try:
        import src.device_clone.device_config as dc
    except Exception:
        # If import fails, nothing to configure
        yield
        return

    prev = getattr(dc, "_config_manager", None)
    
    # Explicitly disable on-disk configs to enforce security principles
    dc._config_manager = None

    yield

    # Restore previous manager
    dc._config_manager = prev


# Common test fixtures to reduce duplication across test files
@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def valid_bdf():
    """Provide a valid BDF string for testing."""
    return "0000:03:00.0"


@pytest.fixture
def invalid_bdf():
    """Provide an invalid BDF string for testing."""
    return "invalid_bdf"


@pytest.fixture
def valid_board():
    """Provide a valid board name for testing."""
    return "pcileech_35t325_x1"


@pytest.fixture
def mock_subprocess():
    """Mock subprocess module for testing."""
    import subprocess
    from unittest.mock import patch
    
    with patch("subprocess.run", autospec=True) as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )
        yield mock_run


@pytest.fixture
def mock_open_file():
    """Mock open() for file operations."""
    from unittest.mock import mock_open, patch
    
    m = mock_open()
    with patch("builtins.open", m):
        yield m


@pytest.fixture
def sample_config_space():
    """Provide sample PCI configuration space data."""
    return "86801533020000000300020800000000040000f400000000000000000000000000000000000000000000000000000000"


@pytest.fixture
def sample_device_info():
    """Provide sample device information dictionary."""
    return {
        "vendor_id": "8086",
        "device_id": "1533",
        "class_code": "020000",
        "revision_id": "03",
        "subsystem_vendor_id": "8086",
        "subsystem_device_id": "0000",
        "bar0_size": "0x20000",
    }


@pytest.fixture
def mock_path_exists():
    """Mock Path.exists for testing."""
    from unittest.mock import patch
    
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = True
        yield mock_exists


@pytest.fixture
def mock_file_manager():
    """Create a mock FileManager for testing."""
    manager = MagicMock()
    manager.output_dir = Path("/tmp/output")
    manager.tcl_dir = Path("/tmp/output/tcl")
    manager.sv_dir = Path("/tmp/output/sv")
    manager.write_file = MagicMock()
    manager.create_directories = MagicMock()
    return manager
