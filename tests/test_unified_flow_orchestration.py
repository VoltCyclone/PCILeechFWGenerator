#!/usr/bin/env python3
"""
Unit tests for unified 3-stage flow orchestration in pcileech.py

Tests the flow:
  Stage 1: run_host_collect() - Host-side device data collection
  Stage 2: run_container_templating() - Container-based template rendering
  Stage 3: run_host_vivado() - Host-side Vivado synthesis
"""
import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch, mock_open


# Module path constant for HostCollector to improve maintainability.
HOST_COLLECTOR_CLASS_PATH = "src.host_collect.collector.HostCollector"
FIND_VIVADO_INSTALLATION_PATH = "src.vivado_handling.find_vivado_installation"
VIVADO_RUNNER_PATH = "src.vivado_handling.VivadoRunner"


class TestHostCollect:
    """Test Stage 1: Host data collection."""

    def test_run_host_collect_creates_datastore(self, tmp_path, monkeypatch):
        """Verify run_host_collect creates datastore directory structure."""
        import pcileech

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(tmp_path / "datastore"),
            no_mmio_learning=False,
            force_recapture=False,
        )

        # Mock HostCollector
        mock_collector = MagicMock()
        mock_collector.run.return_value = 0

        mock_path = HOST_COLLECTOR_CLASS_PATH
        with patch(mock_path, return_value=mock_collector):
            result = pcileech.run_host_collect(args)

        assert result == 0
        # Verify datastore and output dirs were created
        assert (tmp_path / "datastore").exists()
        assert (tmp_path / "datastore" / "output").exists()

        # Verify collector was called
        mock_collector.run.assert_called_once()

    def test_run_host_collect_passes_mmio_flags(self, tmp_path):
        """Verify MMIO learning flags are passed to collector."""
        import pcileech

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(tmp_path / "datastore"),
            no_mmio_learning=True,
            force_recapture=True,
        )

        mock_collector_class = MagicMock()
        mock_collector = MagicMock()
        mock_collector.run.return_value = 0
        mock_collector_class.return_value = mock_collector

        mock_path = HOST_COLLECTOR_CLASS_PATH
        with patch(mock_path, mock_collector_class):
            pcileech.run_host_collect(args)

        # Verify constructor was called with correct flags
        mock_collector_class.assert_called_once()
        call_kwargs = mock_collector_class.call_args[1]
        assert call_kwargs["enable_mmio_learning"] is False
        assert call_kwargs["force_recapture"] is True

    def test_run_host_collect_handles_failure(self, tmp_path):
        """Verify run_host_collect handles collector failures."""
        import pcileech

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(tmp_path / "datastore"),
            no_mmio_learning=False,
            force_recapture=False,
        )

        mock_collector = MagicMock()
        mock_collector.run.return_value = 1  # Failure

        mock_path = HOST_COLLECTOR_CLASS_PATH
        with patch(mock_path, return_value=mock_collector):
            result = pcileech.run_host_collect(args)

        assert result == 1


