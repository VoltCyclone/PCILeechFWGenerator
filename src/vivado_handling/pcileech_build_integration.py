#!/usr/bin/env python3
"""
PCILeech Build Integration Module

Stage 3 (Vivado project construction) consumes the artifact set that Stage 2
finalized under ``output/src``, ``output/ip`` and ``output/constraints``, as
recorded in ``output/stage2_artifacts.json``. It never rediscovers or copies
stock board RTL, IP or constraints: a missing or inconsistent artifact is an
error, not permission to substitute the unmodified board design.
"""

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..file_management.board_discovery import BoardDiscovery, get_board_config
from ..file_management.repo_manager import RepoManager
from ..log_config import get_logger
from ..string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from ..utils.file_manifest import FileManifestTracker
from .build_evidence import build_quality_tcl, write_build_manifest
from .ip_build_contract import (
    CAPTURE_PCIE_CONTRACT_TCL,
    RESTORE_PCIE_CONTRACT_TCL,
    VERIFY_PCIE_CONTRACT_TCL,
)
from .memory_ip_contract import (
    CAPTURE_MEMORY_CONTRACT_TCL,
    RESTORE_MEMORY_CONTRACT_TCL,
)
from .requester_plan import PLAN_FILES, PLAN_TCL, REQUESTER_SOURCE

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stage 2 -> Stage 3 artifact contract
# ---------------------------------------------------------------------------

STAGE2_MANIFEST_NAME = "stage2_artifacts.json"
STAGE2_MANIFEST_VERSION = 2
DONOR_IP_OVERRIDE_NAME = "pcileech_donor_ip_overrides.tcl"
REQUIRED_HEADER = "pcileech_header.svh"
HDL_SUFFIXES = (".sv", ".svh", ".v")
CFGSPACE_COE = "pcileech_cfgspace.coe"
WRITEMASK_COE = "pcileech_cfgspace_writemask.coe"
# Initialization memories whose content is donor data. A board IP that
# references one of these must receive this run's generated copy; the stock
# file of the same name is never acceptable.
GENERATED_MEMORY_FILES = (CFGSPACE_COE, WRITEMASK_COE)
# Board implementation hooks Stage 2 may carry from the board directory, and
# the impl_1 step property each one is attached to. Only these names are
# accepted; a board without them builds with the stock implementation steps.
IMPL_HOOKS_DIR = "impl_hooks"
# A board's endpoint-scoped CDC disposition checker (board-relative source),
# staged as cdc_check/check_cdc.tcl and run on the routed checkpoint.
CDC_CHECK_DIR = "cdc_check"
CDC_CHECK_NAME = "check_cdc.tcl"
CDC_CHECK_BOARD_PATH = "sim/cdc/check_cdc.tcl"
# Content pins: a checker replaces the blanket CDC gate only when it is the
# reviewed one for that board (sim/cdc/README.md disposition). An edited or
# unknown checker is not staged, so the stricter blanket gate applies.
QUALIFIED_CDC_CHECKER_SHA256 = {
    "pcileech_100t484_x1": (
        "ec6612fbe550fee2dc5bbb1947b9b2d8adc4a2afd81031abec9fba187ad104c2"
    ),
}
IMPL_HOOK_STEPS = {
    "place_design_post.tcl": "STEPS.PLACE_DESIGN.TCL.POST",
}

_COE_REF_RE = re.compile(r"([A-Za-z0-9_.\-]+\.coe)\b")


class BuildArtifactError(RuntimeError):
    """Stage 2 artifacts are missing, incomplete or inconsistent."""


# Legacy script default, used only when no job count was selected.
_DEFAULT_RUN_JOBS_TCL = """set RUN_JOBS 8
if {[info exists ::env(VIVADO_RUN_JOBS)] && $::env(VIVADO_RUN_JOBS) > 0} {
    set RUN_JOBS $::env(VIVADO_RUN_JOBS)
}"""


