"""
Comprehensive unit tests for src/build.py

This test module provides complete coverage for all classes, functions,
and error scenarios in the PCILeech FPGA Firmware Builder.
"""

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

# Add project root to Python path for direct test execution
project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.build import (  # Exception classes; Data classes; Manager classes; Main class; CLI functions; Constants
    BUFFER_SIZE,
    CONFIG_SPACE_PATH_TEMPLATE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PROFILE_DURATION,
    FILE_WRITE_TIMEOUT,
    MAX_PARALLEL_FILE_WRITES,
    REQUIRED_MODULES,
    BuildConfiguration,
    ConfigurationError,
    ConfigurationManager,
    DeviceConfiguration,
    FileOperationError,
    FileOperationsManager,
    FirmwareBuilder,
    ModuleChecker,
    ModuleImportError,
    MSIXData,
    MSIXManager,
    MSIXPreloadError,
    PCILeechBuildError,
    VivadoIntegrationError,
    _display_summary,
    main,
    parse_args,
)
from src.error_utils import build_issue_report

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def valid_bdf():
    """Return a valid BDF string."""
    return "0000:03:00.0"


@pytest.fixture
def valid_board():
    """Return a valid board name."""
    return "pcileech_35t325_x4"


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return mock.MagicMock(spec=logging.Logger)


@pytest.fixture
def build_config(temp_dir, valid_bdf, valid_board):
    """Create a valid BuildConfiguration instance."""
    return BuildConfiguration(
        bdf=valid_bdf,
        board=valid_board,
        output_dir=temp_dir,
        enable_profiling=True,
        preload_msix=True,
        profile_duration=30,
        parallel_writes=True,
        max_workers=4,
    )


@pytest.fixture
def msix_data_empty():
    """Create an empty MSIXData instance."""
    return MSIXData(preloaded=False)


@pytest.fixture
def msix_data_valid():
    """Create a valid MSIXData instance with preloaded data."""
    msix_info = {
        "table_size": 16,
        "table_bir": 0,
        "table_offset": 0x1000,
        "pba_bir": 0,
        "pba_offset": 0x2000,
        "enabled": True,
        "function_mask": False,
    }
    return MSIXData(
        preloaded=True,
        msix_info=msix_info,
        config_space_hex="deadbeef",
        config_space_bytes=b"\xde\xad\xbe\xef",
    )


@pytest.fixture
def device_config():
    """Create a valid DeviceConfiguration instance."""
    return DeviceConfiguration(
        vendor_id=0x1234,
        device_id=0x5678,
        revision_id=0x01,
        class_code=0x030000,  # Display controller
        requires_msix=True,
        pcie_lanes=4,
    )


@pytest.fixture
def mock_args():
    """Create a mock argparse.Namespace with valid arguments."""
    args = mock.MagicMock(spec=argparse.Namespace)
    args.bdf = "0000:03:00.0"
    args.board = "pcileech_35t325_x4"
    args.output = "output"
    args.profile = 30
    args.preload_msix = True
    args.vivado = False
    return args


@pytest.fixture
def mock_generation_result():
    """Create a mock generation result dictionary."""
    return {
        "systemverilog_modules": {
            "module1.sv": "module module1; endmodule",
            "module2.sv": "module module2; endmodule",
            "config.coe": "memory_initialization_radix=16;",
        },
        "template_context": {
            "device_config": {
                "vendor_id": 0x1234,
                "device_id": 0x5678,
                "revision_id": 0x01,
                "class_code": 0x030000,
            },
            "pcie_config": {
                "max_lanes": 4,
            },
            "msix_config": {
                "is_supported": False,
                "num_vectors": 0,
            },
        },
        "config_space_data": {
            "device_info": {
                "vendor_id": "0x1234",
                "device_id": "0x5678",
            },
        },
        "msix_data": None,
    }


# ============================================================================
# Test Exception Classes
# ============================================================================


def test_pcileech_build_error():
    """Test PCILeechBuildError exception."""
    error = PCILeechBuildError("Test error")
    assert str(error) == "Test error"
    assert isinstance(error, Exception)


def test_module_import_error():
    """Test ModuleImportError exception."""
    error = ModuleImportError("Module not found")
    assert str(error) == "Module not found"
    assert isinstance(error, PCILeechBuildError)


def test_msix_preload_error():
    """Test MSIXPreloadError exception."""
    error = MSIXPreloadError("Failed to preload MSI-X data")
    assert str(error) == "Failed to preload MSI-X data"
    assert isinstance(error, PCILeechBuildError)


def test_file_operation_error():
    """Test FileOperationError exception."""
    error = FileOperationError("Failed to write file")
    assert str(error) == "Failed to write file"
    assert isinstance(error, PCILeechBuildError)


def test_vivado_integration_error():
    """Test VivadoIntegrationError exception."""
    error = VivadoIntegrationError("Vivado integration failed")
    assert str(error) == "Vivado integration failed"
    assert isinstance(error, PCILeechBuildError)


def test_configuration_error():
    """Test ConfigurationError exception."""
    error = ConfigurationError("Invalid configuration")
    assert str(error) == "Invalid configuration"
    assert isinstance(error, PCILeechBuildError)


# =========================================================================
# Issue report generation
# =========================================================================


def test_build_issue_report_basic():
    """Ensure build_issue_report returns required keys and is JSON serializable."""
    try:
        raise ValueError("Synthetic failure for report")
    except Exception as exc:  # noqa: PIE786
        report = build_issue_report(
            exc,
            context="unit-test",
            build_args=["--bdf", "0000:03:00.0", "--board", "pcileech_35t325_x4"],
            extra_metadata={"selected_board": "pcileech_35t325_x4"},
            include_traceback=True,
        )

    # Required top-level keys
    for key in [
        "schema_version",
        "timestamp_utc",
        "error",
        "environment",
        "build",
        "user_actionable",
    ]:
        assert key in report

    # Error section basics
    assert report["error"]["root_cause"] == "Synthetic failure for report"
    assert isinstance(report["error"].get("exception_chain"), list)

    # JSON serializable
    serialized = json.dumps(report)
    assert "Synthetic failure" in serialized