class TestContainerTemplating:
    """Test Stage 2: Container templating."""

    @pytest.mark.skip(reason="Container tests require actual runtime and cause stdin issues in pytest")
    def test_run_container_templating_builds_image(self, tmp_path, monkeypatch):
        """Verify container image is built before running."""
        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "device_context.json").write_text("{}")
        (datastore / "output").mkdir()

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            generate_donor_template=None,
        )

        mock_subprocess = MagicMock()
        mock_subprocess.call.return_value = 0

        with patch("pcileech.shutil.which", return_value="/usr/bin/podman"):
            with patch("pcileech._ensure_container_image", return_value=True):
                with patch("pcileech.subprocess", mock_subprocess):
                    result = pcileech.run_container_templating(args)

        assert result == 0
        # Verify subprocess.call was invoked for podman run
        assert mock_subprocess.call.called

    @pytest.mark.skip(reason="Container tests require actual runtime and cause stdin issues in pytest")
    def test_run_container_templating_sets_env_vars(self, tmp_path):
        """Verify container gets correct environment variables."""
        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "device_context.json").write_text("{}")
        (datastore / "output").mkdir()

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            generate_donor_template=None,
        )

        mock_subprocess = MagicMock()
        mock_subprocess.call.return_value = 0

        with patch("pcileech.shutil.which", return_value="/usr/bin/podman"):
            with patch("pcileech._ensure_container_image", return_value=True):
                with patch("pcileech.subprocess", mock_subprocess):
                    pcileech.run_container_templating(args)

        # Get the command that was called
        call_args = mock_subprocess.call.call_args[0][0]

        # Verify environment variables are set
        device_ctx_check = any(
            "DEVICE_CONTEXT_PATH=/datastore/device_context.json" in arg
            for arg in call_args
        )
        assert device_ctx_check
        host_ctx_check = any("PCILEECH_HOST_CONTEXT_ONLY=1" in arg for arg in call_args)
        assert host_ctx_check
        vfio_check = any("PCILEECH_DISABLE_VFIO=1" in arg for arg in call_args)
        assert vfio_check

    @pytest.mark.skip(reason="Container tests require actual runtime and cause stdin issues in pytest")
    def test_run_container_templating_mounts_datastore(self, tmp_path):
        """Verify datastore is mounted to /datastore in container."""
        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "device_context.json").write_text("{}")
        (datastore / "output").mkdir()

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            generate_donor_template=None,
        )

        mock_subprocess = MagicMock()
        mock_subprocess.call.return_value = 0

        with patch("pcileech.shutil.which", return_value="/usr/bin/podman"):
            with patch("pcileech._ensure_container_image", return_value=True):
                with patch("pcileech.subprocess", mock_subprocess):
                    pcileech.run_container_templating(args)

        call_args = mock_subprocess.call.call_args[0][0]

        # Verify volume mount
        assert any(f"{str(datastore)}:/datastore" in arg for arg in call_args)

    def test_run_container_templating_falls_back_to_local(self, tmp_path):
        """Verify fallback to local templating when podman unavailable."""
        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "device_context.json").write_text("{}")
        (datastore / "output").mkdir()

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            generate_donor_template=None,
        )

        with patch("pcileech.shutil.which", return_value=None):  # No podman
            with patch("pcileech.run_local_templating", return_value=0) as mock_local:
                result = pcileech.run_container_templating(args)

        assert result == 0
        mock_local.assert_called_once_with(args)


class TestLocalTemplating:
    """Test Stage 2 alternative: Local templating (no container)."""

    def test_run_local_templating_sets_env_vars(self, tmp_path, monkeypatch):
        """Verify local templating sets required environment variables."""
        import os

        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "device_context.json").write_text("{}")
        (datastore / "msix_data.json").write_text("{}")
        (datastore / "output").mkdir()

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            generate_donor_template=None,
            donor_template=None,
            vivado_path=None,
            vivado_jobs=4,
            vivado_timeout=3600,
            enable_error_injection=False,
        )

        # Mock the builder
        mock_builder = MagicMock()
        mock_builder.build.return_value = ["artifact1", "artifact2"]

        with patch("src.build.FirmwareBuilder", return_value=mock_builder):
            with patch("src.build.ConfigurationManager") as mock_cfg_mgr:
                mock_cfg = MagicMock()
                mock_cfg_mgr.return_value.create_from_args.return_value = mock_cfg

                result = pcileech.run_local_templating(args)

        assert result == 0

        # Verify environment variables were set
        expected_device_path = str(datastore / "device_context.json")
        assert os.environ.get("DEVICE_CONTEXT_PATH") == expected_device_path
        expected_msix_path = str(datastore / "msix_data.json")
        assert os.environ.get("MSIX_DATA_PATH") == expected_msix_path
        assert os.environ.get("PCILEECH_HOST_CONTEXT_ONLY") == "1"