def validate_vivado_jobs(value: Any) -> int:
    """Return ``value`` as a Vivado job count, rejecting non-positive counts."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Vivado job count must be a positive integer, got {value!r}")
    return value


def run_jobs_tcl(vivado_jobs: Optional[int]) -> str:
    """Tcl that sets RUN_JOBS; a selected count is fixed in the script."""
    if vivado_jobs is None:
        return _DEFAULT_RUN_JOBS_TCL
    return f"set RUN_JOBS {validate_vivado_jobs(vivado_jobs)}"


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def xci_memory_references(xci_path: Path) -> List[str]:
    """Return the COE basenames an XCI (JSON or XML) initializes from."""
    text = Path(xci_path).read_text(encoding="utf-8", errors="replace")
    names = {Path(m.replace("\\", "/")).name for m in _COE_REF_RE.findall(text)}
    return sorted(names)


def _plain_name(name: Any, field: str) -> str:
    """Reject manifest entries that are not bare file names."""
    if (
        not isinstance(name, str)
        or not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
    ):
        raise BuildArtifactError(
            safe_format(
                "Invalid {field} entry in {manifest}: {name!r}",
                field=field,
                manifest=STAGE2_MANIFEST_NAME,
                name=name,
            )
        )
    return name


def stage_generated_memories(
    output_dir: Path, memories: Mapping[str, str]
) -> Dict[str, str]:
    """Write this run's generated memories to ``src/`` and overlay ``ip/``.

    Must run after Stage 2 has staged the board IP (so the overlay replaces
    the stock COE of the same name) and before the manifest is written.
    Returns ``{name: sha256}`` of the content now present in both places.
    """
    output_dir = Path(output_dir)
    if CFGSPACE_COE not in memories or not memories[CFGSPACE_COE].strip():
        raise BuildArtifactError(
            safe_format(
                "Generation produced no {name}; refusing to build with the "
                "stock configuration-space memory",
                name=CFGSPACE_COE,
            )
        )
    src_dir = output_dir / "src"
    ip_dir = output_dir / "ip"
    if not ip_dir.is_dir() or not any(ip_dir.glob("*.xci")):
        raise BuildArtifactError(
            safe_format(
                "No staged IP definitions under {path}; Stage 2 IP staging "
                "did not run or failed",
                path=str(ip_dir),
            )
        )
    src_dir.mkdir(parents=True, exist_ok=True)
    hashes: Dict[str, str] = {}
    for name, content in memories.items():
        _plain_name(name, "generated memory")
        if not content or not content.strip():
            raise BuildArtifactError(
                safe_format("Generated memory {name} is empty", name=name)
            )
        data = content.encode("utf-8")
        expected = hashlib.sha256(data).hexdigest()
        for dest in (src_dir / name, ip_dir / name):
            try:
                dest.write_bytes(data)
            except OSError as e:
                raise BuildArtifactError(
                    safe_format(
                        "Failed to write generated memory {path}: {err}",
                        path=str(dest),
                        err=e,
                    )
                ) from e
            if sha256_file(dest) != expected:
                raise BuildArtifactError(
                    safe_format("Verification failed for {path}", path=str(dest))
                )
        hashes[name] = expected
    return hashes


def write_stage2_manifest(
    output_dir: Path,
    *,
    board: str,
    sources: Iterable[str],
    constraints: Iterable[str],
    generated_memories: Mapping[str, str],
    ip_definitions: Iterable[str],
    impl_hooks: Iterable[str] = (),
    cdc_check: bool = False,
) -> Path:
    """Record the authoritative Stage 2 artifact set, then validate it.

    Only files listed here reach the Vivado project; anything else left in
    the output tree (for example from an earlier run) is excluded.
    ``impl_hooks`` names board implementation hooks staged in
    ``output_dir/impl_hooks`` (see ``IMPL_HOOK_STEPS``).
    """
    output_dir = Path(output_dir)
    ip_dir = output_dir / "ip"
    xci = sorted({_plain_name(name, "IP definition") for name in ip_definitions})
    if not xci or any(not name.endswith(".xci") for name in xci):
        raise BuildArtifactError("Generation did not record a valid IP definition set")
    memories = set()
    for name in xci:
        memories.update(xci_memory_references(ip_dir / name))
    override = output_dir / DONOR_IP_OVERRIDE_NAME
    hooks = sorted({_plain_name(name, "implementation hook") for name in impl_hooks})
    for name in hooks:
        if name not in IMPL_HOOK_STEPS:
            raise BuildArtifactError(
                safe_format("Unknown implementation hook {name}", name=name)
            )
    manifest: dict[str, Any] = {
        "version": STAGE2_MANIFEST_VERSION,
        "board": board,
        "sources": sorted(set(sources)),
        "ip": {"xci": xci, "memories": sorted(memories)},
        "constraints": sorted(set(constraints)),
        "generated_memories": dict(sorted(generated_memories.items())),
        "donor_ip_override": {
            "name": DONOR_IP_OVERRIDE_NAME,
            "sha256": sha256_file(override) if override.is_file() else None,
        },
        "impl_hooks": hooks,
        "cdc_check": CDC_CHECK_NAME if cdc_check else None,
    }
    manifest["input_hashes"] = {}
    if REQUESTER_SOURCE in manifest["sources"]:
        manifest["requester_plan"] = {}
        for name in PLAN_FILES:
            artifact = output_dir / name
            if not artifact.is_file() or not artifact.stat().st_size:
                raise BuildArtifactError(f"Missing requester plan input: {artifact}")
            manifest["requester_plan"][name] = sha256_file(artifact)
    for directory, names in (
        ("src", manifest["sources"]),
        ("ip", xci),
        ("constraints", manifest["constraints"]),
        (IMPL_HOOKS_DIR, hooks),
        (CDC_CHECK_DIR, [CDC_CHECK_NAME] if cdc_check else []),
    ):
        for name in names:
            _plain_name(name, directory)
            artifact = output_dir / directory / name
            if not artifact.is_file():
                raise BuildArtifactError(f"Missing generated input: {artifact}")
            manifest["input_hashes"][f"{directory}/{name}"] = sha256_file(artifact)
    path = output_dir / STAGE2_MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    validate_stage2_artifacts(output_dir, board=board)
    return path


def validate_stage2_artifacts(
    output_dir: Path, board: Optional[str] = None
) -> Dict[str, Any]:
    """Load the Stage 2 manifest and verify every artifact it names.

    Raises BuildArtifactError on the first missing, empty or mismatched
    input. Never consults the board repository.
    """
    output_dir = Path(output_dir)
    path = output_dir / STAGE2_MANIFEST_NAME
    if not path.is_file():
        raise BuildArtifactError(
            safe_format(
                "Stage 2 artifact manifest not found at {path}. Run firmware "
                "generation for this board into this output directory first; "
                "Stage 3 does not fall back to stock board sources.",
                path=str(path),
            )
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BuildArtifactError(
            safe_format("Unreadable {path}: {err}", path=str(path), err=e)
        ) from e
    if not isinstance(manifest, dict) or (
        manifest.get("version") != STAGE2_MANIFEST_VERSION
    ):
        raise BuildArtifactError(
            safe_format("Unsupported manifest format in {path}", path=str(path))
        )
    if board is not None and manifest.get("board") != board:
        raise BuildArtifactError(
            safe_format(
                "Stage 2 artifacts were generated for board {have}, not {want}",
                have=manifest.get("board"),
                want=board,
            )
        )

    def _names(value: Any, field: str) -> List[str]:
        if not isinstance(value, list):
            raise BuildArtifactError(
                safe_format("Manifest field {field} must be a list", field=field)
            )
        return [_plain_name(v, field) for v in value]

    def _require(path_: Path, what: str) -> None:
        if not path_.is_file():
            raise BuildArtifactError(
                safe_format("Missing {what}: {path}", what=what, path=str(path_))
            )
        if path_.stat().st_size == 0:
            raise BuildArtifactError(
                safe_format("Empty {what}: {path}", what=what, path=str(path_))
            )

    src_dir = output_dir / "src"
    sources = _names(manifest.get("sources"), "sources")
    if REQUIRED_HEADER not in sources:
        raise BuildArtifactError(
            safe_format(
                "Generated source set lacks {hdr}; Stage 2 board source "
                "assembly is incomplete",
                hdr=REQUIRED_HEADER,
            )
        )
    if not any(n.endswith(".sv") for n in sources):
        raise BuildArtifactError("Generated source set contains no .sv files")
    for name in sources:
        if not name.endswith(HDL_SUFFIXES):
            raise BuildArtifactError(
                safe_format("Non-HDL entry in sources: {name}", name=name)
            )
        _require(src_dir / name, "generated source")

    ip_dir = output_dir / "ip"
    ip = manifest.get("ip")
    if not isinstance(ip, dict):
        raise BuildArtifactError("Manifest field ip must be an object")
    xci = _names(ip.get("xci"), "ip.xci")
    if not xci:
        raise BuildArtifactError("Stage 2 staged no IP definitions (.xci)")
    referenced = set()
    for name in xci:
        _require(ip_dir / name, "staged IP definition")
        referenced.update(xci_memory_references(ip_dir / name))
    listed = set(_names(ip.get("memories"), "ip.memories"))
    if referenced != listed:
        raise BuildArtifactError(
            safe_format(
                "Staged IP memory references changed since Stage 2: "
                "manifest={listed} actual={actual}",
                listed=sorted(listed),
                actual=sorted(referenced),
            )
        )
    for name in sorted(referenced):
        _require(ip_dir / name, "IP initialization memory")

    generated = manifest.get("generated_memories")
    if not isinstance(generated, dict):
        raise BuildArtifactError("Manifest field generated_memories must be an object")
    if CFGSPACE_COE not in generated:
        raise BuildArtifactError(
            safe_format("No generated {name} recorded", name=CFGSPACE_COE)
        )
    for name in GENERATED_MEMORY_FILES:
        if name in referenced and name not in generated:
            raise BuildArtifactError(
                safe_format(
                    "Staged IP initializes from {name} but Stage 2 generated "
                    "none; the stock board memory would be built",
                    name=name,
                )
            )
    for name, digest in generated.items():
        _plain_name(name, "generated_memories")
        for where in (src_dir / name, ip_dir / name):
            _require(where, "generated memory")
            if sha256_file(where) != digest:
                raise BuildArtifactError(
                    safe_format(
                        "{path} does not match the generated {name} "
                        "(stale or stock content)",
                        path=str(where),
                        name=name,
                    )
                )

    constraints = _names(manifest.get("constraints"), "constraints")
    if not constraints:
        raise BuildArtifactError("Stage 2 staged no constraint files")
    for name in constraints:
        if not name.endswith(".xdc"):
            raise BuildArtifactError(
                safe_format("Non-XDC entry in constraints: {name}", name=name)
            )
        _require(output_dir / "constraints" / name, "staged constraint file")

    override = manifest.get("donor_ip_override") or {}
    override_path = output_dir / DONOR_IP_OVERRIDE_NAME
    if override.get("name") != DONOR_IP_OVERRIDE_NAME or not override.get("sha256"):
        raise BuildArtifactError(
            "Stage 2 produced no donor PCIe IP override; the hard IP would "
            "keep stock identity"
        )
    _require(override_path, "donor PCIe IP override")
    if sha256_file(override_path) != override["sha256"]:
        raise BuildArtifactError(
            safe_format(
                "{path} changed since Stage 2 finalized it",
                path=str(override_path),
            )
        )
    hooks = _names(manifest.get("impl_hooks"), "impl_hooks")
    for name in hooks:
        if name not in IMPL_HOOK_STEPS:
            raise BuildArtifactError(
                safe_format("Unknown implementation hook {name}", name=name)
            )
        _require(output_dir / IMPL_HOOKS_DIR / name, "implementation hook")
    expected_paths = {
        *(f"src/{name}" for name in sources),
        *(f"ip/{name}" for name in xci),
        *(f"constraints/{name}" for name in constraints),
        *(f"{IMPL_HOOKS_DIR}/{name}" for name in hooks),
    }
    cdc_check = manifest.get("cdc_check")
    if cdc_check is not None:
        if cdc_check != CDC_CHECK_NAME:
            raise BuildArtifactError("Unknown board CDC checker")
        _require(output_dir / CDC_CHECK_DIR / CDC_CHECK_NAME, "board CDC checker")
        expected_paths.add(f"{CDC_CHECK_DIR}/{CDC_CHECK_NAME}")
    input_hashes = manifest.get("input_hashes", {})
    if REQUESTER_SOURCE in sources:
        requester = manifest.get("requester_plan")
        if not isinstance(requester, dict) or set(requester) != set(PLAN_FILES):
            raise BuildArtifactError("Incomplete requester plan inventory")
        for name, digest in requester.items():
            _require(output_dir / name, "requester plan input")
            if sha256_file(output_dir / name) != digest:
                raise BuildArtifactError(f"Changed requester plan input: {name}")
    elif manifest.get("requester_plan"):
        raise BuildArtifactError("Requester plan recorded without requester RTL")
    if set(input_hashes) != expected_paths:
        raise BuildArtifactError("Stage 2 input hash inventory is incomplete")
    for name, digest in input_hashes.items():
        if sha256_file(output_dir / name) != digest:
            raise BuildArtifactError(
                f"Stage 2 input changed after finalization: {name}"
            )
    return manifest


class PCILeechBuildIntegration:
    """Integrates pcileech-fpga repository with the build process."""

    def __init__(
        self,
        output_dir: Path,
        repo_root: Optional[Path] = None,
        manifest_tracker=None,
        logger=None,
    ):
        """
        Initialize the build integration.

        Args:
            output_dir: Output directory for build artifacts
            repo_root: Optional repository root path
            manifest_tracker: Optional file manifest tracker
            logger: Optional logger
        """
        self.output_dir = Path(output_dir)
        self.repo_root = repo_root or RepoManager.ensure_repo()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.file_manifest = manifest_tracker
        self.logger = logger

        # Cache discovered boards
        self._boards_cache: Optional[Dict[str, Dict]] = None
        self.prefix = "BUILD"

    def get_available_boards(self) -> Dict[str, Dict]:
        """
        Get all available boards from the repository.

        Returns:
            Dictionary mapping board names to configurations
        """
        if self._boards_cache is None:
            self._boards_cache = BoardDiscovery.discover_boards(self.repo_root)
        return self._boards_cache

    def prepare_build_environment(self, board_name: str) -> Dict[str, Any]:
        """
        Prepare the build environment for a specific board.

        Args:
            board_name: Name of the board to build for

        Returns:
            Dictionary containing build configuration and paths

        Raises:
            ValueError: If board is not found
        """
        # Get board configuration
        boards = self.get_available_boards()
        if board_name not in boards:
            raise ValueError(
                safe_format(
                    "Board '{board_name}' not found. Available: {available_boards}",
                    board_name=board_name,
                    available_boards=", ".join(boards.keys()),
                )
            )

        board_config = boards[board_name]
        log_info_safe(
            logger,
            "Preparing build environment for {board_name}",
            board_name=board_name,
            prefix=self.prefix,
        )

        # Stage 3 consumes only what Stage 2 finalized and recorded. Validate
        # the whole set before touching the board directory so an incomplete
        # generation can never be "completed" from stock board files.
        manifest = self._load_stage2_manifest(board_name)

        # Create board-specific output directory
        board_output_dir = self.output_dir / board_name
        board_output_dir.mkdir(parents=True, exist_ok=True)

        xdc_files = self._copy_xdc_files(board_name, board_output_dir / "constraints")
        src_files = self._copy_source_files(board_name, board_output_dir)
        ip_files = self._copy_ip_files(board_name, board_output_dir / "ip")

        # Patch XCI speed grade to match board's target FPGA part.
        # Some upstream XCI files are generated for a different speed grade
        # than the board targets, causing Vivado to lock the IP cores.
        fpga_part = board_config.get("fpga_part", "")
        if fpga_part and (board_output_dir / "ip").is_dir():
            from .ip_lock_resolver import patch_xci_speed_grade

            patch_xci_speed_grade(
                board_output_dir,
                fpga_part,
                logger=logger,
                prefix=self.prefix,
            )

        # Fail fast if no IP definition files were staged. These are required
        # for Vivado IP import/regeneration.
        if not ip_files:
            log_error_safe(
                logger,
                safe_format(
                    "Build aborted: no staged IP definition files for board "
                    "{board}. Re-run firmware generation for this board.",
                    board=board_name,
                ),
                prefix=self.prefix,
            )
            raise SystemExit(2)

        # The memories Vivado imports must still be this run's generated data
        # after every Stage 3 step that touched the board IP directory.
        self._verify_staged_memories(manifest, board_output_dir / "ip")

        return {
            "board_name": board_name,
            "board_config": board_config,
            "output_dir": board_output_dir,
            # Board templates and build scripts are not staged. build_all.tcl
            # builds the project from the artifacts above.
            "templates": {},
            "xdc_files": xdc_files,
            "src_files": src_files,
            "ip_files": ip_files,
            "build_scripts": {},
            "stage2_manifest": manifest,
        }

    def _load_stage2_manifest(self, board_name: str) -> Dict[str, Any]:
        """Validate and cache the Stage 2 manifest for ``board_name``."""
        cached = getattr(self, "_stage2_manifest", None)
        if cached is not None and cached.get("board") == board_name:
            return cached
        manifest = validate_stage2_artifacts(self.output_dir, board=board_name)
        self._stage2_manifest = manifest
        return manifest

    def _stage_artifacts(
        self, source_dir: Path, names: Iterable[str], dest_dir: Path, kind: str
    ) -> List[Path]:
        """Copy named Stage 2 artifacts into the board build tree.

        Every failure raises: a partially staged directory would silently
        build a different design. A destination that already *is* the source
        (an in-place layout) is used as-is rather than copied onto itself.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        staged: List[Path] = []
        for name in names:
            src = source_dir / name
            dest = dest_dir / name
            if not src.is_file():
                raise BuildArtifactError(
                    safe_format(
                        "Missing {kind} artifact {path}", kind=kind, path=str(src)
                    )
                )
            try:
                same = dest.exists() and os.path.samefile(src, dest)
            except OSError:
                same = False
            if not same:
                if self.file_manifest and not self.file_manifest.add_copy_operation(
                    src, dest
                ):
                    raise BuildArtifactError(
                        safe_format(
                            "Two {kind} artifacts map to {dest}",
                            kind=kind,
                            dest=str(dest),
                        )
                    )
                try:
                    shutil.copy2(src, dest)
                except OSError as e:
                    raise BuildArtifactError(
                        safe_format(
                            "Failed to stage {kind} artifact {src} -> {dest}: {err}",
                            kind=kind,
                            src=str(src),
                            dest=str(dest),
                            err=e,
                        )
                    ) from e
            # Compare the staged bytes with what Stage 2 recorded, not with the
            # source as it is now: the manifest check may predate this copy.
            recorded = (getattr(self, "_stage2_manifest", None) or {}).get(
                "input_hashes", {}
            )
            key = f"{source_dir.name}/{name}"
            if key in recorded and sha256_file(dest) != recorded[key]:
                raise BuildArtifactError(
                    safe_format(
                        "Stage 2 input changed after finalization: {name}",
                        name=key,
                    )
                )
            staged.append(dest)
            log_debug_safe(
                logger,
                safe_format("Staged {kind}: {name}", kind=kind, name=name),
                prefix=self.prefix,
            )
        log_info_safe(
            logger,
            safe_format(
                "Staged {count} {kind} artifact(s) from {src}",
                count=len(staged),
                kind=kind,
                src=str(source_dir),
            ),
            prefix=self.prefix,
        )
        return staged

    def _verify_staged_memories(self, manifest: Dict[str, Any], ip_dir: Path) -> None:
        """Require the board IP directory to hold the generated memories."""
        for name, digest in manifest["generated_memories"].items():
            path = ip_dir / name
            if not path.is_file() or sha256_file(path) != digest:
                raise BuildArtifactError(
                    safe_format(
                        "Staged IP memory {path} is not the generated {name}",
                        path=str(path),
                        name=name,
                    )
                )

    def _copy_xdc_files(self, board_name: str, output_dir: Path) -> List[Path]:
        """Stage the constraint files Stage 2 recorded in output/constraints."""
        manifest = self._load_stage2_manifest(board_name)
        return self._stage_artifacts(
            self.output_dir / "constraints",
            manifest["constraints"],
            output_dir,
            "constraint",
        )

    def _copy_source_files(self, board_name: str, output_dir: Path) -> List[Path]:
        """Stage the generated HDL source set into ``output_dir/src``.

        output/src is the complete, authoritative design: board support
        assembled by Stage 2 plus the generated overlays. There is no
        repository fallback; a missing header or empty set means generation
        was incomplete and manifest validation reports it. Files are placed
        flat in ``src/`` so build scripts see predictable include paths.
        """
        manifest = self._load_stage2_manifest(board_name)
        return self._stage_artifacts(
            self.output_dir / "src", manifest["sources"], output_dir / "src", "source"
        )

    def _copy_ip_files(self, board_name: str, output_dir: Path) -> List[Path]:
        """Stage IP definitions and initialization memories from output/ip.

        Stage 2 staged the board IP and overlaid this run's generated COEs.
        Stock board IP is never re-fetched here: that would silently replace
        donor configuration space and write masks with stock data.
        """
        manifest = self._load_stage2_manifest(board_name)
        ip = manifest["ip"]
        names = list(ip["xci"]) + list(ip["memories"])
        staged = self._stage_artifacts(self.output_dir / "ip", names, output_dir, "IP")
        self._verify_staged_memories(manifest, output_dir)
        return staged

    def create_unified_build_script(
        self,
        board_name: str,
        device_config: Optional[Dict] = None,
        vivado_jobs: Optional[int] = None,
    ) -> Path:
        """
        Create a unified build script that incorporates all necessary steps.

        Args:
            board_name: Name of the board
            device_config: Optional device-specific configuration (unused, kept for backwards compatibility)
            vivado_jobs: Selected launch_runs job count; None keeps the
                legacy default of 8 overridable by VIVADO_RUN_JOBS

        Returns:
            Path to the unified build script
        """
        jobs_tcl = run_jobs_tcl(vivado_jobs)
        # A local preflight can fail before Tcl has a chance to invalidate the
        # previous live readback. Archive it before validating this run's inputs.
        board_output = self.output_dir / board_name
        if not board_output.resolve().is_relative_to(self.output_dir.resolve()):
            raise BuildArtifactError("Board output escapes the output directory")
        resolved = board_output / "requester_plan_resolved.json"
        if resolved.exists() or resolved.is_symlink():
            resolved.rename(
                board_output / f"previous-requester-plan-{uuid.uuid4().hex}.json"
            )
        # Prepare build environment
        build_env = self.prepare_build_environment(board_name)
        # A rerun must not leave a prior successful image beside new inputs.
        # Preserve it as an explicitly historical result rather than deleting it.
        published = build_env["output_dir"] / "output"
        if published.exists():
            published.rename(published.with_name(f"previous-output-{uuid.uuid4().hex}"))

        # Create unified script
        script_path = build_env["output_dir"] / "build_all.tcl"

        board_config = build_env.get("board_config", {})
        if "fpga_part" not in board_config or not board_config["fpga_part"]:
            log_error_safe(
                logger,
                safe_format(
                    "Missing required 'fpga_part' for board '{board_name}' in unified build script generation.",
                    board_name=board_name,
                ),
                prefix=self.prefix,
            )
            raise ValueError(
                safe_format(
                    "Cannot create unified build script: missing required 'fpga_part' for board '{board_name}'.",
                    board_name=board_name,
                )
            )
        fpga_part = board_config["fpga_part"]
        project_name = safe_format("pcileech_{board_name}", board_name=board_name)

        script_content = safe_format(
            """
# PCILeech Unified Build Script for {board_name}
# Generated by PCILeechBuildIntegration

puts "Starting PCILeech build for board: {board_name}"
puts "FPGA Part: {fpga_part}"

# Change to board-specific directory for relative path resolution
cd [file dirname [info script]]
puts "Working directory: [pwd]"

# Set project parameters
set PROJECT_NAME "{project_name}"
set PROJECT_DIR "./vivado_project"
set OUTPUT_DIR "./output"
set FPGA_PART "{fpga_part}"

# Create project directory
file mkdir $PROJECT_DIR
file mkdir $OUTPUT_DIR

# Create the project from the Stage 2 artifact set only. Stock board project
# scripts add stock sources and IP, so they are never sourced here.
create_project $PROJECT_NAME $PROJECT_DIR -part $FPGA_PART -force

# Add source files
puts "Adding source files..."
""",
            board_name=board_name,
            fpga_part=fpga_part,
            project_name=project_name,
        )

        # Add source files (deduplicate exact paths to avoid true duplicates)
        script_content += "\n# Add source files\n"
        script_content += 'puts "Adding source files..."\n'

        # Track added files to avoid duplicates
        added_files = set()
        board_output_dir = build_env["output_dir"]

        for src_file in build_env["src_files"]:
            src_path = Path(src_file)
            # Use relative path from board directory
            try:
                rel_path = src_path.relative_to(board_output_dir)
            except ValueError:
                # If file is not under board_output_dir, use the filename and assume it's in src/
                rel_path = Path("src") / src_path.name

            rel_path_str = str(rel_path)
            # Skip if this file has already been added
            if rel_path_str in added_files:
                continue
            added_files.add(rel_path_str)

            script_content += f'add_files -norecurse "{rel_path}"\n'

        # Import exactly the staged IP definitions (not a directory glob, which
        # would also pick up stale XCIs). import_files copies each XCI with its
        # adjacent initialization memories into the project. Failure to add a
        # required IP is fatal: a missing BRAM/PCIe core is not a warning.
        xci_rel = []
        for ip_file in build_env["ip_files"]:
            ip_path = Path(ip_file)
            if ip_path.suffix != ".xci":
                continue
            try:
                xci_rel.append(str(ip_path.relative_to(board_output_dir)))
            except ValueError:
                xci_rel.append(str(Path("ip") / ip_path.name))
        script_content += "\n# Add staged IP cores (import to avoid locked IP issues)\n"
        script_content += 'puts "Adding IP cores..."\n'
        script_content += "set ip_files [list"
        for rel in xci_rel:
            script_content += f' [file normalize "{rel}"]'
        script_content += "]\n"
        script_content += (
            "if {[llength $ip_files] == 0} {\n"
            '    error "No staged IP definitions to import"\n'
            "}\n"
            "set fs [get_filesets sources_1]\n"
            "foreach ip_file $ip_files {\n"
            "    set ip_name [file rootname [file tail $ip_file]]\n"
            '    puts "Importing IP: $ip_name"\n'
            "    if {![file exists $ip_file]} {\n"
            '        error "Staged IP definition missing: $ip_file"\n'
            "    }\n"
            "    if {[catch {import_files -norecurse -fileset $fs $ip_file} import_err]} {\n"
            '        error "Failed to import IP $ip_name without modifying staged inputs: $import_err"\n'
            "    }\n"
            "}\n"
            "update_ip_catalog -rebuild -scan_changes\n"
        )

        memories = build_env["stage2_manifest"]["ip"]["memories"]
        script_content += "set staged_memory_names [list " + " ".join(memories) + "]\n"
        generated_memories = build_env["stage2_manifest"]["generated_memories"]
        script_content += (
            "set generated_memory_names [list "
            + " ".join(sorted(generated_memories))
            + "]\n"
        )
        script_content += CAPTURE_MEMORY_CONTRACT_TCL
        script_content += CAPTURE_PCIE_CONTRACT_TCL
        link_speed = board_config.get("pcie_link_speed")
        if link_speed is not None:
            if link_speed not in ("2.5_GT/s", "5.0_GT/s"):
                raise BuildArtifactError("Unsupported board PCIe link speed")
            script_content += (
                f'puts "Applying explicit board link-speed limit: {link_speed}"\n'
                f"dict set board_pcie_contract CONFIG.Link_Speed {{{link_speed}}}\n"
            )
        script_content += RESTORE_PCIE_CONTRACT_TCL
        script_content += RESTORE_MEMORY_CONTRACT_TCL

        # Ensure all .sv and .svh files are treated as SystemVerilog
        # CRITICAL: .svh files containing interface definitions (like IfAXIS128) must be
        # compiled as SystemVerilog, NOT as Verilog Header. Interfaces are design units
        # that need to be compiled, not just included as text.
        script_content += "\n# Set SystemVerilog file types\n"
        script_content += (
            "set sv_in_proj "
            "[get_files -of_objects [get_filesets sources_1] *.sv]\n"
            "if {[llength $sv_in_proj] > 0} {\n"
            '    puts "Setting SystemVerilog type for '
            '[llength $sv_in_proj] files"\n'
            "    set_property file_type SystemVerilog $sv_in_proj\n"
            "}\n"
            "# CRITICAL FIX: Set .svh files as SystemVerilog (not Verilog Header)\n"
            "# because pcileech_header.svh contains interface definitions (IfAXIS128, etc.)\n"
            "# which are design units that must be compiled, not just text-included.\n"
            "set svh_in_proj "
            "[get_files -of_objects [get_filesets sources_1] *.svh]\n"
            "if {[llength $svh_in_proj] > 0} {\n"
            '    puts "Setting SystemVerilog type for '
            '[llength $svh_in_proj] header files (contains interface definitions)"\n'
            "    set_property file_type SystemVerilog $svh_in_proj\n"
            "}\n"
        )

        # Set include directories for SystemVerilog header files
        script_content += "\n# Set include directories for SystemVerilog headers\n"
        script_content += (
            'puts "Setting include directories for SystemVerilog headers..."\n'
            'set src_dir [file normalize "./src"]\n'
            "if {[file exists $src_dir]} {\n"
            "    set_property include_dirs [list $src_dir] [current_fileset]\n"
            '    puts "Include directory set: $src_dir"\n'
            "} else {\n"
            '    puts "WARNING: src directory not found at $src_dir"\n'
            "}\n"
        )

        # Refresh compile order after file-type changes
        script_content += "update_compile_order -fileset sources_1\n"

        # Add constraints
        script_content += "\n# Add constraint files\n"
        script_content += 'puts "Adding constraint files..."\n'
        for xdc_file in build_env["xdc_files"]:
            xdc_path = Path(xdc_file)
            # Use relative path from board directory
            try:
                rel_path = xdc_path.relative_to(board_output_dir)
            except ValueError:
                rel_path = Path("constraints") / xdc_path.name

            cmd = f'add_files -fileset constrs_1 -norecurse "{rel_path}"\n'
            script_content += cmd

        # Apply the generated donor-ID / MSI-X override to the hard PCIe IP.
        # The generator writes pcileech_donor_ip_overrides.tcl next to the build
        # output; without this the imported stock pcie_7x_0.xci keeps Xilinx's
        # Vendor/Device IDs and advertises no MSI-X, so the donor identity never
        # reaches the hard IP. It is sourced after IP import and before the
        # pre-synthesis generate_target so the regeneration picks up the config.
        # The override is mandatory: without it the hard IP keeps stock
        # identity. Manifest validation already required it and pinned its
        # hash; stage it like every other artifact and source it unconditionally
        # so a missing file aborts Vivado instead of building stock identity.
        override_dest = self._stage_artifacts(
            self.output_dir,
            [DONOR_IP_OVERRIDE_NAME],
            board_output_dir,
            "donor IP override",
        )[0]
        manifest = build_env.get("stage2_manifest") or self._load_stage2_manifest(
            board_name
        )
        if sha256_file(override_dest) != manifest["donor_ip_override"]["sha256"]:
            raise BuildArtifactError(
                safe_format(
                    "Staged donor IP override {path} does not match Stage 2",
                    path=str(override_dest),
                )
            )
        script_content += (
            "\n# [PCILeechFWGenerator] Apply donor-ID / MSI-X override to"
            " the hard PCIe IP (required)\n"
            'puts "Applying PCILeechFWGenerator donor IP override..."\n'
            f'source "{DONOR_IP_OVERRIDE_NAME}"\n'
        )
        log_info_safe(
            logger,
            safe_format(
                "Wired donor IP override into unified build: {path}",
                path=str(override_dest),
            ),
            prefix=self.prefix,
        )

        script_content += VERIFY_PCIE_CONTRACT_TCL

        requester_inputs = []
        if REQUESTER_SOURCE in manifest["sources"]:
            requester_inputs = self._stage_artifacts(
                self.output_dir, PLAN_FILES, board_output_dir, "requester plan"
            )
            for artifact in requester_inputs:
                if sha256_file(artifact) != manifest["requester_plan"][artifact.name]:
                    raise BuildArtifactError("Requester plan changed during staging")
            script_content += f'\nsource "{PLAN_TCL}"\n' "::pcileech_requester::apply\n"

        # Board implementation hooks recorded by Stage 2 (for example the
        # CaptainDMA 100T post-placement replication of the COM TX FIFO
        # pointer registers). Staged and hash-checked like every other input,
        # then attached to their impl_1 step.
        hook_names = manifest.get("impl_hooks") or []
        hook_inputs = (
            self._stage_artifacts(
                self.output_dir / IMPL_HOOKS_DIR,
                hook_names,
                board_output_dir / IMPL_HOOKS_DIR,
                "implementation hook",
            )
            if hook_names
            else []
        )
        if hook_inputs:
            script_content += "\n# Board implementation hooks (Stage 2)\n"
        for hook in hook_inputs:
            digest = manifest["input_hashes"][f"{IMPL_HOOKS_DIR}/{hook.name}"]
            if sha256_file(hook) != digest:
                raise BuildArtifactError(
                    safe_format(
                        "Staged implementation hook {path} does not match Stage 2",
                        path=str(hook),
                    )
                )
            script_content += (
                f"set_property {IMPL_HOOK_STEPS[hook.name]} "
                f'[file normalize "{IMPL_HOOKS_DIR}/{hook.name}"] [get_runs impl_1]\n'
            )

        # Board CDC disposition checker recorded by Stage 2; run on the routed
        # checkpoint in place of the blanket "no Critical finding" gate.
        cdc_inputs = (
            self._stage_artifacts(
                self.output_dir / CDC_CHECK_DIR,
                [CDC_CHECK_NAME],
                board_output_dir / CDC_CHECK_DIR,
                "board CDC checker",
            )
            if manifest.get("cdc_check")
            else []
        )
        for checker in cdc_inputs:
            digest = manifest["input_hashes"][f"{CDC_CHECK_DIR}/{checker.name}"]
            if digest != QUALIFIED_CDC_CHECKER_SHA256.get(board_name):
                raise BuildArtifactError(
                    safe_format(
                        "Board CDC checker for {board} is not the qualified one",
                        board=board_name,
                    )
                )
            if sha256_file(checker) != digest:
                raise BuildArtifactError(
                    safe_format(
                        "Staged board CDC checker {path} does not match Stage 2",
                        path=str(checker),
                    )
                )
        if cdc_inputs:
            # Fail before synthesis, not after routing.
            script_content += (
                "\nif {![info exists ::env(XILINX_VIVADO)]} "
                '{error "XILINX_VIVADO is not set; cannot run the board CDC checker"}\n'
            )

        # Add synthesis and implementation
        script_content += safe_format(
            """
# Generate IP cores before synthesis
puts "Generating IP cores..."
generate_target all [get_ips *]
verify_memory_contract
puts "IP core generation completed."

# Configure run concurrency
{run_jobs}

# Run synthesis
puts "Running synthesis with $RUN_JOBS job(s)..."
launch_runs synth_1 -jobs $RUN_JOBS
wait_on_run synth_1

# Check synthesis results
if {{[get_property PROGRESS [get_runs synth_1]] != "100%"}} {{
    error "Synthesis failed"
}}

# Run implementation
puts "Running implementation with $RUN_JOBS job(s)..."
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE Explore [get_runs impl_1]
launch_runs impl_1 -to_step {{phys_opt_design (Post-Route)}} -jobs $RUN_JOBS
wait_on_run impl_1

# Qualify the same post-route checkpoint that will be used for the bitstream.
if {{![string match "*phys_opt_design (Post-Route) Complete*" [get_property STATUS [get_runs impl_1]]]}} {{
    error "Post-route physical optimization failed"
}}

{quality_checks}

launch_runs impl_1 -to_step write_bitstream -jobs $RUN_JOBS
wait_on_run impl_1
if {{[get_property PROGRESS [get_runs impl_1]] != "100%"}} {{
    error "Bitstream generation failed"
}}

# Copy bitstream to output directory
set BITSTREAM_DIR [get_property DIRECTORY [get_runs impl_1]]
set BITSTREAM_NAME [get_property top [current_fileset]].bit
set BITSTREAM_FILE [file join $BITSTREAM_DIR $BITSTREAM_NAME]

if {{![file exists $BITSTREAM_FILE]}} {{
    error [format "Bitstream not found at %s" $BITSTREAM_FILE]
}}

if {{[catch {{file copy -force $BITSTREAM_FILE $OUTPUT_DIR/}} copy_error]}} {{
    error [format "Failed to copy bitstream: %s" $copy_error]
}}

puts "Build completed successfully!"
puts "Bitstream location: $OUTPUT_DIR/$BITSTREAM_NAME"
""",
            quality_checks=build_quality_tcl(board_cdc_check=bool(cdc_inputs)),
            run_jobs=jobs_tcl,
        )
        script_path.write_text(script_content)
        write_build_manifest(
            board_output_dir,
            board=board_name,
            part=fpga_part,
            inputs=[
                *(Path(p) for p in build_env["src_files"]),
                *(Path(p) for p in build_env["ip_files"]),
                *(Path(p) for p in build_env["xdc_files"]),
                override_dest,
                *requester_inputs,
                *hook_inputs,
                *cdc_inputs,
                script_path,
            ],
        )

        return script_path

    def validate_board_compatibility(
        self, board_name: str, device_config: Dict
    ) -> Tuple[bool, List[str]]:
        """
        Validate if a board is compatible with the device configuration.

        Args:
            board_name: Name of the board
            device_config: Device configuration to validate against

        Returns:
            Tuple of (is_compatible, list_of_warnings)
        """
        warnings = []
        board_config = get_board_config(board_name, self.repo_root)

        # Check MSI-X support
        if device_config.get("requires_msix", False) and not board_config.get(
            "supports_msix", False
        ):
            warnings.append(
                safe_format(
                    "Board {board_name} does not support MSI-X but device requires it",
                    board_name=board_name,
                )
            )

        # Check PCIe lanes
        device_lanes = device_config.get("pcie_lanes", 1)
        board_lanes = board_config.get("max_lanes", 1)
        if device_lanes > board_lanes:
            warnings.append(
                safe_format(
                    "Device requires {device_lanes} PCIe lanes but board supports only {board_lanes}",
                    device_lanes=device_lanes,
                    board_lanes=board_lanes,
                )
            )

        # Check FPGA resources (simplified check)
        if board_config.get("fpga_family") == "7series" and device_config.get(
            "requires_ultrascale", False
        ):
            warnings.append(
                "Device requires UltraScale features but board has 7-series FPGA"
            )

        is_compatible = len(warnings) == 0
        return is_compatible, warnings


