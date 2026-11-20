"""Stage 3 consumes the finalized Stage 2 artifact set.

Uses the real ``pcileech_squirrel`` board from the voltcyclone-fpga submodule
as Stage 2 input. Generated memories and the generated controller are the
stock files plus a marker comment, so they are byte-distinct from stock
without inventing any donor identity.
"""

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pcileechfwgenerator.build import FileOperationsManager, FirmwareBuilder
from pcileechfwgenerator.file_management.file_manager import FileManager
from pcileechfwgenerator.file_management.repo_manager import RepoManager
from pcileechfwgenerator.vivado_handling import pcileech_build_integration as pbi
from pcileechfwgenerator.vivado_handling.pcileech_build_integration import (
    CDC_CHECK_BOARD_PATH,
    CDC_CHECK_DIR,
    CDC_CHECK_NAME,
    CFGSPACE_COE,
    DONOR_IP_OVERRIDE_NAME,
    IMPL_HOOKS_DIR,
    STAGE2_MANIFEST_NAME,
    WRITEMASK_COE,
    BuildArtifactError,
    PCILeechBuildIntegration,
    sha256_file,
    stage_generated_memories,
    validate_stage2_artifacts,
    write_stage2_manifest,
)

BOARD = "pcileech_squirrel"
CONTROLLER = "pcileech_tlps128_bar_controller.sv"
MARKER = "GENERATED-BY-TEST-E1"

try:
    REPO = RepoManager.ensure_repo()
    BOARD_PATH = RepoManager.get_board_path(BOARD, repo_root=REPO)
    BOARD_XDC = RepoManager.get_xdc_files(BOARD, repo_root=REPO)
    _HAVE_BOARD = (BOARD_PATH / "ip" / CFGSPACE_COE).is_file()
except Exception:  # pragma: no cover - submodule absent
    REPO = BOARD_PATH = BOARD_XDC = None
    _HAVE_BOARD = False

pytestmark = pytest.mark.skipif(
    not _HAVE_BOARD, reason="voltcyclone-fpga submodule not initialized"
)


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _generated(stock: Path, comment: str) -> str:
    """A byte-distinct generated variant of a stock artifact."""
    return f"{comment} {MARKER}\n" + stock.read_text(encoding="utf-8")


def _generated_memories() -> dict:
    ip = BOARD_PATH / "ip"
    return {
        CFGSPACE_COE: _generated(ip / CFGSPACE_COE, ";"),
        WRITEMASK_COE: _generated(ip / WRITEMASK_COE, ";"),
    }


HOOK = "place_design_post.tcl"
HOOK_TEXT = "# post-placement hook staged by Stage 2\nputs hook\n"
CDC_TEXT = "# board CDC disposition checker\nputs CDC_CHECK_PASS\n"


def _stage2(
    out: Path, *, memories=None, write_override=True, hooks=(), cdc_check=False
) -> dict:
    """Assemble a Stage 2 output tree the way build.py does.

    Stock board IP is staged *first* (as the generator does), so the
    overlay has to replace stock COEs that are already present.
    """
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("src", "ip", "constraints"):
        (out / sub).mkdir(exist_ok=True)
    for f in (BOARD_PATH / "ip").iterdir():
        if f.suffix in (".xci", ".coe"):
            shutil.copy2(f, out / "ip" / f.name)
    sources = []
    for f in (BOARD_PATH / "src").iterdir():
        if f.suffix in (".sv", ".svh", ".v"):
            shutil.copy2(f, out / "src" / f.name)
            sources.append(f.name)
    (out / "src" / CONTROLLER).write_text(
        _generated(BOARD_PATH / "src" / CONTROLLER, "//"), encoding="utf-8"
    )
    for f in BOARD_XDC:
        shutil.copy2(f, out / "constraints" / f.name)
    if write_override:
        (out / DONOR_IP_OVERRIDE_NAME).write_text(
            "# donor override emitted by Stage 2\n", encoding="utf-8"
        )
    memories = _generated_memories() if memories is None else memories
    hashes = stage_generated_memories(out, memories)
    for name in hooks:
        (out / IMPL_HOOKS_DIR).mkdir(exist_ok=True)
        (out / IMPL_HOOKS_DIR / name).write_text(HOOK_TEXT, encoding="utf-8")
    if cdc_check:
        (out / CDC_CHECK_DIR).mkdir(exist_ok=True)
        (out / CDC_CHECK_DIR / CDC_CHECK_NAME).write_text(CDC_TEXT, encoding="utf-8")
    write_stage2_manifest(
        out,
        board=BOARD,
        sources=sources,
        ip_definitions=[p.name for p in (BOARD_PATH / "ip").glob("*.xci")],
        constraints=["pcileech_squirrel.xdc"],
        generated_memories=hashes,
        impl_hooks=hooks,
        cdc_check=cdc_check,
    )
    return hashes