class TestHostVivado:
    """Test Stage 3: Host Vivado synthesis."""

    def test_run_host_vivado_requires_output_dir(self, tmp_path):
        """Verify run_host_vivado checks for output directory."""
        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        # Note: NOT creating output directory

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            vivado_path=None,
        )

        with patch("pcileech.get_logger"):
            result = pcileech.run_host_vivado(args)

        # Should fail when output dir missing
        assert result == 1

    def test_run_host_vivado_finds_vivado_auto(self, tmp_path):
        """Verify run_host_vivado can auto-detect Vivado installation."""
        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "output").mkdir()

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            vivado_path=None,  # Auto-detect
        )

        mock_runner = MagicMock()

        vivado_info = {
            "executable": "/tools/Xilinx/Vivado/2025.1/bin/vivado",
            "version": "2025.1",
        }

        vivado_find_path = FIND_VIVADO_INSTALLATION_PATH
        vivado_runner_path = VIVADO_RUNNER_PATH
        with patch(vivado_find_path, return_value=vivado_info):
            with patch(vivado_runner_path, return_value=mock_runner):
                with patch("pcileech.get_logger"):
                    result = pcileech.run_host_vivado(args)

        assert result == 0
        mock_runner.run.assert_called_once()

    def test_run_host_vivado_uses_explicit_path(self, tmp_path):
        """Verify run_host_vivado uses explicit vivado_path when provided."""
        import pcileech

        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "output").mkdir()

        explicit_path = "/custom/vivado/path"

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            vivado_path=explicit_path,
        )

        mock_runner = MagicMock()

        vivado_runner_path = VIVADO_RUNNER_PATH
        with patch(vivado_runner_path, return_value=mock_runner) as mock_runner_class:
            with patch("pcileech.get_logger"):
                result = pcileech.run_host_vivado(args)

        assert result == 0
        # Verify VivadoRunner was created with explicit path
        assert mock_runner_class.call_args[1]["vivado_path"] == explicit_path


class TestUnifiedFlowOrchestration:
    """Test the complete 3-stage flow orchestration."""

    def test_handle_build_runs_all_three_stages(self, tmp_path):
        """Verify handle_build orchestrates all 3 stages in order."""
        import pcileech

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(tmp_path / "datastore"),
            host_collect_only=False,
            local=False,
        )

        # Track call order
        call_order = []

        def mock_host_collect(a):
            call_order.append("host_collect")
            # Create datastore structure
            (tmp_path / "datastore").mkdir(exist_ok=True)
            (tmp_path / "datastore" / "device_context.json").write_text("{}")
            return 0

        def mock_container(a):
            call_order.append("container")
            return 0

        def mock_vivado(a):
            call_order.append("vivado")
            return 0

        boards_list = ["pcileech_35t325_x1"]
        with patch("pcileech.run_host_collect", side_effect=mock_host_collect):
            with patch("pcileech.run_container_templating", side_effect=mock_container):
                with patch("pcileech.run_host_vivado", side_effect=mock_vivado):
                    with patch(
                        "pcileech.get_available_boards", return_value=boards_list
                    ):
                        with patch("pcileech.get_logger"):
                            result = pcileech.handle_build(args)

        assert result == 0
        # Verify stages ran in correct order
        assert call_order == ["host_collect", "container", "vivado"]

    def test_handle_build_stops_on_stage1_failure(self, tmp_path):
        """Verify build stops if Stage 1 fails."""
        import pcileech

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(tmp_path / "datastore"),
            host_collect_only=False,
            local=False,
        )

        call_order = []

        def mock_host_collect(a):
            call_order.append("host_collect")
            return 1  # Failure

        boards_list = ["pcileech_35t325_x1"]
        with patch("pcileech.run_host_collect", side_effect=mock_host_collect):
            with patch("pcileech.run_container_templating") as mock_container:
                with patch("pcileech.run_host_vivado") as mock_vivado:
                    with patch(
                        "pcileech.get_available_boards", return_value=boards_list
                    ):
                        with patch("pcileech.get_logger"):
                            # Create device_context to trigger Stage 1
                            (tmp_path / "datastore").mkdir(exist_ok=True)
                            result = pcileech.handle_build(args)

        assert result == 1
        # Container and Vivado should NOT have been called
        mock_container.assert_not_called()
        mock_vivado.assert_not_called()

    def test_handle_build_skips_collect_if_datastore_exists(self, tmp_path):
        """Verify Stage 1 is skipped if datastore already exists."""
        import pcileech

        # Pre-create datastore
        datastore = tmp_path / "datastore"
        datastore.mkdir()
        (datastore / "device_context.json").write_text("{}")

        args = SimpleNamespace(
            bdf="0000:03:00.0",
            board="pcileech_35t325_x1",
            datastore=str(datastore),
            host_collect_only=False,
            local=False,
        )

        boards_list = ["pcileech_35t325_x1"]
        with patch("pcileech.run_host_collect") as mock_collect:
            with patch("pcileech.run_container_templating", return_value=0):
                with patch("pcileech.run_host_vivado", return_value=0):
                    with patch(
                        "pcileech.get_available_boards", return_value=boards_list
                    ):
                        with patch("pcileech.get_logger"):
                            result = pcileech.handle_build(args)

        assert result == 0
        # Host collect should NOT have been called
        mock_collect.assert_not_called()
