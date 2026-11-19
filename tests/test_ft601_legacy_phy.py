"""--ft601-legacy-phy: FT601_PHY_MMCM binding and MMCM constraint selection."""

import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pcileechfwgenerator import build
from pcileechfwgenerator.build import FirmwareBuilder
from pcileechfwgenerator.exceptions import PCILeechBuildError
from pcileechfwgenerator.file_management.repo_manager import (
    FT601_MMCM_XDC,
    RepoManager,
)
from pcileechfwgenerator.vivado_handling import (
    pcileech_build_integration,
    requester_plan,
)
from pcileechfwgenerator.vivado_handling.requester_plan import (
    FT601_MMCM_SOURCE,
    REQUESTER_SOURCE,
    render_requester_plan_tcl,
    stage_requester_plan,
)

import pcileech
from tests.test_requester_plan import policy

BOARD = "pcileech_100t484_x1"

try:
    REPO = RepoManager.ensure_repo()
    BOARD_PATH = RepoManager.get_board_path(BOARD, repo_root=REPO)
except Exception:  # pragma: no cover - submodule missing
    REPO = BOARD_PATH = None

needs_board = pytest.mark.skipif(BOARD_PATH is None, reason="needs voltcyclone-fpga")


@needs_board
def test_board_xdc_set_drops_only_the_mmcm_file_for_legacy():
    full = RepoManager.get_xdc_files(BOARD, repo_root=REPO)
    legacy = RepoManager.get_xdc_files(BOARD, repo_root=REPO, ft601_phy_mmcm=False)
    assert FT601_MMCM_XDC in [p.name for p in full]
    assert legacy == [p for p in full if p.name != FT601_MMCM_XDC]
    assert f"{FT601_MMCM_XDC} ====" in RepoManager.read_combined_xdc(
        BOARD, repo_root=REPO
    )
    combined = RepoManager.read_combined_xdc(
        BOARD, repo_root=REPO, ft601_phy_mmcm=False
    )
    assert FT601_MMCM_XDC not in combined
    assert (BOARD_PATH / "src" / FT601_MMCM_SOURCE).is_file()