def _forbid_stock_lookup(monkeypatch):
    """Fail if Stage 3 reaches into the board repository for design inputs."""

    def _boom(*_a, **_k):
        raise AssertionError("Stage 3 consulted the stock board repository")

    from pcileechfwgenerator.file_management.template_discovery import (
        TemplateDiscovery,
    )

    monkeypatch.setattr(TemplateDiscovery, "get_source_files", _boom)
    monkeypatch.setattr(TemplateDiscovery, "copy_board_templates", _boom)
    monkeypatch.setattr(pbi.RepoManager, "get_xdc_files", _boom)
    monkeypatch.setattr(pbi.RepoManager, "get_board_path", _boom)


@pytest.fixture
def no_stock_lookup(monkeypatch):
    _forbid_stock_lookup(monkeypatch)


def _integration(out: Path) -> PCILeechBuildIntegration:
    integ = PCILeechBuildIntegration(out, REPO)
    integ.get_available_boards()  # board metadata only, cached before guards
    return integ


# ---------------------------------------------------------------------------
# Stage 2 finalization
# ---------------------------------------------------------------------------


def test_overlay_replaces_stock_memories_already_staged(tmp_path):
    hashes = _stage2(tmp_path)
    for name in (CFGSPACE_COE, WRITEMASK_COE):
        stock = sha256_file(BOARD_PATH / "ip" / name)
        assert hashes[name] != stock
        assert sha256_file(tmp_path / "ip" / name) == hashes[name]
        assert sha256_file(tmp_path / "src" / name) == hashes[name]
    manifest = validate_stage2_artifacts(tmp_path, board=BOARD)
    assert {CFGSPACE_COE, WRITEMASK_COE} <= set(manifest["ip"]["memories"])


def test_missing_cfgspace_memory_is_rejected(tmp_path):
    with pytest.raises(BuildArtifactError, match=CFGSPACE_COE):
        _stage2(tmp_path, memories={WRITEMASK_COE: "; x\n"})


def test_ip_referencing_ungenerated_writemask_is_rejected(tmp_path):
    memories = _generated_memories()
    del memories[WRITEMASK_COE]
    with pytest.raises(BuildArtifactError, match=WRITEMASK_COE):
        _stage2(tmp_path, memories=memories)


def test_missing_override_is_rejected(tmp_path):
    with pytest.raises(BuildArtifactError, match="override"):
        _stage2(tmp_path, write_override=False)


def test_missing_header_is_rejected(tmp_path):
    _stage2(tmp_path)
    with pytest.raises(BuildArtifactError, match="pcileech_header.svh"):
        write_stage2_manifest(
            tmp_path,
            board=BOARD,
            sources=[CONTROLLER],
            ip_definitions=[p.name for p in (BOARD_PATH / "ip").glob("*.xci")],
            constraints=["pcileech_squirrel.xdc"],
            generated_memories={
                CFGSPACE_COE: sha256_file(tmp_path / "ip" / CFGSPACE_COE)
            },
        )


def test_no_staged_ip_is_rejected(tmp_path):
    (tmp_path / "ip").mkdir()
    with pytest.raises(BuildArtifactError, match="IP"):
        stage_generated_memories(tmp_path, _generated_memories())


