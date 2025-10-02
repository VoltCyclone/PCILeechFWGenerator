"""
Comprehensive unit tests for src/file_management/donor_dump_manager.py

This test module provides complete coverage for all classes, functions,
and error scenarios in the DonorDumpManager.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import types
import typing
from pathlib import Path
from unittest import mock

import pytest

# Add project root to Python path for direct test execution
project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.file_management.donor_dump_manager import (DonorDumpError,
                                                    DonorDumpManager,
                                                    DonorDumpModuleError,
                                                    DonorDumpPermissionError,
                                                    DonorDumpTimeoutError,
                                                    KernelHeadersNotFoundError,
                                                    ModuleBuildError,
                                                    ModuleLoadError)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return mock.MagicMock(spec=logging.Logger)


@pytest.fixture
def donor_dump_manager(temp_dir, mock_logger):
    """Create a DonorDumpManager instance with mocked logger."""
    with mock.patch("src.file_management.donor_dump_manager.logger", mock_logger):
        manager = DonorDumpManager(module_source_dir=temp_dir)
        return manager


@pytest.fixture(autouse=True)
def stub_device_clone_constants():
    """Provide a lightweight constants module to avoid heavy dependencies."""
    module_name = "src.device_clone.constants"
    stub_module = types.ModuleType(module_name)
    setattr(stub_module, "VENDOR_ID_INTEL", 0x8086)
    setattr(stub_module, "VENDOR_ID_REALTEK", 0x10EC)
    setattr(stub_module, "VENDOR_ID_NVIDIA", 0x10DE)
    setattr(stub_module, "VENDOR_ID_AMD", 0x1002)
    setattr(stub_module, "DEVICE_ID_INTEL_ETH", 0x1533)
    setattr(stub_module, "DEVICE_ID_INTEL_NVME", 0x2522)
    setattr(stub_module, "get_fallback_vendor_id", lambda prefer_random=False: 0x8086)

    with mock.patch.dict(sys.modules, {module_name: stub_module}):
        yield


@pytest.fixture
def mock_subprocess():
    """Mock subprocess module for testing."""
    with mock.patch(
        "src.file_management.donor_dump_manager.subprocess", autospec=True
    ) as mock_subp:
        mock_subp.CalledProcessError = subprocess.CalledProcessError
        mock_subp.CompletedProcess = subprocess.CompletedProcess
        mock_subp.TimeoutExpired = subprocess.TimeoutExpired
        yield mock_subp


@pytest.fixture
def mock_os_path():
    """Mock os.path for testing."""
    with mock.patch("src.file_management.donor_dump_manager.os.path") as mock_path:
        yield mock_path


# ============================================================================
# Test Exception Classes
# ============================================================================


def test_donor_dump_error():
    """Test DonorDumpError exception."""
    error = DonorDumpError("Test error")
    assert str(error) == "Test error"
    assert isinstance(error, Exception)
    assert error.context == {}

    # Test with context
    context = {"key": "value"}
    error = DonorDumpError("Test error", context)
    assert error.context == context


def test_kernel_headers_not_found_error():
    """Test KernelHeadersNotFoundError exception."""
    error = KernelHeadersNotFoundError("Headers not found")
    assert "Headers not found" in str(error)
    assert isinstance(error, DonorDumpError)

    # Test with install command
    error = KernelHeadersNotFoundError(
        "Headers not found",
        kernel_version="5.4.0",
        install_command="apt-get install linux-headers-5.4.0",
    )
    assert error.kernel_version == "5.4.0"
    assert error.install_command == "apt-get install linux-headers-5.4.0"


def test_module_build_error():
    """Test ModuleBuildError exception."""
    error = ModuleBuildError("Build failed")
    assert "Build failed" in str(error)
    assert isinstance(error, DonorDumpError)

    # Test with context
    error = ModuleBuildError(
        "Build failed", build_command="make", stderr_output="error output", exit_code=1
    )
    assert error.build_command == "make"
    assert error.stderr_output == "error output"
    assert error.exit_code == 1


def test_module_load_error():
    """Test ModuleLoadError exception."""
    error = ModuleLoadError("Load failed")
    assert "Load failed" in str(error)
    assert isinstance(error, DonorDumpError)

    # Test with context
    error = ModuleLoadError(
        "Load failed",
        module_path="/path/to/module.ko",
        bdf="0000:03:00.0",
        stderr_output="error output",
    )
    assert error.module_path == "/path/to/module.ko"
    assert error.bdf == "0000:03:00.0"
    assert error.stderr_output == "error output"


def test_donor_dump_timeout_error():
    """Test DonorDumpTimeoutError exception."""
    error = DonorDumpTimeoutError("Operation timed out")
    assert "Operation timed out" in str(error)
    assert isinstance(error, DonorDumpError)

    # Test with timeout and operation
    error = DonorDumpTimeoutError(
        "Operation timed out", timeout_seconds=30.0, operation="module_load"
    )
    assert error.timeout_seconds == 30.0
    assert error.operation == "module_load"
    assert "30.0s" in str(error)
    assert "module_load" in str(error)


def test_donor_dump_permission_error():
    """Test DonorDumpPermissionError exception."""
    error = DonorDumpPermissionError("Permission denied")
    assert "Permission denied" in str(error)
    assert isinstance(error, DonorDumpError)

    # Test with required permission and file path
    error = DonorDumpPermissionError(
        "Permission denied", required_permission="root", file_path="/dev/device"
    )
    assert error.required_permission == "root"
    assert error.file_path == "/dev/device"
    assert "root" in str(error)
    assert "/dev/device" in str(error)


def test_donor_dump_module_error():
    """Test DonorDumpModuleError exception."""
    error = DonorDumpModuleError("Module error")
    assert "Module error" in str(error)
    assert isinstance(error, DonorDumpError)

    # Test with context
    error = DonorDumpModuleError(
        "Module error",
        module_name="donor_dump",
        error_code=1,
        stderr_output="error output",
    )
    assert error.module_name == "donor_dump"
    assert error.error_code == 1
    assert error.stderr_output == "error output"
    assert "donor_dump" in str(error)
    assert "exit_code: 1" in str(error)


# ============================================================================
# Test DonorDumpManager Class
# ============================================================================


def test_donor_dump_manager_init(temp_dir):
    """Test DonorDumpManager initialization."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    assert manager.module_source_dir == temp_dir
    assert manager.module_name == "donor_dump"
    assert manager.proc_path == "/proc/donor_dump"
    assert manager.donor_info_path is None