def test_reproduction_command_basic():
    """Reproduction command includes core args; omits issue-report flags."""
    # Local import to avoid circular import during test collection
    from src.build import _build_reproduction_command

    args = argparse.Namespace(
        bdf="0000:03:00.0",
        board="pcileech_35t325_x4",
        profile=45,
        donor_template="donor.json",
        output_template="out_template.json",
        vivado=True,
        vivado_path="/tools/Xilinx/2025.1/Vivado",
        vivado_jobs=8,  # non-default so it should appear
        vivado_timeout=7200,  # non-default so it should appear
        preload_msix=False,  # triggers --no-preload-msix flag
        enable_error_injection=True,
        issue_report_json="failure.json",  # should be excluded
        print_issue_report=True,  # should be excluded
        no_repro_hint=False,
        output="output",  # not used in repro command currently
    )

    cmd = _build_reproduction_command(args)

    # Must include core mandatory flags & values
    assert "--bdf 0000:03:00.0" in cmd
    assert "--board pcileech_35t325_x4" in cmd
    assert "--profile 45" in cmd
    assert "--donor-template donor.json" in cmd
    assert "--output-template out_template.json" in cmd
    assert "--vivado" in cmd
    assert "--vivado-path /tools/Xilinx/2025.1/Vivado" in cmd
    assert "--vivado-jobs 8" in cmd
    assert "--vivado-timeout 7200" in cmd
    assert "--no-preload-msix" in cmd
    assert "--enable-error-injection" in cmd

    # Must not include issue reporting flags
    assert "issue-report" not in cmd
    assert "print-issue-report" not in cmd


def test_reproduction_command_defaults_minimal():
    """Ensure defaults omitted; msix preload enabled doesn't add extra flag."""
    from src.build import _build_reproduction_command

    args = argparse.Namespace(
        bdf="0000:04:00.0",
        board="pcileech_35t325_x4",
        profile=30,  # default
        donor_template=None,
        output_template=None,
        vivado=False,
        vivado_path=None,
        vivado_jobs=4,  # default -> should be omitted
        vivado_timeout=3600,  # default -> should be omitted
        preload_msix=True,  # default -> should not add --no-preload-msix
        enable_error_injection=False,
        issue_report_json=None,
        print_issue_report=False,
        no_repro_hint=False,
        output="output",
    )

    cmd = _build_reproduction_command(args)

    assert cmd.startswith("python3 -m src.build")
    assert "--bdf 0000:04:00.0" in cmd
    assert "--board pcileech_35t325_x4" in cmd
    # Default values should not introduce these flags
    assert "--vivado-jobs" not in cmd
    assert "--vivado-timeout" not in cmd
    assert "--no-preload-msix" not in cmd
    assert "--enable-error-injection" not in cmd


def test_reproduction_command_suppressed(mock_logger):
    """Ensure reproduction hint is not logged when --no-repro-hint flag is set."""
    from src.build import _maybe_emit_issue_report

    args = argparse.Namespace(
        bdf="0000:05:00.0",
        board="pcileech_35t325_x4",
        profile=30,
        donor_template=None,
        output_template=None,
        vivado=False,
        vivado_path=None,
        vivado_jobs=4,
        vivado_timeout=3600,
        preload_msix=True,
        enable_error_injection=False,
        issue_report_json=None,
        print_issue_report=False,
        no_repro_hint=True,  # suppression active
        output="output",
    )

    # Invoke with synthetic failure
    _maybe_emit_issue_report(ValueError("boom"), mock_logger, args)

    # Ensure no reproduction hint logged
    if hasattr(mock_logger, "info"):
        for call in mock_logger.info.call_args_list:
            assert "Reproduce with:" not in (call.args[0] if call.args else "")


# ============================================================================
# Test Data Classes
# ============================================================================


def test_build_configuration():
    """Test BuildConfiguration data class."""
    config = BuildConfiguration(
        bdf="0000:03:00.0",
        board="pcileech_35t325_x4",
        output_dir=Path("output"),
        enable_profiling=True,
        preload_msix=True,
        profile_duration=30,
        parallel_writes=True,
        max_workers=4,
    )

    assert config.bdf == "0000:03:00.0"
    assert config.board == "pcileech_35t325_x4"
    assert config.output_dir == Path("output")
    assert config.enable_profiling is True
    assert config.preload_msix is True
    assert config.profile_duration == 30
    assert config.parallel_writes is True
    assert config.max_workers == 4


def test_msix_data():
    """Test MSIXData data class."""
    # Test with minimal data
    msix_data = MSIXData(preloaded=False)
    assert msix_data.preloaded is False
    assert msix_data.msix_info is None
    assert msix_data.config_space_hex is None
    assert msix_data.config_space_bytes is None

    # Test with full data
    msix_info = {"table_size": 16}
    msix_data = MSIXData(
        preloaded=True,
        msix_info=msix_info,
        config_space_hex="deadbeef",
        config_space_bytes=b"\xde\xad\xbe\xef",
    )
    assert msix_data.preloaded is True
    assert msix_data.msix_info == msix_info
    assert msix_data.config_space_hex == "deadbeef"
    assert msix_data.config_space_bytes == b"\xde\xad\xbe\xef"


def test_device_configuration():
    """Test DeviceConfiguration data class."""
    config = DeviceConfiguration(
        vendor_id=0x1234,
        device_id=0x5678,
        revision_id=0x01,
        class_code=0x030000,
        requires_msix=True,
        pcie_lanes=4,
    )

    assert config.vendor_id == 0x1234
    assert config.device_id == 0x5678
    assert config.revision_id == 0x01
    assert config.class_code == 0x030000
    assert config.requires_msix is True
    assert config.pcie_lanes == 4


# ============================================================================
# Test ModuleChecker Class
# ============================================================================


def test_module_checker_init():
    """Test ModuleChecker initialization."""
    required_modules = ["module1", "module2"]
    checker = ModuleChecker(required_modules)

    assert checker.required_modules == required_modules
    assert checker.logger is not None


def test_module_checker_check_all_success():
    """Test ModuleChecker.check_all() with all modules available."""
    # Mock successful imports
    with mock.patch.object(ModuleChecker, "_check_module") as mock_check:
        checker = ModuleChecker(["os", "sys"])
        checker.check_all()

        assert mock_check.call_count == 2
        mock_check.assert_any_call("os")
        mock_check.assert_any_call("sys")


def test_module_checker_check_all_failure():
    """Test ModuleChecker.check_all() with missing module."""
    # Create a checker with a non-existent module
    checker = ModuleChecker(["non_existent_module"])

    # Should raise ModuleImportError
    with pytest.raises(ModuleImportError):
        checker.check_all()


def test_module_checker_check_module_success():
    """Test ModuleChecker._check_module() with available module."""
    checker = ModuleChecker([])

    # Should not raise an exception
    checker._check_module("os")


def test_module_checker_check_module_failure():
    """Test ModuleChecker._check_module() with missing module."""
    checker = ModuleChecker([])

    # Should raise ModuleImportError
    with pytest.raises(ModuleImportError):
        checker._check_module("non_existent_module")