def test_manifest_rejects_path_entries(tmp_path):
    _stage2(tmp_path)
    with pytest.raises(BuildArtifactError, match="Invalid"):
        write_stage2_manifest(
            tmp_path,
            board=BOARD,
            sources=["pcileech_header.svh", "../escape.sv"],
            ip_definitions=[p.name for p in (BOARD_PATH / "ip").glob("*.xci")],
            constraints=["pcileech_squirrel.xdc"],
            generated_memories={
                CFGSPACE_COE: sha256_file(tmp_path / "ip" / CFGSPACE_COE)
            },
        )


# ---------------------------------------------------------------------------
# Stage 3 consumption
# ---------------------------------------------------------------------------


def test_stage3_builds_generated_not_stock(tmp_path, no_stock_lookup):
    hashes = _stage2(tmp_path)
    # Leftovers from an earlier run must not reach the project.
    (tmp_path / "src" / "stale_previous_run.sv").write_text("module stale; endmodule\n")
    shutil.copy2(tmp_path / "ip" / "pcie_7x_0.xci", tmp_path / "ip" / "stale_ip.xci")

    script = _integration(tmp_path).create_unified_build_script(BOARD)
    board_dir = tmp_path / BOARD
    text = script.read_text()

    for name, digest in hashes.items():
        assert sha256_file(board_dir / "ip" / name) == digest
        assert digest != sha256_file(BOARD_PATH / "ip" / name)
    assert MARKER in (board_dir / "src" / CONTROLLER).read_text()

    assert "stale_previous_run" not in text
    assert "stale_ip" not in text
    assert not (board_dir / "src" / "stale_previous_run.sv").exists()
    assert "vivado_generate_project" not in text
    assert 'source "pcileech_donor_ip_overrides.tcl"' in text
    assert "WARNING: donor IP override script not found" not in text
    assert '[file normalize "ip/pcie_7x_0.xci"]' in text
    assert '[file normalize "ip/bram_pcie_cfgspace.xci"]' in text
    assert "glob -nocomplain -directory $ip_dir" not in text
    assert (
        'add_files -fileset constrs_1 -norecurse "constraints/pcileech_squirrel.xdc"'
        in text
    )


def test_stage3_without_manifest_does_not_fall_back(tmp_path, no_stock_lookup):
    with pytest.raises(BuildArtifactError, match="manifest not found"):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_stage3_missing_header_after_finalize(tmp_path, no_stock_lookup):
    _stage2(tmp_path)
    (tmp_path / "src" / "pcileech_header.svh").unlink()
    with pytest.raises(BuildArtifactError, match="pcileech_header.svh"):
        _integration(tmp_path).create_unified_build_script(BOARD)


@pytest.mark.parametrize("where", ["ip", "src"])
def test_stage3_rejects_stock_memory_restaged_after_finalize(
    tmp_path, no_stock_lookup, where
):
    _stage2(tmp_path)
    shutil.copy2(BOARD_PATH / "ip" / CFGSPACE_COE, tmp_path / where / CFGSPACE_COE)
    with pytest.raises(BuildArtifactError, match="stale or stock"):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_stage3_missing_memory(tmp_path, no_stock_lookup):
    _stage2(tmp_path)
    (tmp_path / "ip" / WRITEMASK_COE).unlink()
    with pytest.raises(BuildArtifactError, match=WRITEMASK_COE):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_stage3_override_missing_or_changed(tmp_path, no_stock_lookup):
    _stage2(tmp_path)
    override = tmp_path / DONOR_IP_OVERRIDE_NAME
    override.write_text("# edited after Stage 2\n")
    with pytest.raises(BuildArtifactError, match="changed since Stage 2"):
        _integration(tmp_path).create_unified_build_script(BOARD)
    override.unlink()
    with pytest.raises(BuildArtifactError, match="override"):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_stage3_rejects_other_board_artifacts(tmp_path, no_stock_lookup):
    _stage2(tmp_path)
    with pytest.raises(BuildArtifactError, match="generated for board"):
        validate_stage2_artifacts(tmp_path, board="pcileech_screamer_m2")