def test_donor_dump_manager_init_with_donor_info_path(temp_dir):
    """Test DonorDumpManager initialization with donor info path."""
    donor_path = "/path/to/donor.json"
    manager = DonorDumpManager(module_source_dir=temp_dir, donor_info_path=donor_path)

    assert manager.donor_info_path == donor_path


def test_donor_dump_manager_init_default_source_dir():
    """Test DonorDumpManager initialization with default source directory."""
    manager = DonorDumpManager()

    # Should be relative to the file location
    module = sys.modules[DonorDumpManager.__module__]
    module_file = typing.cast(str, getattr(module, "__file__", ""))
    expected_path = Path(module_file).resolve().parent / "donor_dump"
    assert manager.module_source_dir == expected_path


def test_check_kernel_headers_success(mock_subprocess, mock_os_path):
    """Test check_kernel_headers with successful execution."""
    mock_subprocess.check_output.return_value = "5.4.0-42-generic\n"
    mock_os_path.exists.return_value = True

    manager = DonorDumpManager()
    available, version = manager.check_kernel_headers()

    assert available is True
    assert version == "5.4.0-42-generic"
    mock_subprocess.check_output.assert_called_once_with(["uname", "-r"], text=True)
    mock_os_path.exists.assert_called_once_with("/lib/modules/5.4.0-42-generic/build")


def test_check_kernel_headers_failure(mock_subprocess, mock_os_path):
    """Test check_kernel_headers with subprocess failure."""
    mock_subprocess.check_output.side_effect = subprocess.CalledProcessError(
        returncode=1, cmd=["uname", "-r"]
    )
    mock_os_path.exists.return_value = False

    manager = DonorDumpManager()
    available, version = manager.check_kernel_headers()

    assert available is False
    assert version == ""


