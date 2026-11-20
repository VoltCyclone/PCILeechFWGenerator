"""Board test fixtures must not become production HDL or overwrite its headers."""

from pathlib import Path

import pytest
from pcileechfwgenerator.file_management.file_manager import FileManager
from pcileechfwgenerator.file_management.repo_manager import RepoManager


@pytest.fixture
def board_copy(tmp_path, monkeypatch):
    board = tmp_path / "tests" / "repo" / "board"
    board.mkdir(parents=True)
    monkeypatch.setattr(RepoManager, "ensure_repo", lambda: board.parent)
    monkeypatch.setattr(RepoManager, "get_board_path", lambda *a, **k: board)
    monkeypatch.setattr(RepoManager, "get_xdc_files", lambda *a, **k: [])
    out = tmp_path / "output"
    return board, out, FileManager(out)


def write(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.mark.parametrize(
    "simulation_directory",
    [
        "sim/r1",
        "test",
        "tests",
        "tb",
        "testbench",
        "testbenches",
        "simulation",
        "simulations",
        "rtl/Sim/r1",
    ],
)
def test_production_copy_excludes_simulation_sources_and_header_collisions(
    board_copy, simulation_directory
):
    board, out, manager = board_copy
    # Create simulation entries first: header discovery must not depend on the
    # filesystem traversal order, nor allow these same-basename replacements.
    write(board, simulation_directory + "/bridge.sv", "module bench; endmodule\n")
    write(board, simulation_directory + "/defs.svh", "`define WRONG_HEADER 1\n")
    write(board, simulation_directory + "/types_pkg.sv", "package wrong; endpackage\n")
    write(
        board, simulation_directory + "/simulation_only.v", "module model;\nendmodule\n"
    )
    write(board, "bridge.sv", "module bridge; endmodule\n")
    write(board, "rtl/serial.v", "module serial; endmodule\n")
    write(board, "rtl/include/defs.svh", "`define PRODUCTION_HEADER 1\n")
    write(board, "rtl/include/types_pkg.sv", "package production; endpackage\n")
    # Only exact directory components are excluded: do not guess from filenames
    # or substrings in otherwise valid production layouts.
    write(board, "rtl/tb_bridge.sv", "module tb_bridge; endmodule\n")
    write(board, "sim_support/helper.sv", "module helper; endmodule\n")

    copied = manager.copy_pcileech_sources("board")

    src = out / "src"
    assert (src / "bridge.sv").read_text() == "module bridge; endmodule\n"
    assert (src / "defs.svh").read_text() == "`define PRODUCTION_HEADER 1\n"
    assert (src / "types_pkg.sv").read_text() == "package production; endpackage\n"
    assert (src / "serial.v").exists()
    assert (src / "tb_bridge.sv").exists()
    assert (src / "helper.sv").exists()
    assert not (src / "simulation_only.v").exists()
    assert "simulation_only.v" not in {
        Path(path).name for paths in copied.values() for path in paths
    }


def test_simulation_collision_cannot_add_modules_to_generated_source(board_copy):
    board, out, manager = board_copy
    write(board, "sim/r1/controller.sv", "module model;\nendmodule\n")
    write(
        board,
        "src/controller.sv",
        "module controller;\nendmodule\nmodule engine;\nendmodule\n",
    )
    generated = write(out, "src/controller.sv", "module controller;\nendmodule\n")

    copied = manager.copy_pcileech_sources("board", generated=["controller.sv"])

    assert generated.read_text() == "module controller;\nendmodule\n"
    companion = out / "src/controller_board_modules.sv"
    assert "module engine;" in companion.read_text()
    assert "module model;" not in companion.read_text()
    assert copied["systemverilog"].count(str(companion)) == 1