def test_module_checker_handle_import_error():
    """Test ModuleChecker._handle_import_error()."""
    checker = ModuleChecker([])

    # Mock _gather_diagnostics
    with mock.patch.object(
        checker,
        "_gather_diagnostics",
        return_value="Diagnostics",
    ):
        with pytest.raises(ModuleImportError) as excinfo:
            checker._handle_import_error("test_module", ImportError("Test error"))

        # Check error message
        assert "test_module" in str(excinfo.value)
        assert "Diagnostics" in str(excinfo.value)


def test_module_checker_gather_diagnostics():
    """Test ModuleChecker._gather_diagnostics()."""
    checker = ModuleChecker([])

    # Test with a real module
    diagnostics = checker._gather_diagnostics("os")

    # Check that diagnostics contains expected information
    assert "DIAGNOSTICS" in diagnostics
    assert "Python version" in diagnostics
    assert "PYTHONPATH" in diagnostics
    assert "Current directory" in diagnostics


# ============================================================================
# Test MSIXManager Class
# ============================================================================


def test_msix_manager_init(valid_bdf, mock_logger):
    """Test MSIXManager initialization."""
    manager = MSIXManager(valid_bdf, mock_logger)

    assert manager.bdf == valid_bdf
    assert manager.logger == mock_logger


def test_msix_manager_init_default_logger(valid_bdf):
    """Test MSIXManager initialization with default logger."""
    manager = MSIXManager(valid_bdf)

    assert manager.bdf == valid_bdf
    assert manager.logger is not None


def test_msix_manager_preload_data_success(valid_bdf, mock_logger):
    """Test MSIXManager.preload_data() success case."""
    manager = MSIXManager(valid_bdf, mock_logger)

    # Mock config space path existence and read_config_space
    config_path = CONFIG_SPACE_PATH_TEMPLATE.format(valid_bdf)

    with mock.patch("os.path.exists", return_value=True), mock.patch.object(
        manager, "_read_config_space", return_value=b"\xde\xad\xbe\xef"
    ), mock.patch(
        "src.build.parse_msix_capability",
        return_value={"table_size": 16},
    ):

        result = manager.preload_data()

        assert result.preloaded is True
        assert result.msix_info == {"table_size": 16}
        assert result.config_space_hex == "deadbeef"
        assert result.config_space_bytes == b"\xde\xad\xbe\xef"


def test_msix_manager_preload_data_no_config_space(valid_bdf, mock_logger):
    """Test MSIXManager.preload_data() when config space is not accessible."""
    manager = MSIXManager(valid_bdf, mock_logger)

    # Mock config space path not existing
    with mock.patch("os.path.exists", return_value=False):
        result = manager.preload_data()

        assert result.preloaded is False
        assert result.msix_info is None
        assert result.config_space_hex is None
        assert result.config_space_bytes is None


def test_msix_manager_preload_data_no_msix(valid_bdf, mock_logger):
    """Test MSIXManager.preload_data() when no MSI-X capability is found."""
    manager = MSIXManager(valid_bdf, mock_logger)

    # Mock config space path existence and read_config_space
    with mock.patch("os.path.exists", return_value=True), mock.patch.object(
        manager, "_read_config_space", return_value=b"\xde\xad\xbe\xef"
    ), mock.patch("src.build.parse_msix_capability", return_value={"table_size": 0}):
        result = manager.preload_data()

        # New behavior: treat absence of MSI-X capability as not preloaded
        assert result.preloaded is False
        assert result.msix_info is None
        assert result.config_space_hex is None
        assert result.config_space_bytes is None


def test_msix_manager_preload_data_exception(valid_bdf, mock_logger):
    """Test MSIXManager.preload_data() when an exception occurs."""
    manager = MSIXManager(valid_bdf, mock_logger)

    # Mock config space path existence but raise exception in read_config_space
    with mock.patch("os.path.exists", return_value=True), mock.patch.object(
        manager, "_read_config_space", side_effect=IOError("Test error")
    ):

        result = manager.preload_data()

        assert result.preloaded is False
        assert result.msix_info is None
        assert result.config_space_hex is None
        assert result.config_space_bytes is None


def test_msix_manager_inject_data_with_valid_data(msix_data_valid, mock_logger):
    """Test MSIXManager.inject_data() with valid MSI-X data."""
    manager = MSIXManager("0000:03:00.0", mock_logger)

    # Create a result dictionary to update
    result = {
        "template_context": {
            "msix_config": {
                "is_supported": False,
                "num_vectors": 0,
            }
        }
    }

    manager.inject_data(result, msix_data_valid)

    # Check that MSI-X data was injected
    assert "msix_data" in result
    assert result["msix_data"]["table_size"] == 16
    assert result["msix_data"]["is_valid"] is True

    # Check that template context was updated
    assert result["template_context"]["msix_config"]["is_supported"] is True
    assert result["template_context"]["msix_config"]["num_vectors"] == 16


def test_msix_manager_inject_data_with_empty_data(msix_data_empty, mock_logger):
    """Test MSIXManager.inject_data() with empty MSI-X data."""
    manager = MSIXManager("0000:03:00.0", mock_logger)

    # Create a result dictionary to update
    result = {
        "template_context": {
            "msix_config": {
                "is_supported": False,
                "num_vectors": 0,
            }
        }
    }

    manager.inject_data(result, msix_data_empty)

    # Check that MSI-X data was not injected
    assert "msix_data" not in result

    # Check that template context was not updated
    assert result["template_context"]["msix_config"]["is_supported"] is False
    assert result["template_context"]["msix_config"]["num_vectors"] == 0


def test_msix_manager_inject_data_without_template_context(
    msix_data_valid, mock_logger
):
    """Test MSIXManager.inject_data() without template_context in result."""
    manager = MSIXManager("0000:03:00.0", mock_logger)

    # Create a result dictionary without template_context
    result = {}

    manager.inject_data(result, msix_data_valid)

    # Check that MSI-X data was injected
    assert "msix_data" in result
    assert result["msix_data"]["table_size"] == 16
    assert result["msix_data"]["is_valid"] is True


def test_msix_manager_read_config_space(valid_bdf, mock_logger, temp_dir):
    """Test MSIXManager._read_config_space()."""
    manager = MSIXManager(valid_bdf, mock_logger)

    # Create a temporary file with test content
    test_content = b"\xde\xad\xbe\xef"
    test_file = temp_dir / "config"
    with open(test_file, "wb") as f:
        f.write(test_content)

    # Test reading the file
    with mock.patch(
        "builtins.open", mock.mock_open(read_data=test_content)
    ) as mock_file:
        result = manager._read_config_space(str(test_file))

        assert result == test_content
        mock_file.assert_called_once_with(str(test_file), "rb")