def test_check_kernel_headers_no_headers(mock_subprocess, mock_os_path):
    """Test check_kernel_headers when headers directory doesn't exist."""
    mock_subprocess.check_output.return_value = "5.4.0\n"
    mock_os_path.exists.return_value = False

    manager = DonorDumpManager()
    available, version = manager.check_kernel_headers()

    assert available is False
    assert version == "5.4.0"


def test_detect_linux_distribution_debian(mock_os_path):
    """Test _detect_linux_distribution for Debian."""
    manager = DonorDumpManager()

    with mock.patch("builtins.open", mock.mock_open(read_data="ID=debian\n")):
        with mock.patch(
            "src.file_management.donor_dump_manager.os.path.exists"
        ) as mock_exists:
            mock_exists.return_value = True
            distro = manager._detect_linux_distribution()
            assert distro == "debian"


def test_detect_linux_distribution_ubuntu(mock_os_path):
    """Test _detect_linux_distribution for Ubuntu."""
    manager = DonorDumpManager()

    with mock.patch("builtins.open", mock.mock_open(read_data="ID=ubuntu\n")):
        with mock.patch(
            "src.file_management.donor_dump_manager.os.path.exists"
        ) as mock_exists:
            mock_exists.return_value = True
            distro = manager._detect_linux_distribution()
            assert distro == "ubuntu"


def test_detect_linux_distribution_fallback(mock_os_path):
    """Test _detect_linux_distribution fallback methods."""
    manager = DonorDumpManager()

    # Test debian version file fallback
    with mock.patch(
        "src.file_management.donor_dump_manager.os.path.exists"
    ) as mock_exists:
        mock_exists.side_effect = lambda path: path == "/etc/debian_version"
        distro = manager._detect_linux_distribution()
        assert distro == "debian"


def test_detect_linux_distribution_lsb_release(mock_os_path):
    """Test _detect_linux_distribution with lsb_release command."""
    manager = DonorDumpManager()

    with mock.patch(
        "src.file_management.donor_dump_manager.os.path.exists", return_value=False
    ):
        with mock.patch(
            "src.file_management.donor_dump_manager.subprocess.run"
        ) as mock_run:
            mock_run.return_value = mock.MagicMock(stdout="Distributor ID:\tUbuntu\n")
            distro = manager._detect_linux_distribution()
            assert distro == "ubuntu"


def test_detect_linux_distribution_unknown():
    """Test _detect_linux_distribution when unable to detect."""
    manager = DonorDumpManager()

    with mock.patch(
        "src.file_management.donor_dump_manager.os.path.exists", return_value=False
    ):
        with mock.patch(
            "src.file_management.donor_dump_manager.subprocess.run"
        ) as mock_run:
            mock_run.side_effect = Exception("Command failed")
            distro = manager._detect_linux_distribution()
            assert distro == "unknown"


def test_get_header_install_command_debian():
    """Test _get_header_install_command for Debian."""
    manager = DonorDumpManager()
    cmd = manager._get_header_install_command("debian", "5.4.0")
    assert cmd == "apt-get install linux-headers-5.4.0"


def test_get_header_install_command_ubuntu():
    """Test _get_header_install_command for Ubuntu."""
    manager = DonorDumpManager()
    cmd = manager._get_header_install_command("ubuntu", "5.4.0")
    assert cmd == "apt-get install linux-headers-5.4.0"


def test_get_header_install_command_fedora():
    """Test _get_header_install_command for Fedora."""
    manager = DonorDumpManager()
    cmd = manager._get_header_install_command("fedora", "5.4.0")
    assert cmd == "dnf install kernel-devel-5.4.0"


def test_get_header_install_command_arch():
    """Test _get_header_install_command for Arch Linux."""
    manager = DonorDumpManager()
    cmd = manager._get_header_install_command("arch", "5.4.0")
    assert cmd == "pacman -S linux-headers"


def test_get_header_install_command_unknown():
    """Test _get_header_install_command for unknown distribution."""
    manager = DonorDumpManager()
    cmd = manager._get_header_install_command("unknown", "5.4.0")
    assert "Please install kernel headers" in cmd