def integrate_pcileech_build(
    board_name: str,
    output_dir: Path,
    device_config: Optional[Dict] = None,
    repo_root: Optional[Path] = None,
    prefix: str = "BUILD",
    vivado_jobs: Optional[int] = None,
) -> Path:
    """
    Convenience function to integrate PCILeech build for a specific board.

    Args:
        board_name: Name of the board
        output_dir: Output directory for build artifacts
        device_config: Optional device-specific configuration
        repo_root: Optional repository root path
        vivado_jobs: Selected launch_runs job count (None keeps the default)

    Returns:
        Path to the unified build script
    """
    # Provide a manifest tracker so any cross-pass copy collisions get
    # surfaced instead of silently overwriting (defense in depth alongside
    # the Stage-B removal in `_copy_source_files`).
    integration = PCILeechBuildIntegration(
        output_dir,
        repo_root,
        manifest_tracker=FileManifestTracker(logger=logger),
    )

    # Validate compatibility if device config provided
    if device_config:
        is_compatible, warnings = integration.validate_board_compatibility(
            board_name, device_config
        )
        if warnings:
            for warning in warnings:
                log_warning_safe(logger, safe_format(warning), prefix=prefix)
        if not is_compatible:
            log_error_safe(
                logger,
                safe_format(
                    "Board {board_name} is not compatible with device configuration",
                    board_name=board_name,
                ),
                prefix=prefix,
            )

    return integration.create_unified_build_script(
        board_name, device_config, vivado_jobs=vivado_jobs
    )