def _run(tmp_path, ft601):
    tclsh = shutil.which("tclsh")
    if not tclsh:
        pytest.skip("needs Tcl")
    props = {
        "cfg_fc_if": "true",
        "User_Clk_Freq": "62.5",
        "Cpl_Timeout_Range": "Ranges_A,_B,_C_&_D",
        "Cpl_Timeout_Disable_Sup": "true",
        "Extended_Tag_Field": "true",
        "RECRC_Check": "0",
        "RECRC_Check_Trim": "false",
    }
    script = tmp_path / "run.tcl"
    script.write_text(
        "set props {}\n"
        + "".join(f"dict set props CONFIG.{k} {{{v}}}\n" for k, v in props.items())
        + "set generics {PARAM_DEVICE_ID=9}\n"
        "set enabled unset\n"
        "proc generate_target {args} {}\n"
        "proc get_ips {args} {return pcie_7x_0}\n"
        "proc get_filesets {args} {return [lindex $args end]}\n"
        "proc get_files {args} {return xdc/pcileech_ft601_mmcm.xdc}\n"
        "proc get_property {key obj} {\n"
        " if {$key eq {generic}} {return $::generics}\n"
        " return [dict get $::props $key]\n}\n"
        "proc set_property {key value obj} {\n"
        " if {$key eq {CONFIG.cfg_fc_if}} {return}\n"
        " if {$key eq {IS_ENABLED}} {set ::enabled $value; return}\n"
        " if {$key ne {generic} || $obj ne {sources_1}} {error {bad setter}}\n"
        " set ::generics $value\n}\n"
        + render_requester_plan_tcl(policy(), ft601_phy_mmcm=ft601)
        + "\nif {[catch {::pcileech_requester::apply} reason]} {\n"
        ' puts stderr $reason\n exit 1\n}\nputs "$enabled $generics"\n'
    )
    result = subprocess.run(
        [tclsh, str(script)], cwd=tmp_path, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    enabled, *generics = result.stdout.strip().splitlines()[-1].split()
    return enabled, dict(item.split("=", 1) for item in generics)


@pytest.mark.parametrize("ft601,value", [(False, "0"), (True, "1")])
def test_plan_binds_generic_and_leaves_constraints_to_stage2(tmp_path, ft601, value):
    """The plan only binds FT601_PHY_MMCM; Stage 2 chose the XDC set."""
    enabled, generics = _run(tmp_path, ft601)
    assert generics["FT601_PHY_MMCM"] == value
    assert generics["PARAM_DEVICE_ID"] == "9"
    assert enabled == "unset"
    assert "sync_ft601_xdc" not in render_requester_plan_tcl(
        policy(), ft601_phy_mmcm=ft601
    )


def test_board_without_mmcm_phy_gets_no_ft601_binding(tmp_path):
    enabled, generics = _run(tmp_path, None)
    assert "FT601_PHY_MMCM" not in generics
    assert enabled == "unset"
    assert "sync_ft601_xdc" not in render_requester_plan_tcl(policy())


def test_legacy_binding_without_requester_plan_is_refused(tmp_path):
    with pytest.raises(ValueError, match="legacy FT601"):
        stage_requester_plan(tmp_path, {}, sources=[], ft601_phy_mmcm=False)
    assert stage_requester_plan(tmp_path, {}, sources=[], ft601_phy_mmcm=True) is None


def _finalizer(tmp_path, monkeypatch, *, legacy, sources, combined):
    out = tmp_path / "out"
    (out / "constraints").mkdir(parents=True)
    builder = object.__new__(FirmwareBuilder)
    builder.config = SimpleNamespace(
        output_dir=out, board=BOARD, ft601_legacy_phy=legacy
    )
    builder.logger = MagicMock()
    builder._generated_memories = {}
    builder._generated_sources = list(sources)
    builder._staged_board_files = {
        "constraints": [str(out / "constraints" / "a.xdc"), FT601_MMCM_XDC]
    }
    if combined is not None:
        (out / "constraints" / f"{BOARD}.xdc").write_text(combined)
        builder._written_xdc = f"{BOARD}.xdc"
    seen = {}
    monkeypatch.setattr(
        pcileech_build_integration, "stage_generated_memories", lambda *a: {}
    )
    monkeypatch.setattr(
        requester_plan,
        "stage_requester_plan",
        lambda *a, **kw: seen.setdefault("ft601", kw["ft601_phy_mmcm"]),
    )
    monkeypatch.setattr(
        pcileech_build_integration,
        "write_stage2_manifest",
        lambda *a, **kw: seen.setdefault("constraints", kw["constraints"])
        and out / "manifest.json",
    )
    return builder, seen


SOURCES = ("pcileech_top.sv", REQUESTER_SOURCE, FT601_MMCM_SOURCE)


def test_legacy_on_board_without_mmcm_phy_is_refused(tmp_path, monkeypatch):
    builder, _ = _finalizer(
        tmp_path, monkeypatch, legacy=True, sources=SOURCES[:2], combined=None
    )
    with pytest.raises(PCILeechBuildError, match="MMCM FT601 PHY"):
        builder._finalize_stage2_artifacts({})


def test_legacy_without_requester_rtl_is_refused(tmp_path, monkeypatch):
    builder, _ = _finalizer(
        tmp_path,
        monkeypatch,
        legacy=True,
        sources=(SOURCES[0], SOURCES[2]),
        combined=None,
    )
    with pytest.raises(PCILeechBuildError, match=REQUESTER_SOURCE):
        builder._finalize_stage2_artifacts({})


@pytest.mark.parametrize("legacy", [False, True])
def test_stage2_constraints_follow_the_phy(tmp_path, monkeypatch, legacy):
    """No combined XDC: the copied board files are the fallback set."""
    builder, seen = _finalizer(
        tmp_path, monkeypatch, legacy=legacy, sources=SOURCES, combined=None
    )
    copy = builder.config.output_dir / "constraints" / FT601_MMCM_XDC
    copy.write_text("# mmcm\n")
    builder._finalize_stage2_artifacts({})
    assert copy.exists() is (not legacy)
    assert seen["ft601"] is (not legacy)
    assert (FT601_MMCM_XDC in seen["constraints"]) is (not legacy)
    assert "a.xdc" in seen["constraints"]


def test_board_without_mmcm_phy_is_not_bound(tmp_path, monkeypatch):
    builder, seen = _finalizer(
        tmp_path, monkeypatch, legacy=False, sources=SOURCES[:2], combined=None
    )
    builder._finalize_stage2_artifacts({})
    assert seen["ft601"] is None


def test_legacy_refuses_combined_xdc_with_mmcm_constraints(tmp_path, monkeypatch):
    builder, _ = _finalizer(
        tmp_path,
        monkeypatch,
        legacy=True,
        sources=SOURCES,
        combined=f"# ==== CaptainDMA/100t484-1/src/{FT601_MMCM_XDC} ====\n",
    )
    with pytest.raises(PCILeechBuildError, match="combined board XDC"):
        builder._finalize_stage2_artifacts({})


@pytest.mark.parametrize("opted", [False, True])
def test_flag_reaches_build_configuration_and_container(opted):
    extra = ("--ft601-legacy-phy",) if opted else ()
    top = pcileech.create_parser().parse_args(
        ["build", "--bdf", "0000:03:00.0", "--board", BOARD, *extra]
    )
    assert top.ft601_legacy_phy is opted
    stage2 = build.parse_args(["--bdf", "0000:03:00.0", "--board", BOARD, *extra])
    config = build.ConfigurationManager().create_from_args(stage2)
    assert config.ft601_legacy_phy is opted

    from pcileechfwgenerator.cli.container import BuildConfig

    args = BuildConfig(bdf="0000:03:00.0", board=BOARD, ft601_legacy_phy=opted)
    assert ("--ft601-legacy-phy" in args.cmd_args()) is opted