def test_install_kernel_headers_debian_success(mock_subprocess):
    """Test install_kernel_headers for Debian with success."""
    manager = DonorDumpManager()

    # Mock successful installation
    mock_subprocess.run.return_value = mock.MagicMock(returncode=0)

    with mock.patch.object(
        manager, "_detect_linux_distribution", return_value="debian"
    ):
        with mock.patch.object(
            manager, "check_kernel_headers", return_value=(True, "5.4.0")
        ):
            result = manager.install_kernel_headers("5.4.0")
            assert result is True


def test_install_kernel_headers_unsupported_distro():
    """Test install_kernel_headers for unsupported distribution."""
    manager = DonorDumpManager()

    with mock.patch.object(
        manager, "_detect_linux_distribution", return_value="unsupported"
    ):
        result = manager.install_kernel_headers("5.4.0")
        assert result is False


def test_install_kernel_headers_command_failure(mock_subprocess):
    """Test install_kernel_headers when command fails."""
    manager = DonorDumpManager()

    mock_subprocess.run.side_effect = Exception("Command failed")

    with mock.patch.object(
        manager, "_detect_linux_distribution", return_value="debian"
    ):
        result = manager.install_kernel_headers("5.4.0")
        assert result is False


def test_build_module_source_dir_not_found(temp_dir):
    """Test build_module when source directory doesn't exist."""
    manager = DonorDumpManager(module_source_dir=temp_dir / "nonexistent")

    with pytest.raises(ModuleBuildError, match="Module source directory not found"):
        manager.build_module()


def test_build_module_kernel_headers_not_found(temp_dir, mock_subprocess):
    """Test build_module when kernel headers are not available."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory
    (temp_dir / "Makefile").touch()

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(False, "5.4.0")
    ):
        with pytest.raises(KernelHeadersNotFoundError):
            manager.build_module()


def test_build_module_already_built(temp_dir):
    """Test build_module when module is already built and not forcing rebuild."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory and module file
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()
    (temp_dir / "donor_dump.ko").touch()

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(True, "5.4.0")
    ):
        result = manager.build_module(force_rebuild=False)
        assert result is True


def test_build_module_success(temp_dir, mock_subprocess):
    """Test build_module successful build."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()

    # Mock successful make command
    mock_subprocess.run.return_value = mock.MagicMock(returncode=0)

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(True, "5.4.0")
    ):
        result = manager.build_module()
        assert result is True

        # Verify make was called
        mock_subprocess.run.assert_called_with(
            ["make"], cwd=temp_dir, check=True, capture_output=True, text=True
        )


def test_build_module_with_kernelrelease_fallback(temp_dir, mock_subprocess):
    """Test build_module with KERNELRELEASE fallback."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()

    # Mock make failure then success with KERNELRELEASE
    mock_subprocess.run.side_effect = [
        subprocess.CalledProcessError(returncode=2, cmd=["make"]),
        mock.MagicMock(returncode=0),
    ]

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(True, "5.4.0")
    ):
        result = manager.build_module()
        assert result is True

        # Verify both make commands were tried
        assert mock_subprocess.run.call_count == 2


def test_build_module_force_rebuild(temp_dir, mock_subprocess):
    """Test build_module with force rebuild."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory and existing module
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()
    (temp_dir / "donor_dump.ko").touch()

    mock_subprocess.run.return_value = mock.MagicMock(returncode=0)

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(True, "5.4.0")
    ):
        result = manager.build_module(force_rebuild=True)
        assert result is True

        # Verify make clean was called first
        clean_call = mock.call(
            ["make", "clean"], cwd=temp_dir, check=True, capture_output=True, text=True
        )
        assert clean_call in mock_subprocess.run.call_args_list


def test_build_module_make_failure(temp_dir, mock_subprocess):
    """Test build_module when make command fails."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()

    mock_subprocess.run.side_effect = subprocess.CalledProcessError(
        returncode=2, cmd=["make"]
    )

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(True, "5.4.0")
    ):
        with pytest.raises(ModuleBuildError):
            manager.build_module()