def test_msix_manager_should_inject(msix_data_valid, msix_data_empty, mock_logger):
    """Test MSIXManager._should_inject()."""
    manager = MSIXManager("0000:03:00.0", mock_logger)

    # Test with valid MSI-X data
    assert manager._should_inject(msix_data_valid) is True

    # Test with empty MSI-X data
    assert manager._should_inject(msix_data_empty) is False

    # Test with preloaded but no MSI-X capability
    msix_data_no_capability = MSIXData(preloaded=True, msix_info={"table_size": 0})
    assert manager._should_inject(msix_data_no_capability) is False


def test_msix_manager_create_msix_result(mock_logger):
    """Test MSIXManager._create_msix_result()."""
    manager = MSIXManager("0000:03:00.0", mock_logger)

    msix_info = {
        "table_size": 16,
        "table_bir": 0,
        "table_offset": 0x1000,
        "pba_bir": 0,
        "pba_offset": 0x2000,
        "enabled": True,
        "function_mask": False,
    }

    result = manager._create_msix_result(msix_info)

    assert result["capability_info"] == msix_info
    assert result["table_size"] == 16
    assert result["table_bir"] == 0
    assert result["table_offset"] == 0x1000
    assert result["pba_bir"] == 0
    assert result["pba_offset"] == 0x2000
    assert result["enabled"] is True
    assert result["function_mask"] is False
    assert result["is_valid"] is True
    assert result["validation_errors"] == []


# ============================================================================
# Test FileOperationsManager Class
# ============================================================================


def test_file_operations_manager_init(temp_dir, mock_logger):
    """Test FileOperationsManager initialization."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    assert manager.output_dir == temp_dir
    assert manager.parallel is True
    assert manager.max_workers == 4
    assert manager.logger == mock_logger

    # Check that output directory was created
    assert temp_dir.exists()


def test_file_operations_manager_init_default_logger(temp_dir):
    """Test FileOperationsManager initialization with default logger."""
    manager = FileOperationsManager(temp_dir)

    assert manager.output_dir == temp_dir
    assert manager.parallel is True
    assert manager.max_workers == MAX_PARALLEL_FILE_WRITES
    assert manager.logger is not None


def test_file_operations_manager_write_systemverilog_modules(temp_dir, mock_logger):
    """Test FileOperationsManager.write_systemverilog_modules()."""
    manager = FileOperationsManager(temp_dir, False, 4, mock_logger)

    # Create test modules (COE files are skipped by design)
    modules = {
        "module1": "module module1; endmodule",
        "module2.sv": "module module2; endmodule",
        "config.coe": "memory_initialization_radix=16;",  # This will be skipped
    }

    # Mock _sequential_write to avoid actual file operations
    with mock.patch.object(manager, "_sequential_write") as mock_write:
        sv_files, special_files = manager.write_systemverilog_modules(modules)

        # Check that files were categorized correctly
        # COE files are skipped, so only SV files should be present
        assert set(sv_files) == {"module1.sv", "module2.sv"}
        assert set(special_files) == set()  # Empty because COE files are skipped

        # Check that _sequential_write was called with correct arguments
        assert mock_write.call_count == 1
        args = mock_write.call_args[0][0]
        assert len(args) == 2  # Only 2 files (COE file is skipped)

        # Check paths and contents
        paths = [path for path, _ in args]
        assert any(path.name == "module1.sv" for path in paths)
        assert any(path.name == "module2.sv" for path in paths)
        # COE file should NOT be in the paths
        assert not any(path.name == "config.coe" for path in paths)


def test_file_operations_manager_write_systemverilog_modules_parallel(
    temp_dir, mock_logger
):
    """Test parallel SystemVerilog module writes."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create test modules
    modules = {
        "module1": "module module1; endmodule",
        "module2.sv": "module module2; endmodule",
    }

    # Mock _parallel_write to avoid actual file operations
    with mock.patch.object(manager, "_parallel_write") as mock_write:
        sv_files, special_files = manager.write_systemverilog_modules(modules)

        # Check that _parallel_write was called
        assert mock_write.call_count == 1


def test_file_operations_manager_write_systemverilog_modules_sequential_single(
    temp_dir, mock_logger
):
    """Test sequential write path with single file."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create test modules with only one file
    modules = {
        "module1": "module module1; endmodule",
    }

    # Mock _sequential_write to avoid actual file operations
    with mock.patch.object(manager, "_sequential_write") as mock_write:
        sv_files, special_files = manager.write_systemverilog_modules(modules)

        # Check that _sequential_write was called (not parallel for single file)
        assert mock_write.call_count == 1


def test_file_operations_manager_write_json(temp_dir, mock_logger):
    """Test FileOperationsManager.write_json()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Test data
    data = {"key": "value", "nested": {"key": "value"}}

    # Mock both open and json.dump
    with mock.patch("builtins.open", mock.mock_open()) as mock_file, mock.patch(
        "json.dump"
    ) as mock_json_dump:

        manager.write_json("test.json", data)

        # Check that file was opened correctly
        mock_file.assert_called_once_with(
            temp_dir / "test.json", "w", buffering=BUFFER_SIZE
        )

        # Check that json.dump was called with correct arguments
        handle = mock_file()
        mock_json_dump.assert_called_once()
        args, kwargs = mock_json_dump.call_args
        assert args[0] == data  # First arg is data
        assert args[1] == handle  # Second arg is file handle
        assert kwargs["indent"] == 2


def test_file_operations_manager_write_json_error(temp_dir, mock_logger):
    """Test FileOperationsManager.write_json() with error."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Test data
    data = {"key": "value"}

    # Mock open to raise an exception
    with mock.patch("builtins.open", side_effect=IOError("Test error")):
        with pytest.raises(FileOperationError) as excinfo:
            manager.write_json("test.json", data)

        # Check error message
        assert "Failed to write JSON file" in str(excinfo.value)
        assert "test.json" in str(excinfo.value)


def test_file_operations_manager_write_text(temp_dir, mock_logger):
    """Test FileOperationsManager.write_text()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Test content
    content = "Test content"

    # Mock open to avoid actual file operations
    with mock.patch("builtins.open", mock.mock_open()) as mock_file:
        manager.write_text("test.txt", content)

        # Check that file was opened correctly
        mock_file.assert_called_once_with(
            temp_dir / "test.txt", "w", buffering=BUFFER_SIZE
        )

        # Check that write was called with correct content
        handle = mock_file()
        handle.write.assert_called_once_with(content)


