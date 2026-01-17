from pathlib import Path

import pytest

from src import pcileech_core_discovery


def test_discover_pcileech_files_generic(monkeypatch):
    # Patch RepoManager.ensure_repo to use a temp dir
    monkeypatch.setattr(
        pcileech_core_discovery.RepoManager, "ensure_repo", lambda: Path("/tmp")
    )
    # Patch TemplateDiscovery.get_pcileech_core_files to return a dummy file
    monkeypatch.setattr(
        pcileech_core_discovery.TemplateDiscovery,
        "get_pcileech_core_files",
        lambda repo: {"pcileech_header.svh": Path("/tmp/pcileech_header.svh")},
    )
    # Patch _enhanced_file_search to always return a dummy path
    monkeypatch.setattr(
        pcileech_core_discovery,
        "_enhanced_file_search",
        lambda repo, fname: Path(f"/tmp/{fname}"),
    )
    files = pcileech_core_discovery.discover_pcileech_files()
    assert "pcileech_header.svh" in files
    assert isinstance(files["pcileech_header.svh"], Path)


def test_validate_pcileech_environment_missing(monkeypatch):
    # Simulate missing critical files
    files = {"pcileech_mux.sv": Path("/tmp/pcileech_mux.sv")}
    issues = pcileech_core_discovery.validate_pcileech_environment(files)
    assert any("Missing critical files" in issue for issue in issues)


def test_validate_pcileech_environment_access(monkeypatch, tmp_path):
    # Create a dummy file
    dummy_file = tmp_path / "pcileech_mux.sv"
    dummy_file.write_text("dummy")
    files = {"pcileech_mux.sv": dummy_file}
    issues = pcileech_core_discovery.validate_pcileech_environment(files)
    # Expect missing critical files since only a non-critical file is present
    assert any("Missing critical files" in issue for issue in issues)


def test_discover_pcileech_files_board(monkeypatch):
    # Patch board config and search
    monkeypatch.setattr(
        pcileech_core_discovery.RepoManager, "ensure_repo", lambda: Path("/tmp")
    )
    monkeypatch.setattr(
        pcileech_core_discovery.TemplateDiscovery,
        "get_pcileech_core_files",
        lambda repo: {},
    )

    class DummyBoardConfig(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    def dummy_get_pcileech_board_config(board_name, repo_root):
        return DummyBoardConfig(
            {"fpga_family": "ultrascale", "supports_msix": True, "has_option_rom": True}
        )

    monkeypatch.setattr(
        "pcileechfwgenerator.device_clone.board_config.get_pcileech_board_config",
        dummy_get_pcileech_board_config,
    )
    monkeypatch.setattr(
        pcileech_core_discovery,
        "_enhanced_file_search",
        lambda repo, fname: Path(f"/tmp/{fname}"),
    )
    files = pcileech_core_discovery.discover_pcileech_files(board_name="dummy")
    assert "pcileech_pcie_cfg_us.sv" in files
    assert "msix_capability_registers.sv" in files
    assert "option_rom_bar_window.sv" in files


def test_enhanced_file_search(tmp_path):
    # Create a nested directory structure and file
    nested_dir = tmp_path / "pcileech" / "rtl"
    nested_dir.mkdir(parents=True)
    target_file = nested_dir / "testfile.sv"
    target_file.write_text("dummy")
    # Should find the file in the nested structure
    result = pcileech_core_discovery._enhanced_file_search(tmp_path, "testfile.sv")
    assert result == target_file


def test_enhanced_file_search_not_found(tmp_path):
    # No file present
    result = pcileech_core_discovery._enhanced_file_search(tmp_path, "missing.sv")
    assert result is None


def test_discover_pcileech_files_board_config_error(monkeypatch):
    # Simulate board config import error
    monkeypatch.setattr(
        pcileech_core_discovery.RepoManager, "ensure_repo", lambda: Path("/tmp")
    )
    monkeypatch.setattr(
        pcileech_core_discovery.TemplateDiscovery,
        "get_pcileech_core_files",
        lambda repo: {},
    )

    def raise_import_error(*args, **kwargs):
        raise ImportError("fail")

    monkeypatch.setattr(
        "pcileechfwgenerator.device_clone.board_config.get_pcileech_board_config", raise_import_error
    )
    monkeypatch.setattr(
        pcileech_core_discovery, "_enhanced_file_search", lambda repo, fname: None
    )
    files = pcileech_core_discovery.discover_pcileech_files(board_name="dummy")
    # Should fallback to generic search and return at least some files
    assert isinstance(files, dict)


def test_discover_pcileech_files_board_path_error(monkeypatch):
    # Simulate board path lookup error
    monkeypatch.setattr(
        pcileech_core_discovery.RepoManager, "ensure_repo", lambda: Path("/tmp")
    )
    monkeypatch.setattr(
        pcileech_core_discovery.TemplateDiscovery,
        "get_pcileech_core_files",
        lambda repo: {},
    )

    class DummyBoardConfig(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    def dummy_get_pcileech_board_config(board_name, repo_root):
        return DummyBoardConfig({"fpga_family": "ultrascale"})

    monkeypatch.setattr(
        "pcileechfwgenerator.device_clone.board_config.get_pcileech_board_config",
        dummy_get_pcileech_board_config,
    )

    def raise_path_error(*args, **kwargs):
        raise Exception("fail")

    monkeypatch.setattr(
        pcileech_core_discovery.RepoManager, "get_board_path", raise_path_error
    )
    monkeypatch.setattr(
        pcileech_core_discovery, "_enhanced_file_search", lambda repo, fname: None
    )
    files = pcileech_core_discovery.discover_pcileech_files(board_name="dummy")
    assert isinstance(files, dict)


def test_validate_pcileech_environment_file_not_accessible(tmp_path):
    # File path does not exist
    files = {"pcileech_mux.sv": tmp_path / "not_a_file.sv"}
    issues = pcileech_core_discovery.validate_pcileech_environment(files)
    assert any("File not accessible" in issue for issue in issues)


def test_validate_pcileech_environment_path_not_file(tmp_path):
    # Path exists but is a directory
    dir_path = tmp_path / "adir"
    dir_path.mkdir()
    files = {"pcileech_mux.sv": dir_path}
    issues = pcileech_core_discovery.validate_pcileech_environment(files)
    assert any("Path is not a file" in issue for issue in issues)