def test_is_module_loaded_success(mock_subprocess):
    """Test is_module_loaded when module is loaded."""
    manager = DonorDumpManager()

    mock_subprocess.run.return_value = mock.MagicMock(
        returncode=0, stdout="donor_dump 12345 0 - Live 0x0000000000000000\n"
    )

    result = manager.is_module_loaded()
    assert result is True


def test_is_module_loaded_not_loaded(mock_subprocess):
    """Test is_module_loaded when module is not loaded."""
    manager = DonorDumpManager()

    mock_subprocess.run.return_value = mock.MagicMock(
        returncode=0, stdout="other_module 12345 0 - Live 0x0000000000000000\n"
    )

    result = manager.is_module_loaded()
    assert result is False


def test_is_module_loaded_lsmod_not_found(mock_subprocess):
    """Test is_module_loaded when lsmod command is not found."""
    manager = DonorDumpManager()

    mock_subprocess.run.side_effect = FileNotFoundError("lsmod not found")

    result = manager.is_module_loaded()
    assert result is False


def test_is_module_loaded_lsmod_failure(mock_subprocess):
    """Test is_module_loaded when lsmod command fails."""
    manager = DonorDumpManager()

    mock_subprocess.run.side_effect = subprocess.CalledProcessError(
        returncode=1, cmd=["lsmod"]
    )

    result = manager.is_module_loaded()
    assert result is False


def test_load_module_invalid_bdf():
    """Test load_module with invalid BDF format."""
    manager = DonorDumpManager()

    with pytest.raises(ModuleLoadError, match="Invalid BDF format"):
        manager.load_module("invalid_bdf")


def test_load_module_already_loaded():
    """Test load_module when module is already loaded."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "is_module_loaded", return_value=True):
        with mock.patch.object(manager, "build_module") as mock_build:
            result = manager.load_module("0000:03:00.0")
            assert result is True
            mock_build.assert_not_called()


def test_load_module_force_reload():
    """Test load_module with force reload."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "is_module_loaded", return_value=True):
        with mock.patch.object(manager, "unload_module") as mock_unload:
            with mock.patch.object(manager, "build_module") as mock_build:
                with mock.patch(
                    "src.file_management.donor_dump_manager.os.path.exists",
                    return_value=True,
                ):
                    with mock.patch(
                        "src.file_management.donor_dump_manager.subprocess.run"
                    ) as mock_run:
                        mock_run.return_value = mock.MagicMock(returncode=0)

                        result = manager.load_module("0000:03:00.0", force_reload=True)
                        assert result is True
                        mock_unload.assert_called_once()


def test_load_module_build_required(temp_dir, mock_subprocess):
    """Test load_module when module needs to be built."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()

    with mock.patch.object(
        manager, "is_module_loaded", side_effect=[False, True]
    ) as mock_is_loaded:
        with mock.patch.object(manager, "build_module", return_value=True):
            with mock.patch(
                "src.file_management.donor_dump_manager.os.path.exists",
                return_value=True,
            ):
                mock_subprocess.run.return_value = mock.MagicMock(returncode=0)

                result = manager.load_module("0000:03:00.0")
                assert result is True
                assert mock_is_loaded.call_count >= 2


def test_load_module_insmod_failure(temp_dir, mock_subprocess):
    """Test load_module when insmod fails."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory and module file
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()
    (temp_dir / "donor_dump.ko").touch()

    with mock.patch.object(manager, "is_module_loaded", return_value=False):
        with mock.patch.object(manager, "build_module", return_value=True):
            with mock.patch(
                "src.file_management.donor_dump_manager.os.path.exists",
                return_value=True,
            ):
                mock_subprocess.run.side_effect = subprocess.CalledProcessError(
                    returncode=1,
                    cmd=["insmod", str(temp_dir / "donor_dump.ko"), "bdf=0000:03:00.0"],
                )

                with pytest.raises(ModuleLoadError):
                    manager.load_module("0000:03:00.0")