def test_stage3_copy_failure_propagates(tmp_path, no_stock_lookup, monkeypatch):
    _stage2(tmp_path)

    def _fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pbi.shutil, "copy2", _fail)
    with pytest.raises(BuildArtifactError, match="disk full"):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_stage_artifacts_in_place_is_not_copied(tmp_path, monkeypatch):
    (tmp_path / "a.sv").write_text("module a; endmodule\n")
    integ = PCILeechBuildIntegration(tmp_path, REPO)
    monkeypatch.setattr(
        pbi.shutil, "copy2", MagicMock(side_effect=AssertionError("copied onto itself"))
    )
    staged = integ._stage_artifacts(tmp_path, ["a.sv"], tmp_path, "source")
    assert staged == [tmp_path / "a.sv"]


# ---------------------------------------------------------------------------
# build.py: real generator-to-build ordering
# ---------------------------------------------------------------------------


def _builder(out: Path) -> FirmwareBuilder:
    b = object.__new__(FirmwareBuilder)
    b.config = SimpleNamespace(output_dir=out, board=BOARD)
    b.logger = MagicMock()
    b.file_manager = FileOperationsManager(out, parallel=False, logger=b.logger)
    return b


def test_collect_memories_prefers_rendered_writemask():
    rendered = {CFGSPACE_COE: "; c\n", WRITEMASK_COE: "; rendered\n", "x.sv": "m"}
    got = FirmwareBuilder._collect_generated_memories(
        {
            "systemverilog_modules": rendered,
            "firmware_components": {"writemask_coe": "; component\n"},
        }
    )
    assert got == {CFGSPACE_COE: "; c\n", WRITEMASK_COE: "; rendered\n"}
    got = FirmwareBuilder._collect_generated_memories(
        {
            "systemverilog_modules": {CFGSPACE_COE: "; c\n"},
            "firmware_components": {"writemask_coe": "; component\n"},
        }
    )
    assert got[WRITEMASK_COE] == "; component\n"


def test_build_sequence_clean_directory_end_to_end(tmp_path, monkeypatch):
    """Stock IP staged before modules are written still ends up generated."""
    out = tmp_path / "output"
    memories = _generated_memories()
    controller = _generated(BOARD_PATH / "src" / CONTROLLER, "//")
    # A COE left in src/ by a previous run must not survive either.
    (out / "src").mkdir(parents=True)
    shutil.copy2(BOARD_PATH / "ip" / CFGSPACE_COE, out / "src" / CFGSPACE_COE)

    builder = _builder(out)
    result = {
        "systemverilog_modules": {CONTROLLER: controller, **memories},
        "firmware_components": {},
    }

    # Stage 2, in the generator's real order: board IP is staged during
    # generation, before the build writes modules.
    fm = FileManager(out)
    result["tcl_scripts"] = {p.name: str(p) for p in fm.copy_ip_files(BOARD)}
    assert sha256_file(out / "ip" / CFGSPACE_COE) == sha256_file(
        BOARD_PATH / "ip" / CFGSPACE_COE
    )
    builder._write_modules(result)
    assert (out / "src" / CFGSPACE_COE).read_text() == memories[CFGSPACE_COE]
    builder._staged_board_files = fm.copy_pcileech_sources(
        BOARD, generated=builder._generated_sources
    )
    (out / DONOR_IP_OVERRIDE_NAME).write_text("# donor override emitted by Stage 2\n")
    builder._finalize_stage2_artifacts(result)

    _forbid_stock_lookup(monkeypatch)  # Stage 3 from here on

    manifest = validate_stage2_artifacts(out, board=BOARD)
    assert manifest["generated_memories"] == {n: _sha(c) for n, c in memories.items()}
    assert CONTROLLER in manifest["sources"]
    assert MARKER in (out / "src" / CONTROLLER).read_text()

    _integration(out).create_unified_build_script(BOARD)
    for name, content in memories.items():
        assert (out / BOARD / "ip" / name).read_text() == content
    assert MARKER in (out / BOARD / "src" / CONTROLLER).read_text()
    assert (out / STAGE2_MANIFEST_NAME).is_file()


