import asyncio
import datetime
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from ..models.config import BuildConfiguration
from ..models.device import PCIDevice
from ..models.progress import BuildProgress, BuildStage
from .build_orchestrator import RESOURCE_MONITOR_INTERVAL, BuildOrchestrator


class TestBuildOrchestrator(unittest.TestCase):
    def setUp(self):
        self.orchestrator = BuildOrchestrator()
        self.device = PCIDevice(
            bdf="0000:00:00.0",
            vendor_id="0x1234",
            device_id="0x5678",
            vendor_name="Test Vendor",
            device_name="Test Device",
            device_class="0x123456",
            subsystem_vendor="0xabcd",
            subsystem_device="0xef01",
            driver=None,
            iommu_group="1",
            power_state="D0",
            link_speed="Gen3 x16",
            bars=[],
            suitability_score=1.0,
            compatibility_issues=[],
        )
        self.config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=True,
            behavior_profiling=False,
            donor_dump=False,
            donor_info_file="",
            advanced_sv=False,
            profile_duration=10,
            disable_ftrace=True,
            auto_install_headers=False,
        )

    @patch("asyncio.create_subprocess_shell")
    async def test_run_shell_without_monitoring(self, mock_subprocess):
        # Setup mock
        process_mock = AsyncMock()
        process_mock.returncode = 0
        process_mock.communicate.return_value = (b"stdout", b"stderr")
        mock_subprocess.return_value = process_mock

        # Run test
        result = await self.orchestrator._run_shell("test command", monitor=False)

        # Assert
        mock_subprocess.assert_called_once()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "stdout")
        self.assertEqual(result.stderr, "stderr")

    @patch("asyncio.create_subprocess_shell")
    async def test_run_shell_with_monitoring(self, mock_subprocess):
        # Setup mock
        process_mock = AsyncMock()
        process_mock.returncode = 0
        process_mock.stdout = AsyncMock()
        process_mock.stdout.readline = AsyncMock(
            side_effect=[b"Running synthesis\n", b""]
        )
        process_mock.stderr = AsyncMock()
        process_mock.stderr.read = AsyncMock(return_value=b"")
        mock_subprocess.return_value = process_mock

        # Setup progress tracking
        self.orchestrator._current_progress = BuildProgress(
            stage=BuildStage.ENVIRONMENT_VALIDATION,
            completion_percent=0.0,
            current_operation="Testing",
        )
        self.orchestrator._update_progress = AsyncMock()

        # Run test
        result = await self.orchestrator._run_shell("test command", monitor=True)

        # Assert
        mock_subprocess.assert_called_once()
        self.assertEqual(result.returncode, 0)
        self.orchestrator._update_progress.assert_called_with(
            BuildStage.VIVADO_SYNTHESIS, 25, "Running synthesis"
        )

    @patch("asyncio.create_subprocess_shell")
    async def test_run_shell_with_error(self, mock_subprocess):
        # Setup mock
        process_mock = AsyncMock()
        process_mock.returncode = 1
        process_mock.stdout = AsyncMock()
        process_mock.stdout.readline = AsyncMock(return_value=b"")
        process_mock.stderr = AsyncMock()
        process_mock.stderr.read = AsyncMock(return_value=b"error message")
        mock_subprocess.return_value = process_mock

        # Setup progress tracking
        self.orchestrator._current_progress = BuildProgress(
            stage=BuildStage.ENVIRONMENT_VALIDATION,
            completion_percent=0.0,
            current_operation="Testing",
        )
        self.orchestrator._update_progress = AsyncMock()

        # Run test and check for exception
        with self.assertRaises(RuntimeError):
            await self.orchestrator._run_shell("test command", monitor=True)

        # Verify error was added to progress
        self.orchestrator._current_progress.add_error.assert_called_once()

    @patch.object(BuildOrchestrator, "_update_progress")
    @patch.object(BuildOrchestrator, "_notify_progress")
    async def test_update_resource_usage(self, _mock_notify, _mock_update):
        # Setup
        self.orchestrator._current_progress = MagicMock()

        with patch("asyncio.get_running_loop") as mock_loop, patch(
            "psutil.cpu_percent"
        ) as mock_cpu, patch("psutil.virtual_memory") as mock_memory, patch(
            "psutil.disk_usage"
        ) as mock_disk:

            # Configure mocks
            mock_cpu.return_value = 25.0
            mock_memory.return_value = MagicMock(used=8 * 1024**3)  # 8 GB
            mock_disk.return_value = MagicMock(free=100 * 1024**3)  # 100 GB

            loop_mock = MagicMock()
            loop_mock.run_in_executor = AsyncMock()
            loop_mock.run_in_executor.side_effect = [
                mock_memory.return_value,
                mock_disk.return_value,
            ]
            mock_loop.return_value = loop_mock

            # Run test
            await self.orchestrator._update_resource_usage()

            # Assert
            self.orchestrator._current_progress.update_resource_usage.assert_called_once_with(
                cpu=25.0, memory=8.0, disk_free=100.0
            )

    async def test_update_progress_skips_resource_update_when_recently_updated(self):
        # Setup
        self.orchestrator._current_progress = MagicMock()
        self.orchestrator._update_resource_usage = AsyncMock()
        self.orchestrator._notify_progress = AsyncMock()

        # Set last resource update to be very recent
        self.orchestrator._last_resource_update = datetime.datetime.now().timestamp()

        # Run test
        await self.orchestrator._update_progress(
            BuildStage.ENVIRONMENT_VALIDATION, 50, "test"
        )

        # Assert that resource usage was not updated
        self.orchestrator._update_resource_usage.assert_not_called()

        # But progress was still notified
        self.orchestrator._notify_progress.assert_called_once()

    async def test_update_progress_updates_resources_when_time_elapsed(self):
        # Setup
        self.orchestrator._current_progress = MagicMock()
        self.orchestrator._update_resource_usage = AsyncMock()
        self.orchestrator._notify_progress = AsyncMock()

        # Set last resource update to be in the past
        self.orchestrator._last_resource_update = (
            datetime.datetime.now().timestamp() - RESOURCE_MONITOR_INTERVAL - 0.1
        )

        # Run test
        await self.orchestrator._update_progress(
            BuildStage.ENVIRONMENT_VALIDATION, 50, "test"
        )

        # Assert that resource usage was updated
        self.orchestrator._update_resource_usage.assert_called_once()
        self.orchestrator._notify_progress.assert_called_once()

    @patch.object(BuildOrchestrator, "_create_build_stages")
    @patch.object(BuildOrchestrator, "_run_stage")
    @patch.object(BuildOrchestrator, "_notify_progress")
    async def test_start_build_success(
        self, _mock_notify, mock_run_stage, mock_create_stages
    ):
        # Setup
        callback_mock = MagicMock()
        mock_create_stages.return_value = [
            (BuildStage.ENVIRONMENT_VALIDATION, AsyncMock(), "start", "end")
        ]

        # Run test
        result = await self.orchestrator.start_build(
            self.device, self.config, callback_mock
        )

        # Assert
        self.assertTrue(result)
        mock_create_stages.assert_called_once_with(self.device, self.config)
        mock_run_stage.assert_called_once()
        self.assertEqual(self.orchestrator._current_progress.completion_percent, 100.0)

    @patch.object(BuildOrchestrator, "_create_build_stages")
    @patch.object(BuildOrchestrator, "_run_stage")
    async def test_start_build_exception(self, mock_run_stage, mock_create_stages):
        # Setup
        callback_mock = MagicMock()
        mock_create_stages.return_value = [
            (BuildStage.ENVIRONMENT_VALIDATION, AsyncMock(), "start", "end")
        ]
        mock_run_stage.side_effect = RuntimeError("Test error")

        # Run test and verify exception
        with self.assertRaises(RuntimeError):
            await self.orchestrator.start_build(self.device, self.config, callback_mock)

        # Assert error was recorded
        self.assertIn(
            "Build failed: Test error", self.orchestrator._current_progress.errors
        )

    async def test_start_build_already_in_progress(self):
        # Setup
        self.orchestrator._is_building = True

        # Run test and verify exception
        with self.assertRaises(RuntimeError):
            await self.orchestrator.start_build(self.device, self.config, None)

    @patch.object(BuildOrchestrator, "_run_stage")
    @patch.object(BuildOrchestrator, "_create_build_stages")
    async def test_cancel_build(self, mock_create_stages, mock_run_stage):
        # Setup
        callback_mock = MagicMock()

        # Mock stage that can be cancelled
        async def cancellable_stage():
            if self.orchestrator._should_cancel:
                raise asyncio.CancelledError()
            return True

        mock_create_stages.return_value = [
            (BuildStage.ENVIRONMENT_VALIDATION, cancellable_stage, "start", "end")
        ]

        # Start build in background task
        build_task = asyncio.create_task(
            self.orchestrator.start_build(self.device, self.config, callback_mock)
        )

        # Allow task to start
        await asyncio.sleep(0.1)

        # Cancel build
        await self.orchestrator.cancel_build()

        # Wait for build to complete
        result = await build_task

        # Assert
        self.assertFalse(result)
        self.assertTrue(
            "Build cancelled by user" in self.orchestrator._current_progress.warnings
        )

    @patch.object(BuildOrchestrator, "_validate_environment")
    @patch.object(BuildOrchestrator, "_validate_pci_config")
    async def test_create_build_stages_basic(
        self, _mock_validate_pci, _mock_validate_env
    ):
        # Setup basic config
        config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=True,
            behavior_profiling=False,
            donor_dump=False,
            donor_info_file="",
            advanced_sv=False,
        )

        # Run test
        stages = self.orchestrator._create_build_stages(self.device, config)

        # Assert basic stages are present
        stage_types = [stage[0] for stage in stages]
        self.assertEqual(len(stages), 7)  # 2 validation + 2 analysis + 3 generation
        self.assertIn(BuildStage.ENVIRONMENT_VALIDATION, stage_types)
        self.assertIn(BuildStage.DEVICE_ANALYSIS, stage_types)
        self.assertIn(BuildStage.REGISTER_EXTRACTION, stage_types)
        self.assertIn(BuildStage.SYSTEMVERILOG_GENERATION, stage_types)
        self.assertIn(BuildStage.VIVADO_SYNTHESIS, stage_types)
        self.assertIn(BuildStage.BITSTREAM_GENERATION, stage_types)

    @patch.object(BuildOrchestrator, "_validate_environment")
    @patch.object(BuildOrchestrator, "_validate_pci_config")
    async def test_create_build_stages_with_behavior_profiling(
        self, _mock_validate_pci, _mock_validate_env
    ):
        # Setup config with behavior profiling
        config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=True,
            behavior_profiling=True,
            donor_dump=False,
            donor_info_file="",
            advanced_sv=False,
        )

        # Run test
        stages = self.orchestrator._create_build_stages(self.device, config)

        # Assert behavior profiling stage is included
        behavior_profiling_stages = [
            s for s in stages if s[1].__name__ == "_run_behavior_profiling"
        ]
        self.assertEqual(len(behavior_profiling_stages), 1)

    @patch.object(BuildOrchestrator, "_validate_environment")
    @patch.object(BuildOrchestrator, "_validate_pci_config")
    async def test_create_build_stages_with_donor_dump(
        self, _mock_validate_pci, _mock_validate_env
    ):
        # Setup config with donor dump
        config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=False,
            behavior_profiling=False,
            donor_dump=True,
            donor_info_file="",
            advanced_sv=False,
        )

        # Run test
        stages = self.orchestrator._create_build_stages(self.device, config)

        # Assert donor module check is included
        donor_check_stages = [
            s for s in stages if s[1].__name__ == "_check_donor_module"
        ]
        self.assertEqual(len(donor_check_stages), 1)

    @patch("os.geteuid", return_value=0)  # Mock as root
    @patch.object(BuildOrchestrator, "_run_shell")
    @patch.object(BuildOrchestrator, "_build_container_image")
    @patch.object(BuildOrchestrator, "_ensure_git_repo")
    async def test_validate_container_environment_success(
        self, _mock_git, mock_build_container, mock_run_shell, _mock_geteuid
    ):
        # Setup
        mock_run_shell.side_effect = [
            # podman --version
            MagicMock(returncode=0),
            # podman images
            MagicMock(returncode=0, stdout="pcileech-fw-generator"),
        ]

        # Run test
        await self.orchestrator._validate_container_environment()

        # Assert
        mock_run_shell.assert_called()
        mock_build_container.assert_not_called()  # Container exists, no need to build

    @patch("os.geteuid", return_value=1000)  # Mock as non-root
    async def test_validate_container_environment_no_root(self, _mock_geteuid):
        # Run test and expect exception
        with self.assertRaises(RuntimeError) as context:
            await self.orchestrator._validate_container_environment()

        # Assert error message
        self.assertIn("Root privileges required", str(context.exception))

    @patch("os.geteuid", return_value=0)  # Mock as root
    @patch.object(BuildOrchestrator, "_run_shell")
    @patch.object(BuildOrchestrator, "_build_container_image")
    async def test_validate_container_environment_no_container(
        self, mock_build_container, mock_run_shell, _mock_geteuid
    ):
        # Setup
        mock_run_shell.side_effect = [
            # podman --version
            MagicMock(returncode=0),
            # podman images
            MagicMock(returncode=0, stdout=""),
        ]

        # Run test
        await self.orchestrator._validate_container_environment()

        # Assert container build was attempted
        mock_build_container.assert_called_once()

    @patch("pathlib.Path.exists")
    async def test_validate_local_environment_success(self, mock_exists):
        # Setup
        mock_exists.return_value = True
        config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=True,
            donor_info_file=None,
        )

        # Run test
        await self.orchestrator._validate_local_environment(config)

        # No assertions needed - function should complete without raising exceptions

    @patch("pathlib.Path.exists")
    async def test_validate_local_environment_no_build_py(self, mock_exists):
        # Setup - build.py doesn't exist
        mock_exists.return_value = False
        config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=True,
            donor_info_file=None,
        )

        # Run test and expect exception
        with self.assertRaises(RuntimeError) as context:
            await self.orchestrator._validate_local_environment(config)

        # Assert error message
        self.assertIn("build.py not found", str(context.exception))

    @patch("os.makedirs")
    @patch("shutil.rmtree")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    @patch("pathlib.Path.exists")
    async def test_clone_git_repo_success(
        self, mock_exists, mock_open, _mock_rmtree, _mock_makedirs
    ):
        # Skip test if git is not available
        if not self.orchestrator.GIT_AVAILABLE:
            pytest.skip("Git not available")

        # Setup mocks
        self.orchestrator._current_progress = MagicMock()
        repo_dir = Path("/tmp/test-repo")

        with patch("git.Repo.clone_from") as mock_clone:
            # Run test
            await self.orchestrator._clone_git_repo(repo_dir)

            # Assert
            mock_clone.assert_called_once()
            mock_open.assert_called_once()
            self.orchestrator._current_progress.current_operation.assert_called()

    @patch("os.makedirs")
    async def test_ensure_repo_fallback(self, mock_makedirs):
        # Setup
        self.orchestrator._current_progress = MagicMock()
        repo_dir = Path("/tmp/test-repo")

        # Run test
        await self.orchestrator._ensure_repo_fallback(repo_dir)

        # Assert
        mock_makedirs.assert_called_once_with(repo_dir, exist_ok=True)
        self.orchestrator._current_progress.add_warning.assert_called_once()

    async def test_ensure_git_repo_creates_cache_dir(self):
        # Setup
        self.orchestrator._current_progress = MagicMock()
        self.orchestrator._ensure_repo_with_manager = AsyncMock()
        self.orchestrator._ensure_repo_with_git = AsyncMock()
        self.orchestrator._ensure_repo_fallback = AsyncMock()

        with patch("os.makedirs") as mock_makedirs:
            # Run test
            await self.orchestrator._ensure_git_repo()

            # Assert cache directory was created
            mock_makedirs.assert_called_once()

    @patch.object(BuildOrchestrator, "_run_shell")
    async def test_run_vivado_synthesis_local(self, mock_run_shell):
        # Setup
        self.orchestrator._current_progress = MagicMock()
        config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=True,
            advanced_sv=True,
            device_type="network",
            behavior_profiling=True,
            profile_duration=30,
        )

        # Run test
        await self.orchestrator._run_vivado_synthesis(self.device, config)

        # Assert
        mock_run_shell.assert_called_once()
        cmd = mock_run_shell.call_args[0][0]

        # Check command contains all expected flags
        self.assertIn("--bdf 0000:00:00.0", " ".join(cmd))
        self.assertIn("--board pcileech_35t325_x1", " ".join(cmd))
        self.assertIn("--advanced-sv", " ".join(cmd))
        self.assertIn("--enable-behavior-profiling", " ".join(cmd))
        self.assertIn("--profile-duration 30", " ".join(cmd))
        self.assertIn("--run-vivado", " ".join(cmd))

    @patch("asyncio.get_running_loop")
    @patch.object(BuildOrchestrator, "_run_shell")
    async def test_run_vivado_synthesis_container(self, mock_run_shell, mock_get_loop):
        # Setup
        self.orchestrator._current_progress = MagicMock()
        config = BuildConfiguration(
            board_type="pcileech_35t325_x1",
            local_build=False,
            advanced_sv=True,
            device_type="network",
        )

        # Mock iommu_group retrieval
        loop_mock = AsyncMock()
        executor_mock = AsyncMock()
        executor_mock.return_value = "42"  # iommu group
        loop_mock.run_in_executor = AsyncMock(return_value=executor_mock)
        mock_get_loop.return_value = loop_mock

        # Run test
        with patch("sys.path.append"):
            await self.orchestrator._run_vivado_synthesis(self.device, config)

        # Assert
        mock_run_shell.assert_called_once()
        cmd = mock_run_shell.call_args[0][0]

        # Check command contains container arguments
        self.assertIn("podman", cmd)
        self.assertIn("run", cmd)
        self.assertIn("--privileged", cmd)
        self.assertIn("--device=/dev/vfio/42", " ".join(cmd))
        self.assertIn("pcileech-fw-generator:latest", " ".join(cmd))

        # Check build flags are passed to container
        self.assertIn("--bdf 0000:00:00.0", " ".join(cmd))
        self.assertIn("--board pcileech_35t325_x1", " ".join(cmd))
        self.assertIn("--advanced-sv", " ".join(cmd))

    async def test_generate_bitstream(self):
        # Setup
        self.orchestrator._current_progress = MagicMock()
        config = BuildConfiguration(board_type="pcileech_35t325_x1")

        # Run test
        with patch("asyncio.sleep"):
            await self.orchestrator._generate_bitstream(config)

        # Assert
        self.orchestrator._current_progress.current_operation = (
            "Bitstream generation complete"
        )

    @patch("os.path.exists")
    async def test_analyze_device(self, mock_exists):
        # Setup
        mock_exists.return_value = False
        self.orchestrator._current_progress = MagicMock()

        # Run test
        with patch("asyncio.get_event_loop") as mock_loop, patch("sys.path.append"):

            loop_mock = MagicMock()
            loop_mock.run_in_executor = AsyncMock()
            loop_mock.run_in_executor.side_effect = [
                "vfio_pci",
                "42",
            ]  # driver, iommu_group
            mock_loop.return_value = loop_mock

            await self.orchestrator._analyze_device(self.device)

        # Assert warning was added for non-existent VFIO device
        self.orchestrator._current_progress.add_warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