def test_load_module_proc_file_missing(temp_dir, mock_subprocess):
    """Test load_module when /proc file is not created."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory and module file
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()
    (temp_dir / "donor_dump.ko").touch()

    with mock.patch.object(
        manager, "is_module_loaded", side_effect=[False, True]
    ) as mock_is_loaded:
        with mock.patch.object(manager, "build_module", return_value=True):
            with mock.patch(
                "src.file_management.donor_dump_manager.os.path.exists"
            ) as mock_exists:
                mock_exists.side_effect = lambda path: path != "/proc/donor_dump"
                mock_subprocess.run.return_value = mock.MagicMock(returncode=0)

                with pytest.raises(
                    ModuleLoadError, match="/proc/donor_dump not created"
                ):
                    manager.load_module("0000:03:00.0")
                assert mock_is_loaded.call_count >= 2


def test_unload_module_not_loaded():
    """Test unload_module when module is not loaded."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "is_module_loaded", return_value=False):
        result = manager.unload_module()
        assert result is True


def test_unload_module_success(mock_subprocess):
    """Test unload_module successful unload."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "is_module_loaded", return_value=True):
        mock_subprocess.run.return_value = mock.MagicMock(returncode=0)

        result = manager.unload_module()
        assert result is True

        mock_subprocess.run.assert_called_with(
            ["rmmod", "donor_dump"], check=True, capture_output=True, text=True
        )


def test_unload_module_rmmod_failure(mock_subprocess):
    """Test unload_module when rmmod fails."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "is_module_loaded", return_value=True):
        mock_subprocess.run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["rmmod", "donor_dump"]
        )

        with pytest.raises(ModuleLoadError):
            manager.unload_module()


def test_read_device_info_success(temp_dir):
    """Test read_device_info successful read."""
    manager = DonorDumpManager()

    proc_content = "vendor_id: 0x1234\ndevice_id: 0x5678\nbar_size: 0x1000\n"

    with mock.patch(
        "src.file_management.donor_dump_manager.os.path.exists", return_value=True
    ):
        with mock.patch("builtins.open", mock.mock_open(read_data=proc_content)):
            result = manager.read_device_info()

            expected = {
                "vendor_id": "0x1234",
                "device_id": "0x5678",
                "bar_size": "0x1000",
            }
            assert result == expected


def test_read_device_info_proc_file_missing():
    """Test read_device_info when /proc file doesn't exist."""
    manager = DonorDumpManager()

    with mock.patch(
        "src.file_management.donor_dump_manager.os.path.exists", return_value=False
    ):
        with pytest.raises(DonorDumpError, match="/proc/donor_dump not available"):
            manager.read_device_info()


def test_read_device_info_io_error():
    """Test read_device_info when file read fails."""
    manager = DonorDumpManager()

    with mock.patch(
        "src.file_management.donor_dump_manager.os.path.exists", return_value=True
    ):
        with mock.patch("builtins.open", side_effect=IOError("Read failed")):
            with pytest.raises(DonorDumpError, match="Failed to read device info"):
                manager.read_device_info()