def test_build_without_generated_memories_fails(tmp_path):
    out = tmp_path / "output"
    builder = _builder(out)
    builder._write_modules({"systemverilog_modules": {}})
    (out / "ip").mkdir(parents=True)
    shutil.copy2(BOARD_PATH / "ip" / "bram_pcie_cfgspace.xci", out / "ip")
    with pytest.raises(BuildArtifactError, match=CFGSPACE_COE):
        builder._finalize_stage2_artifacts({"systemverilog_modules": {}})


def test_override_failure_is_fatal(tmp_path, monkeypatch):
    from pcileechfwgenerator.vivado_handling import (
        fifo_donor_patcher,
        pcie_ip_donor_override,
    )

    builder = _builder(tmp_path)
    monkeypatch.setattr(
        fifo_donor_patcher, "donor_ids_from_template_context", lambda _ctx: object()
    )
    monkeypatch.setattr(fifo_donor_patcher, "apply_fifo_donor_patch", lambda *_a: {})
    monkeypatch.setattr(
        pcie_ip_donor_override, "donor_pcie_ip_config_from_result", lambda _r: None
    )

    def _fail(*_a, **_k):
        raise pcie_ip_donor_override.PcieIpOverrideError("cannot wire")

    monkeypatch.setattr(pcie_ip_donor_override, "apply_pcie_ip_donor_override", _fail)
    with pytest.raises(pcie_ip_donor_override.PcieIpOverrideError):
        builder._patch_fifo_with_donor_ids({"template_context": {}})


@pytest.mark.parametrize(
    "relative",
    [
        "src/pcileech_tlps128_bar_controller.sv",
        "ip/pcie_7x_0.xci",
        "constraints/pcileech_squirrel.xdc",
    ],
)
def test_stage2_rejects_input_changes_before_stage3(tmp_path, relative):
    _stage2(tmp_path)
    path = tmp_path / relative
    path.write_text(path.read_text() + "\n")
    with pytest.raises(BuildArtifactError, match="input changed"):
        _integration(tmp_path).create_unified_build_script(BOARD)


# ---------------------------------------------------------------------------
# Board implementation hooks
# ---------------------------------------------------------------------------

HOOK_PROPERTY = (
    "set_property STEPS.PLACE_DESIGN.TCL.POST "
    '[file normalize "impl_hooks/place_design_post.tcl"] [get_runs impl_1]'
)


def test_stage3_attaches_recorded_impl_hook(tmp_path, no_stock_lookup):
    _stage2(tmp_path, hooks=[HOOK])
    manifest = validate_stage2_artifacts(tmp_path, board=BOARD)
    assert manifest["impl_hooks"] == [HOOK]
    assert f"{IMPL_HOOKS_DIR}/{HOOK}" in manifest["input_hashes"]

    text = _integration(tmp_path).create_unified_build_script(BOARD).read_text()
    staged = tmp_path / BOARD / IMPL_HOOKS_DIR / HOOK
    assert staged.read_text() == HOOK_TEXT
    assert HOOK_PROPERTY in text
    # Attached before the implementation run is launched.
    assert text.index(HOOK_PROPERTY) < text.index("launch_runs impl_1")


def test_stage3_without_hook_sets_no_step(tmp_path, no_stock_lookup):
    _stage2(tmp_path)
    assert validate_stage2_artifacts(tmp_path, board=BOARD)["impl_hooks"] == []
    text = _integration(tmp_path).create_unified_build_script(BOARD).read_text()
    assert "STEPS.PLACE_DESIGN.TCL.POST" not in text
    assert not (tmp_path / BOARD / IMPL_HOOKS_DIR).exists()


def test_impl_hook_changed_or_missing_after_stage2(tmp_path, no_stock_lookup):
    _stage2(tmp_path, hooks=[HOOK])
    hook = tmp_path / IMPL_HOOKS_DIR / HOOK
    hook.write_text(HOOK_TEXT + "phys_opt_design\n")
    with pytest.raises(BuildArtifactError, match="input changed"):
        _integration(tmp_path).create_unified_build_script(BOARD)
    hook.unlink()
    with pytest.raises(BuildArtifactError, match="implementation hook"):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_unknown_impl_hook_is_rejected(tmp_path):
    with pytest.raises(BuildArtifactError, match="Unknown implementation hook"):
        _stage2(tmp_path, hooks=["route_design_post.tcl"])