def test_file_operations_manager_write_text_error(temp_dir, mock_logger):
    """Test FileOperationsManager.write_text() with error."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Test content
    content = "Test content"

    # Mock open to raise an exception
    with mock.patch("builtins.open", side_effect=IOError("Test error")):
        with pytest.raises(FileOperationError) as excinfo:
            manager.write_text("test.txt", content)

        # Check error message
        assert "Failed to write text file" in str(excinfo.value)
        assert "test.txt" in str(excinfo.value)


def test_file_operations_manager_list_artifacts(temp_dir, mock_logger):
    """Test FileOperationsManager.list_artifacts()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create some test files
    (temp_dir / "file1.txt").touch()
    (temp_dir / "subdir").mkdir()
    (temp_dir / "subdir" / "file2.txt").touch()

    # Get artifacts
    artifacts = manager.list_artifacts()

    # Check that all files are listed
    assert set(artifacts) == {"file1.txt", "subdir/file2.txt"}


def test_file_operations_manager_determine_file_path(temp_dir, mock_logger):
    """Test FileOperationsManager._determine_file_path()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Test with SystemVerilog file (no extension)
    path, category = manager._determine_file_path("module1", temp_dir)
    assert path == temp_dir / "module1.sv"
    assert category == "sv"

    # Test with SystemVerilog file (with extension)
    path, category = manager._determine_file_path("module2.sv", temp_dir)
    assert path == temp_dir / "module2.sv"
    assert category == "sv"

    # Test with special file
    path, category = manager._determine_file_path("config.coe", temp_dir)
    assert path == temp_dir / "config.coe"
    assert category == "special"


def test_file_operations_manager_parallel_write(temp_dir, mock_logger):
    """Test FileOperationsManager._parallel_write()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create test write tasks
    write_tasks = [
        (temp_dir / "file1.txt", "Content 1"),
        (temp_dir / "file2.txt", "Content 2"),
    ]

    # Mock ThreadPoolExecutor and Future
    mock_future = mock.MagicMock(spec=Future)
    mock_future.result.return_value = None

    with mock.patch("src.build.ThreadPoolExecutor") as mock_executor_cls, mock.patch(
        "src.build.as_completed", return_value=[mock_future]
    ):

        mock_executor = mock_executor_cls.return_value.__enter__.return_value
        mock_executor.submit.return_value = mock_future

        # Call the method
        manager._parallel_write(write_tasks)

        # Check that executor was used correctly
        assert mock_executor.submit.call_count == 2
        mock_executor.submit.assert_any_call(
            manager._write_single_file, write_tasks[0][0], write_tasks[0][1]
        )
        mock_executor.submit.assert_any_call(
            manager._write_single_file, write_tasks[1][0], write_tasks[1][1]
        )


def test_file_operations_manager_parallel_write_error(temp_dir, mock_logger):
    """Test FileOperationsManager._parallel_write() with error."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create test write tasks
    write_tasks = [
        (temp_dir / "file1.txt", "Content 1"),
    ]

    # Mock ThreadPoolExecutor and Future
    mock_future = mock.MagicMock(spec=Future)
    mock_future.result.side_effect = IOError("Test error")

    with mock.patch("src.build.ThreadPoolExecutor") as mock_executor_cls, mock.patch(
        "src.build.as_completed", return_value=[mock_future]
    ):

        mock_executor = mock_executor_cls.return_value.__enter__.return_value
        mock_executor.submit.return_value = mock_future

        # Call the method and check for exception
        with pytest.raises(FileOperationError) as excinfo:
            manager._parallel_write(write_tasks)

        # Check error message
        assert "Failed to write file" in str(excinfo.value)


def test_file_operations_manager_parallel_write_timeout(temp_dir, mock_logger):
    """Test FileOperationsManager._parallel_write() with timeout."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create test write tasks
    write_tasks = [
        (temp_dir / "file1.txt", "Content 1"),
    ]

    # Mock ThreadPoolExecutor and Future
    mock_future = mock.MagicMock(spec=Future)
    mock_future.result.side_effect = TimeoutError("Test timeout")

    with mock.patch("src.build.ThreadPoolExecutor") as mock_executor_cls, mock.patch(
        "src.build.as_completed", return_value=[mock_future]
    ):

        mock_executor = mock_executor_cls.return_value.__enter__.return_value
        mock_executor.submit.return_value = mock_future

        # Call the method and check for exception
        with pytest.raises(FileOperationError) as excinfo:
            manager._parallel_write(write_tasks)

        # Check error message
        assert "timeout" in str(excinfo.value).lower()


def test_file_operations_manager_sequential_write(temp_dir, mock_logger):
    """Test FileOperationsManager._sequential_write()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create test write tasks
    write_tasks = [
        (temp_dir / "file1.txt", "Content 1"),
        (temp_dir / "file2.txt", "Content 2"),
    ]

    # Mock _write_single_file to avoid actual file operations
    with mock.patch.object(manager, "_write_single_file") as mock_write:
        # Call the method
        manager._sequential_write(write_tasks)

        # Check that _write_single_file was called for each task
        assert mock_write.call_count == 2
        mock_write.assert_any_call(write_tasks[0][0], write_tasks[0][1])
        mock_write.assert_any_call(write_tasks[1][0], write_tasks[1][1])


def test_file_operations_manager_sequential_write_error(temp_dir, mock_logger):
    """Test FileOperationsManager._sequential_write() with error."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Create test write tasks
    write_tasks = [
        (temp_dir / "file1.txt", "Content 1"),
    ]

    # Mock _write_single_file to raise an exception
    with mock.patch.object(
        manager, "_write_single_file", side_effect=IOError("Test error")
    ):
        # Call the method and check for exception
        with pytest.raises(FileOperationError) as excinfo:
            manager._sequential_write(write_tasks)

        # Check error message
        assert "Failed to write file" in str(excinfo.value)