def test_get_module_status(temp_dir):
    """Test get_module_status."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory and module file
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()
    module_file = temp_dir / "donor_dump.ko"
    module_file.touch()

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(True, "5.4.0")
    ):
        with mock.patch.object(manager, "is_module_loaded", return_value=True):
            with mock.patch(
                "src.file_management.donor_dump_manager.os.path.exists",
                return_value=True,
            ):
                status = manager.get_module_status()

                assert status["kernel_version"] == "5.4.0"
                assert status["headers_available"] is True
                assert status["module_built"] is True
                assert status["module_loaded"] is True
                assert status["proc_available"] is True
                assert status["source_dir_exists"] is True
                assert "module_path" in status
                assert "module_size" in status


def test_generate_donor_info_generic():
    """Test generate_donor_info for generic device."""
    manager = DonorDumpManager()

    with mock.patch(
        "src.file_management.donor_dump_manager.random.random", return_value=0.3
    ):
        info = manager.generate_donor_info("generic")

        assert "vendor_id" in info
        assert "device_id" in info
        assert "revision_id" in info
    assert info["revision_id"] == "0x03"


def test_generate_donor_info_network():
    """Test generate_donor_info for network device."""
    manager = DonorDumpManager()

    info = manager.generate_donor_info("network")

    assert info["device_id"] == "0x1533"  # I210 device
    assert "bar_size" in info


def test_generate_donor_info_storage():
    """Test generate_donor_info for storage device."""
    manager = DonorDumpManager()

    info = manager.generate_donor_info("storage")

    assert info["device_id"] == "0x2522"  # NVMe device
    assert "bar_size" in info


def test_save_donor_info_success(temp_dir):
    """Test save_donor_info successful save."""
    manager = DonorDumpManager()

    device_info = {
        "vendor_id": "0x1234",
        "device_id": "0x5678",
        "extended_config": "deadbeef" * 1024,  # 4KB hex string
    }

    output_path = temp_dir / "donor_info.json"
    config_hex_path = temp_dir / "config_space_init.hex"

    result = manager.save_donor_info(device_info, str(output_path))

    assert result is True
    assert output_path.exists()

    # Check JSON content
    with open(output_path, "r") as f:
        saved_data = json.load(f)
        assert saved_data == device_info

    # Check hex file was created
    assert config_hex_path.exists()


def test_save_donor_info_io_error(temp_dir):
    """Test save_donor_info when file write fails."""
    manager = DonorDumpManager()

    device_info = {"vendor_id": "0x1234"}

    with mock.patch("builtins.open", side_effect=IOError("Write failed")):
        result = manager.save_donor_info(device_info, str(temp_dir / "donor_info.json"))
        assert result is False


def test_save_config_space_hex_success(temp_dir):
    """Test save_config_space_hex successful save."""
    manager = DonorDumpManager()

    config_hex = "deadbeef" * 2048  # 4KB hex string
    output_path = temp_dir / "config.hex"

    result = manager.save_config_space_hex(config_hex, str(output_path))

    assert result is True
    assert output_path.exists()

    # Check file content (should be little-endian 32-bit words)
    with open(output_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 1024  # 1024 lines for 4KB
        # First line should be little-endian version of first 8 chars
        assert lines[0].strip() == "efbeadde"  # deadbeef -> efbeadde


def test_save_config_space_hex_padding(temp_dir):
    """Test save_config_space_hex with padding for short input."""
    manager = DonorDumpManager()

    config_hex = "deadbeef"  # Only 8 chars, needs padding to 8192
    output_path = temp_dir / "config.hex"

    result = manager.save_config_space_hex(config_hex, str(output_path))

    assert result is True
    assert output_path.exists()

    # Check file has correct number of lines
    with open(output_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 1024


def test_save_config_space_hex_truncation(temp_dir):
    """Test save_config_space_hex with truncation for long input."""
    manager = DonorDumpManager()

    config_hex = "deadbeef" * 3000  # Much longer than 4KB
    output_path = temp_dir / "config.hex"

    result = manager.save_config_space_hex(config_hex, str(output_path))

    assert result is True
    assert output_path.exists()

    # Check file has correct number of lines (truncated to 4KB)
    with open(output_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 1024


def test_generate_blank_config_hex_success(temp_dir):
    """Test generate_blank_config_hex successful creation."""
    manager = DonorDumpManager()

    output_path = temp_dir / "blank.hex"

    result = manager.generate_blank_config_hex(str(output_path))

    assert result is True
    assert output_path.exists()

    # Check file has 1024 lines of zeros
    with open(output_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 1024
        assert all(line.strip() == "00000000" for line in lines)


def test_check_module_installation_installed():
    """Test check_module_installation when module is fully installed."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "get_module_status") as mock_status:
        mock_status.return_value = {
            "module_loaded": True,
            "proc_available": True,
        }

        result = manager.check_module_installation()

        assert result["status"] == "installed"
        assert "installed and loaded" in result["details"]