def test_manifest_listing_unknown_hook_is_rejected(tmp_path):
    import json

    _stage2(tmp_path)
    path = tmp_path / STAGE2_MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["impl_hooks"] = ["evil.tcl"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(BuildArtifactError, match="Unknown implementation hook"):
        validate_stage2_artifacts(tmp_path, board=BOARD)


def test_version1_manifest_is_rejected(tmp_path):
    import json

    _stage2(tmp_path)
    path = tmp_path / STAGE2_MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["version"] = 1
    del manifest["impl_hooks"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(BuildArtifactError, match="Unsupported manifest format"):
        validate_stage2_artifacts(tmp_path, board=BOARD)


def test_stage2_copies_board_hook_into_manifest(tmp_path, monkeypatch):
    """A board that ships place_design_post.tcl reaches the Stage 3 script."""
    board_copy = tmp_path / "board"
    shutil.copytree(BOARD_PATH, board_copy)
    (board_copy / HOOK).write_text(HOOK_TEXT)
    (board_copy / "other_post.tcl").write_text("# not whitelisted\n")
    monkeypatch.setattr(RepoManager, "get_board_path", lambda *_a, **_k: board_copy)
    out = tmp_path / "output"
    builder = _builder(out)
    result = {
        "systemverilog_modules": {
            CONTROLLER: _generated(BOARD_PATH / "src" / CONTROLLER, "//"),
            **_generated_memories(),
        },
        "firmware_components": {},
    }
    fm = FileManager(out)
    result["tcl_scripts"] = {p.name: str(p) for p in fm.copy_ip_files(BOARD)}
    builder._write_modules(result)
    builder._staged_board_files = fm.copy_pcileech_sources(
        BOARD, generated=builder._generated_sources
    )
    assert builder._staged_board_files["impl_hooks"] == [
        str(out / IMPL_HOOKS_DIR / HOOK)
    ]
    assert not (out / IMPL_HOOKS_DIR / "other_post.tcl").exists()
    (out / DONOR_IP_OVERRIDE_NAME).write_text("# donor override emitted by Stage 2\n")
    builder._finalize_stage2_artifacts(result)

    _forbid_stock_lookup(monkeypatch)
    assert validate_stage2_artifacts(out, board=BOARD)["impl_hooks"] == [HOOK]
    text = _integration(out).create_unified_build_script(BOARD).read_text()
    assert HOOK_PROPERTY in text


# ---------------------------------------------------------------------------
# Board CDC disposition checker
# ---------------------------------------------------------------------------

BLANKET_CDC = "Unqualified critical CDC findings"


@pytest.fixture
def pinned_cdc(monkeypatch):
    """Qualify the test checker text for the test board."""
    monkeypatch.setitem(pbi.QUALIFIED_CDC_CHECKER_SHA256, BOARD, _sha(CDC_TEXT))


def test_stage3_runs_recorded_board_cdc_checker(tmp_path, no_stock_lookup, pinned_cdc):
    _stage2(tmp_path, cdc_check=True)
    manifest = validate_stage2_artifacts(tmp_path, board=BOARD)
    assert manifest["cdc_check"] == CDC_CHECK_NAME
    assert f"{CDC_CHECK_DIR}/{CDC_CHECK_NAME}" in manifest["input_hashes"]

    text = _integration(tmp_path).create_unified_build_script(BOARD).read_text()
    assert (tmp_path / BOARD / CDC_CHECK_DIR / CDC_CHECK_NAME).read_text() == CDC_TEXT
    assert "cdc_check/check_cdc.tcl" in text and "^CDC_CHECK_PASS$" in text
    assert BLANKET_CDC not in text
    # Checked on the routed design, before the bitstream is written.
    assert text.index("routed_cdc.dcp") < text.index("-to_step write_bitstream")
    # XILINX_VIVADO is checked before synthesis starts.
    assert text.index("XILINX_VIVADO is not set") < text.index("launch_runs synth_1")
    inputs = json.loads((tmp_path / BOARD / "build_inputs.json").read_text())
    assert f"{CDC_CHECK_DIR}/{CDC_CHECK_NAME}" in {
        a["path"] for a in inputs["artifacts"]
    }


def test_stage3_without_board_checker_keeps_blanket_gate(tmp_path, no_stock_lookup):
    _stage2(tmp_path)
    assert validate_stage2_artifacts(tmp_path, board=BOARD)["cdc_check"] is None
    text = _integration(tmp_path).create_unified_build_script(BOARD).read_text()
    assert BLANKET_CDC in text and "routed_cdc.dcp" not in text


def test_board_cdc_checker_changed_or_missing_after_stage2(
    tmp_path, no_stock_lookup, pinned_cdc
):
    _stage2(tmp_path, cdc_check=True)
    checker = tmp_path / CDC_CHECK_DIR / CDC_CHECK_NAME
    checker.write_text("puts CDC_CHECK_PASS\nexit 0\n")
    with pytest.raises(BuildArtifactError, match="input changed"):
        _integration(tmp_path).create_unified_build_script(BOARD)
    checker.unlink()
    with pytest.raises(BuildArtifactError, match="board CDC checker"):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_manifest_naming_other_cdc_checker_is_rejected(tmp_path):
    _stage2(tmp_path, cdc_check=True)
    path = tmp_path / STAGE2_MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["cdc_check"] = "evil.tcl"
    path.write_text(json.dumps(manifest))
    with pytest.raises(BuildArtifactError, match="Unknown board CDC checker"):
        validate_stage2_artifacts(tmp_path, board=BOARD)


def test_shipped_100t_checker_is_copied(tmp_path):
    board = RepoManager.get_board_path("pcileech_100t484_x1", repo_root=REPO)
    out = tmp_path / "out"
    copied = FileManager(out).copy_pcileech_sources("pcileech_100t484_x1")
    staged = out / CDC_CHECK_DIR / CDC_CHECK_NAME
    assert copied["cdc_check"] == [str(staged)]
    assert staged.read_bytes() == (board / CDC_CHECK_BOARD_PATH).read_bytes()


def test_cdc_checker_pin_matches_shipped_board():
    board = RepoManager.get_board_path("pcileech_100t484_x1", repo_root=REPO)
    data = (board / CDC_CHECK_BOARD_PATH).read_bytes()
    assert hashlib.sha256(data).hexdigest() == (
        pbi.QUALIFIED_CDC_CHECKER_SHA256["pcileech_100t484_x1"]
    )


def test_unpinned_checker_recorded_by_stage2_is_refused(tmp_path, no_stock_lookup):
    """A checker that is not the qualified one cannot replace the blanket gate."""
    _stage2(tmp_path, cdc_check=True)
    with pytest.raises(BuildArtifactError, match="not the qualified one"):
        _integration(tmp_path).create_unified_build_script(BOARD)


def test_edited_board_checker_is_not_staged(tmp_path, monkeypatch):
    src = RepoManager.get_board_path("pcileech_100t484_x1", repo_root=REPO)
    board_copy = tmp_path / "board"
    shutil.copytree(src, board_copy)
    checker = board_copy / CDC_CHECK_BOARD_PATH
    checker.write_text('puts "CDC_CHECK_PASS"\n')
    monkeypatch.setattr(RepoManager, "get_board_path", lambda *_a, **_k: board_copy)
    out = tmp_path / "out"
    copied = FileManager(out).copy_pcileech_sources("pcileech_100t484_x1")
    assert copied["cdc_check"] == []
    assert not (out / CDC_CHECK_DIR).exists()


def test_source_changed_after_validation_is_refused(tmp_path, no_stock_lookup):
    """staged bytes are checked against Stage 2, not re-hashed."""
    _stage2(tmp_path)
    integration = _integration(tmp_path)
    integration._load_stage2_manifest(BOARD)  # validated and cached
    (tmp_path / "src" / CONTROLLER).write_text("module swapped; endmodule\n")
    with pytest.raises(BuildArtifactError, match="changed after finalization"):
        integration.create_unified_build_script(BOARD)