def test_file_operations_manager_write_single_file(temp_dir, mock_logger):
    """Test FileOperationsManager._write_single_file()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Test file path and content
    file_path = temp_dir / "test.txt"
    content = "Test content"

    # Create a mock for builtins.open to properly track calls
    m = mock.mock_open()
    with mock.patch("builtins.open", m):
        # Call the method
        manager._write_single_file(file_path, content)

        # Check that file was opened correctly with encoding parameter
        # Note: mock_open creates a call with the arguments we expect
        m.assert_called_once_with(
            file_path, "w", buffering=BUFFER_SIZE, encoding="utf-8"
        )

        # Check that write was called with correct content
        handle = m()
        handle.write.assert_called_once_with(content)


def test_file_operations_manager_json_serialize_default(temp_dir, mock_logger):
    """Test FileOperationsManager._json_serialize_default()."""
    manager = FileOperationsManager(temp_dir, True, 4, mock_logger)

    # Test with object that has __dict__

    class TestObject:
        def __init__(self):
            self.attr1 = "value1"
            self.attr2 = "value2"

    test_obj = TestObject()
    result = manager._json_serialize_default(test_obj)
    assert result == {"attr1": "value1", "attr2": "value2"}

    # Test with object that doesn't have __dict__

    class TestObject2:
        __slots__ = ["attr1", "attr2"]

        def __init__(self):
            self.attr1 = "value1"
            self.attr2 = "value2"

    test_obj2 = TestObject2()
    result = manager._json_serialize_default(test_obj2)
    assert isinstance(result, str)


# ============================================================================
# Test ConfigurationManager Class
# ============================================================================


def test_configuration_manager_init(mock_logger):
    """Test ConfigurationManager initialization."""
    manager = ConfigurationManager(mock_logger)

    assert manager.logger == mock_logger


def test_configuration_manager_init_default_logger():
    """Test ConfigurationManager initialization with default logger."""
    manager = ConfigurationManager()

    assert manager.logger is not None


def test_configuration_manager_create_from_args(mock_args, mock_logger):
    """Test ConfigurationManager.create_from_args()."""
    manager = ConfigurationManager(mock_logger)

    # Mock _validate_args to avoid validation
    with mock.patch.object(manager, "_validate_args"):
        config = manager.create_from_args(mock_args)

        # Check that config was created correctly
        assert config.bdf == mock_args.bdf
        assert config.board == mock_args.board
        assert config.output_dir == Path(mock_args.output).resolve()
        assert config.enable_profiling == (mock_args.profile > 0)
        assert config.preload_msix == mock_args.preload_msix
        assert config.profile_duration == mock_args.profile


# ============================================================================
# Test FirmwareBuilder Class
# ============================================================================


def test_firmware_builder_init(build_config, mock_logger):
    """Test FirmwareBuilder initialization."""
    builder = FirmwareBuilder(build_config, logger=mock_logger)

    assert builder.config == build_config
    assert builder.logger == mock_logger
    assert builder._device_config is None
    assert hasattr(builder, "msix_manager")
    assert hasattr(builder, "file_manager")
    assert hasattr(builder, "config_manager")
    assert hasattr(builder, "gen")
    # TCLBuilder removed - now uses static TCL files from submodule
    assert hasattr(builder, "profiler")


def test_firmware_builder_init_dependency_injection(build_config, mock_logger):
    """Test FirmwareBuilder initialization with dependency injection."""
    msix_manager = mock.MagicMock()
    file_manager = mock.MagicMock()
    config_manager = mock.MagicMock()

    builder = FirmwareBuilder(
        build_config,
        msix_manager=msix_manager,
        file_manager=file_manager,
        config_manager=config_manager,
        logger=mock_logger,
    )

    assert builder.msix_manager == msix_manager
    assert builder.file_manager == file_manager
    assert builder.config_manager == config_manager


@pytest.fixture
def mock_firmware_builder(build_config, mock_logger):
    """Create a FirmwareBuilder with mocked dependencies."""
    with mock.patch("src.build.MSIXManager") as mock_msix_cls, mock.patch(
        "src.build.FileOperationsManager"
    ) as mock_file_cls, mock.patch(
        "src.build.ConfigurationManager"
    ) as mock_config_cls, mock.patch(
        "src.device_clone.pcileech_generator.PCILeechGenerator"
    ) as mock_gen_cls, mock.patch(
        "src.device_clone.behavior_profiler.BehaviorProfiler"
    ) as mock_profiler_cls:

        mock_msix = mock.MagicMock()
        mock_file = mock.MagicMock()
        mock_config = mock.MagicMock()
        mock_gen = mock.MagicMock()
        mock_profiler = mock.MagicMock()

        mock_msix_cls.return_value = mock_msix
        mock_file_cls.return_value = mock_file
        mock_config_cls.return_value = mock_config
        mock_gen_cls.return_value = mock_gen
        mock_profiler_cls.return_value = mock_profiler

        builder = FirmwareBuilder(build_config, logger=mock_logger)

        # Return builder and mocks as a tuple
        yield builder, {
            "msix": mock_msix,
            "file": mock_file,
            "config": mock_config,
            "gen": mock_gen,
            "profiler": mock_profiler,
        }


def test_firmware_builder_build_success(mock_firmware_builder):
    """Test successful FirmwareBuilder.build() execution."""
    builder, mocks = mock_firmware_builder

    # Mock all the method calls
    builder._load_donor_template = mock.MagicMock(return_value=None)
    builder._preload_msix = mock.MagicMock(return_value=MSIXData(preloaded=False))
    builder._generate_firmware = mock.MagicMock(
        return_value={
            "systemverilog_modules": {},
            "template_context": {"device_config": {}},
            "config_space_data": {"device_info": {}},
        }
    )
    builder._inject_msix = mock.MagicMock()
    builder._write_modules = mock.MagicMock()
    builder._generate_profile = mock.MagicMock()
    builder._generate_tcl_scripts = mock.MagicMock()
    builder._save_device_info = mock.MagicMock()
    builder._store_device_config = mock.MagicMock()
    builder._generate_donor_template = mock.MagicMock()

    mocks["file"].list_artifacts.return_value = ["file1.sv", "file2.tcl"]

    # Execute build
    artifacts = builder.build()

    # Verify all steps were called
    builder._load_donor_template.assert_called_once()
    builder._preload_msix.assert_called_once()
    builder._generate_firmware.assert_called_once_with(None)
    builder._inject_msix.assert_called_once()
    builder._write_modules.assert_called_once()
    builder._generate_profile.assert_called_once()
    builder._generate_tcl_scripts.assert_called_once()
    builder._save_device_info.assert_called_once()
    builder._store_device_config.assert_called_once()
    builder._generate_donor_template.assert_not_called()  # output_template not set

    # Verify artifacts returned
    assert artifacts == ["file1.sv", "file2.tcl"]


def test_firmware_builder_build_with_donor_template(mock_firmware_builder):
    """Test FirmwareBuilder.build() with donor template."""
    builder, mocks = mock_firmware_builder
    builder.config.donor_template = "/path/to/template.json"

    donor_template = {"test": "data"}

    # Mock all the method calls
    builder._load_donor_template = mock.MagicMock(return_value=donor_template)
    builder._preload_msix = mock.MagicMock(return_value=MSIXData(preloaded=False))
    builder._generate_firmware = mock.MagicMock(
        return_value={
            "systemverilog_modules": {},
            "template_context": {"device_config": {}},
            "config_space_data": {"device_info": {}},
        }
    )
    builder._inject_msix = mock.MagicMock()
    builder._write_modules = mock.MagicMock()
    builder._generate_profile = mock.MagicMock()
    builder._generate_tcl_scripts = mock.MagicMock()
    builder._save_device_info = mock.MagicMock()
    builder._store_device_config = mock.MagicMock()
    builder._generate_donor_template = mock.MagicMock()

    mocks["file"].list_artifacts.return_value = ["file1.sv"]

    # Execute build
    builder.build()

    # Verify donor template was passed to firmware generation
    builder._generate_firmware.assert_called_once_with(donor_template)


def test_firmware_builder_build_with_output_template(mock_firmware_builder):
    """Test FirmwareBuilder.build() with output template generation."""
    builder, mocks = mock_firmware_builder
    builder.config.output_template = "donor_template.json"

    # Mock all the method calls
    builder._load_donor_template = mock.MagicMock(return_value=None)
    builder._preload_msix = mock.MagicMock(return_value=MSIXData(preloaded=False))
    builder._generate_firmware = mock.MagicMock(
        return_value={
            "systemverilog_modules": {},
            "template_context": {"device_config": {}},
            "config_space_data": {"device_info": {}},
        }
    )
    builder._inject_msix = mock.MagicMock()
    builder._write_modules = mock.MagicMock()
    builder._generate_profile = mock.MagicMock()
    builder._generate_tcl_scripts = mock.MagicMock()
    builder._save_device_info = mock.MagicMock()
    builder._store_device_config = mock.MagicMock()
    builder._generate_donor_template = mock.MagicMock()

    mocks["file"].list_artifacts.return_value = ["file1.sv"]

    # Execute build
    builder.build()

    # Verify donor template generation was called
    builder._generate_donor_template.assert_called_once()


def test_firmware_builder_build_exception_handling(mock_firmware_builder):
    """Test FirmwareBuilder.build() exception handling."""
    builder, mocks = mock_firmware_builder

    # Mock _load_donor_template to raise an exception
    builder._load_donor_template = mock.MagicMock(side_effect=Exception("Test error"))

    # Execute build and expect exception
    with pytest.raises(Exception, match="Test error"):
        builder.build()


def test_firmware_builder_load_donor_template_none(mock_firmware_builder):
    """Test FirmwareBuilder._load_donor_template() with no template configured."""
    builder, mocks = mock_firmware_builder
    builder.config.donor_template = None

    result = builder._load_donor_template()

    assert result is None


def test_firmware_builder_load_donor_template_success(mock_firmware_builder):
    """Test FirmwareBuilder._load_donor_template() successful load."""
    builder, mocks = mock_firmware_builder
    builder.config.donor_template = "/path/to/template.json"

    expected_template = {"test": "data"}

    with mock.patch(
        "src.device_clone.donor_info_template.DonorInfoTemplateGenerator"
    ) as mock_gen_cls:
        mock_gen = mock.MagicMock()
        mock_gen_cls.load_template.return_value = expected_template
        mock_gen_cls.return_value = mock_gen

        result = builder._load_donor_template()

        assert result == expected_template
        mock_gen_cls.load_template.assert_called_once_with("/path/to/template.json")


def test_firmware_builder_load_donor_template_failure(mock_firmware_builder):
    """Test FirmwareBuilder._load_donor_template() load failure."""
    builder, mocks = mock_firmware_builder
    builder.config.donor_template = "/path/to/template.json"

    with mock.patch(
        "src.device_clone.donor_info_template.DonorInfoTemplateGenerator"
    ) as mock_gen_cls:
        mock_gen_cls.load_template.side_effect = Exception("Load failed")

        with pytest.raises(PCILeechBuildError, match="Failed to load donor template"):
            builder._load_donor_template()


def test_firmware_builder_preload_msix_enabled(mock_firmware_builder):
    """Test FirmwareBuilder._preload_msix() with MSI-X preloading enabled."""
    builder, mocks = mock_firmware_builder
    builder.config.preload_msix = True

    expected_msix_data = MSIXData(preloaded=True)
    mocks["msix"].preload_data.return_value = expected_msix_data

    result = builder._preload_msix()

    assert result == expected_msix_data
    mocks["msix"].preload_data.assert_called_once()


def test_firmware_builder_preload_msix_disabled(mock_firmware_builder):
    """Test FirmwareBuilder._preload_msix() with MSI-X preloading disabled."""
    builder, mocks = mock_firmware_builder
    builder.config.preload_msix = False

    result = builder._preload_msix()

    assert result.preloaded == False
    mocks["msix"].preload_data.assert_not_called()


def test_firmware_builder_generate_firmware_basic(mock_firmware_builder):
    """Test FirmwareBuilder._generate_firmware() basic functionality."""
    builder, mocks = mock_firmware_builder

    expected_result = {
        "systemverilog_modules": {"module1": "content1"},
        "template_context": {
            "device_config": {"vendor_id": "0x1234"},
            "msix_config": {"is_supported": False, "num_vectors": 0},
            "msix_data": None,
        },
        "config_space_data": {"device_info": {}},
    }

    mocks["gen"].generate_pcileech_firmware.return_value = {
        "systemverilog_modules": {"module1": "content1"},
        "template_context": {"device_config": {"vendor_id": "0x1234"}},
        "config_space_data": {"device_info": {}},
    }

    result = builder._generate_firmware()

    # Verify MSI-X defaults were added
    assert "msix_config" in result["template_context"]
    assert "msix_data" in result["template_context"]
    assert result["template_context"]["msix_config"]["is_supported"] == False
    assert result["template_context"]["msix_config"]["num_vectors"] == 0
    assert result["template_context"]["msix_data"] is None


def test_firmware_builder_generate_firmware_with_donor_template(mock_firmware_builder):
    """Test FirmwareBuilder._generate_firmware() with donor template."""
    builder, mocks = mock_firmware_builder
    donor_template = {"test": "data"}

    mocks["gen"].generate_pcileech_firmware.return_value = {
        "systemverilog_modules": {},
        "template_context": {"device_config": {}},
        "config_space_data": {"device_info": {}},
    }

    result = builder._generate_firmware(donor_template)

    # Verify donor template was passed to generator config
    assert mocks["gen"].config.donor_template == donor_template


def test_firmware_builder_write_modules(mock_firmware_builder):
    """Test FirmwareBuilder._write_modules()."""
    builder, mocks = mock_firmware_builder

    modules = {"module1.sv": "content1", "module2.sv": "content2"}
    result = {"systemverilog_modules": modules}

    mocks["file"].write_systemverilog_modules.return_value = (
        ["module1.sv", "module2.sv"],
        [],
    )

    builder._write_modules(result)

    mocks["file"].write_systemverilog_modules.assert_called_once_with(modules)


def test_firmware_builder_generate_profile_enabled(mock_firmware_builder):
    """Test FirmwareBuilder._generate_profile() with profiling enabled."""
    builder, mocks = mock_firmware_builder
    builder.config.profile_duration = 30

    profile_data = {"profile": "data"}
    mocks["profiler"].capture_behavior_profile.return_value = profile_data

    builder._generate_profile()

    mocks["profiler"].capture_behavior_profile.assert_called_once_with(duration=30)
    mocks["file"].write_json.assert_called_once_with(
        "behavior_profile.json", profile_data
    )


def test_firmware_builder_generate_profile_disabled(mock_firmware_builder):
    """Test FirmwareBuilder._generate_profile() with profiling disabled."""
    builder, mocks = mock_firmware_builder
    builder.config.profile_duration = 0

    builder._generate_profile()

    mocks["profiler"].capture_behavior_profile.assert_not_called()
    mocks["file"].write_json.assert_not_called()


def test_firmware_builder_generate_tcl_scripts(mock_firmware_builder):
    """Test FirmwareBuilder._generate_tcl_scripts() - now copies static TCL."""
    builder, mocks = mock_firmware_builder

    result = {
        "template_context": {
            "device_config": {
                "device_id": "0x5678",
                "class_code": "0x020000",
                "revision_id": "0x01",
                "vendor_id": "0x1234",
                "subsystem_vendor_id": "0x1234",
                "subsystem_device_id": "0x5678",
            },
            "pcie_max_link_speed": 2,  # Gen2 - 5.0 GT/s
            "pcie_max_link_width": 4,  # x4 lanes
        }
    }

    # Mock file manager for TCL copying and source file operations
    with mock.patch("src.file_management.file_manager.FileManager") as mock_fm_cls:
        from pathlib import Path
        
        mock_fm = mock.MagicMock()
        mock_fm_cls.return_value = mock_fm
        mock_fm.create_pcileech_structure.return_value = None
        mock_fm.copy_pcileech_sources.return_value = {}
        # Mock TCL script copying
        mock_fm.copy_vivado_tcl_scripts.return_value = [
            Path("vivado_generate_project.tcl"),
            Path("vivado_build.tcl"),
        ]

        builder._generate_tcl_scripts(result)

        # Verify TCL scripts were copied from submodule
        mock_fm.copy_vivado_tcl_scripts.assert_called_once_with(builder.config.board)


def test_firmware_builder_save_device_info(mock_firmware_builder):
    """Test FirmwareBuilder._save_device_info()."""
    builder, mocks = mock_firmware_builder

    device_info = {"vendor_id": "0x1234", "device_id": "0x5678"}
    result = {"config_space_data": {"device_info": device_info}}

    builder._save_device_info(result)

    mocks["file"].write_json.assert_called_once_with("device_info.json", device_info)


def test_firmware_builder_store_device_config(mock_firmware_builder):
    """Test FirmwareBuilder._store_device_config()."""
    builder, mocks = mock_firmware_builder

    ctx = {"device_config": {"vendor_id": "0x1234"}}
    result = {"template_context": ctx, "msix_data": {"enabled": True}}

    builder._store_device_config(result)

    mocks["config"].extract_device_config.assert_called_once_with(ctx, True)
    assert builder._device_config is not None


def test_firmware_builder_generate_donor_template(mock_firmware_builder):
    """Test FirmwareBuilder._generate_donor_template()."""
    builder, mocks = mock_firmware_builder
    builder.config.output_template = "output_template.json"

    result = {
        "config_space_data": {"device_info": {}},
        "template_context": {"device_config": {}},
    }

    with mock.patch(
        "src.device_clone.donor_info_template.DonorInfoTemplateGenerator"
    ) as mock_gen_cls:
        mock_gen = mock.MagicMock()
        mock_template = {"device_info": {"identification": {}}, "metadata": {}}
        mock_gen.generate_blank_template.return_value = mock_template
        mock_gen_cls.return_value = mock_gen

        builder._generate_donor_template(result)

        mock_gen.generate_blank_template.assert_called_once()
        mock_gen.save_template_dict.assert_called_once()
        # Check that BDF was added to metadata
        assert mock_template["metadata"]["device_bdf"] == builder.config.bdf


def test_firmware_builder_run_vivado_user_path(mock_firmware_builder):
    """Test FirmwareBuilder.run_vivado() with user-specified Vivado path."""
    builder, mocks = mock_firmware_builder
    builder.config.vivado_path = "/custom/vivado/path"

    with mock.patch(
        "src.vivado_handling.VivadoRunner") as mock_runner_cls, mock.patch(
        "src.vivado_handling.find_vivado_installation", return_value=None
    ), mock.patch("pathlib.Path.exists") as mock_path_exists:
        # Mock Path.exists to return True for vivado executable check
        # The check is: Path(vivado_path) / "bin" / "vivado" -> exists()
        mock_path_exists.return_value = True

        mock_runner = mock.MagicMock()
        mock_runner_cls.return_value = mock_runner

        builder.run_vivado()

        mock_runner_cls.assert_called_once_with(
            board=builder.config.board,
            output_dir=builder.config.output_dir,
            vivado_path="/custom/vivado/path",
            logger=builder.logger,
            device_config=None,
        )
        mock_runner.run.assert_called_once()


def test_firmware_builder_run_vivado_auto_detect(mock_firmware_builder):
    """Test FirmwareBuilder.run_vivado() with auto-detected Vivado path."""
    builder, mocks = mock_firmware_builder
    builder.config.vivado_path = None

    vivado_info = {"executable": "/tools/xilinx/vivado/bin/vivado"}

    with mock.patch("src.vivado_handling.VivadoRunner") as mock_runner_cls, mock.patch(
        "src.vivado_handling.find_vivado_installation", return_value=vivado_info
    ):

        mock_runner = mock.MagicMock()
        mock_runner_cls.return_value = mock_runner

        builder.run_vivado()

        expected_path = "/tools/xilinx/vivado"
        mock_runner_cls.assert_called_once_with(
            board=builder.config.board,
            output_dir=builder.config.output_dir,
            vivado_path=expected_path,
            logger=builder.logger,
            device_config=None,
        )
        mock_runner.run.assert_called_once()


def test_firmware_builder_run_vivado_not_found(mock_firmware_builder):
    """Test FirmwareBuilder.run_vivado() when Vivado is not found."""
    builder, mocks = mock_firmware_builder
    builder.config.vivado_path = None

    with mock.patch("src.vivado_handling.find_vivado_installation", return_value=None):
        with pytest.raises(VivadoIntegrationError, match="Vivado not found"):
            builder.run_vivado()


def test_firmware_builder_run_vivado_import_error(mock_firmware_builder):
    """Test FirmwareBuilder.run_vivado() when VivadoRunner import fails."""
    builder, mocks = mock_firmware_builder

    with mock.patch.dict("sys.modules", {"src.vivado_handling": None}):
        with pytest.raises(
            VivadoIntegrationError, match="Vivado handling modules not available"
        ):
            builder.run_vivado()