def test_check_module_installation_built_not_loaded():
    """Test check_module_installation when module is built but not loaded."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "get_module_status") as mock_status:
        mock_status.return_value = {
            "module_built": True,
            "module_loaded": False,
            "kernel_version": "5.4.0",
            "headers_available": True,
            "proc_available": False,
            "module_path": "/tmp/donor_dump.ko",
            "source_dir_exists": True,
        }

        result = manager.check_module_installation()

        assert result["status"] == "built_not_loaded"
        assert "not currently loaded" in result["details"]
        assert len(result["issues"]) > 0
        assert len(result["fixes"]) > 0


def test_check_module_installation_not_built():
    """Test check_module_installation when module is not built."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "get_module_status") as mock_status:
        mock_status.return_value = {
            "source_dir_exists": True,
            "module_built": False,
            "headers_available": False,
            "kernel_version": "5.4.0",
            "module_loaded": False,
            "proc_available": False,
        }

        result = manager.check_module_installation()

        assert result["status"] == "not_built"
        assert "not been built" in result["details"]
        assert any("kernel headers" in issue.lower() for issue in result["issues"])


def test_check_module_installation_missing_source():
    """Test check_module_installation when source directory is missing."""
    manager = DonorDumpManager()

    with mock.patch.object(manager, "get_module_status") as mock_status:
        mock_status.return_value = {
            "source_dir_exists": False,
            "module_loaded": False,
            "proc_available": False,
            "module_built": False,
            "headers_available": False,
            "kernel_version": "",
        }

        result = manager.check_module_installation()

        assert result["status"] == "missing_source"
        assert "not found" in result["details"]


def test_setup_module_success(temp_dir, mock_subprocess):
    """Test setup_module successful execution."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()

    device_info = {"vendor_id": "0x1234", "device_id": "0x5678"}

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(True, "5.4.0")
    ):
        with mock.patch.object(manager, "build_module", return_value=True):
            with mock.patch.object(manager, "load_module", return_value=True):
                with mock.patch.object(
                    manager, "read_device_info", return_value=device_info
                ):
                    with mock.patch("builtins.open", mock.mock_open()) as mock_open:
                        result = manager.setup_module("0000:03:00.0")

                        assert result == device_info
                        mock_open.assert_called()


def test_setup_module_kernel_headers_missing():
    """Test setup_module when kernel headers are missing."""
    manager = DonorDumpManager()

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(False, "5.4.0")
    ):
        with pytest.raises(KernelHeadersNotFoundError):
            manager.setup_module("0000:03:00.0")


def test_setup_module_auto_install_headers(temp_dir, mock_subprocess):
    """Test setup_module with auto header installation."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    # Create source directory
    temp_dir.mkdir(exist_ok=True)
    (temp_dir / "Makefile").touch()

    device_info = {"vendor_id": "0x1234"}

    with mock.patch.object(
        manager, "check_kernel_headers", return_value=(False, "5.4.0")
    ):
        with mock.patch.object(manager, "install_kernel_headers", return_value=True):
            with mock.patch.object(manager, "build_module", return_value=True):
                with mock.patch.object(manager, "load_module", return_value=True):
                    with mock.patch.object(
                        manager, "read_device_info", return_value=device_info
                    ):
                        with mock.patch.object(manager, "save_donor_info"):
                            result = manager.setup_module(
                                "0000:03:00.0", auto_install_headers=True
                            )
                            assert result == device_info


def test_setup_module_generate_fallback(temp_dir):
    """Test setup_module with generate fallback."""
    manager = DonorDumpManager(module_source_dir=temp_dir)

    with mock.patch.object(
        manager, "check_kernel_headers", side_effect=Exception("Headers check failed")
    ):
        with mock.patch.object(manager, "generate_donor_info") as mock_generate:
            mock_generate.return_value = {
                "vendor_id": "0x1234",
                "device_id": "0x5678",
                "subvendor_id": "0x1234",
                "subsystem_id": "0x0000",
                "revision_id": "0x01",
            }

            result = manager.setup_module("0000:03:00.0", generate_if_unavailable=True)
            assert result["vendor_id"] == "0x1234"
            mock_generate.assert_called_once_with("generic")


def test_setup_module_failure_no_fallback():
    """Test setup_module failure without fallback."""
    manager = DonorDumpManager()

    with mock.patch.object(
        manager, "check_kernel_headers", side_effect=Exception("Headers check failed")
    ):
        with pytest.raises(Exception, match="Headers check failed"):
            manager.setup_module("0000:03:00.0", generate_if_unavailable=False)
