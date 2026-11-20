"""Versioned donor captures shared by host collection and build consumers.

A capture is immutable. Publish learning, then MSI-X, then context last;
context is the commit point, and exclusive links never replace old data.
An unreferenced learning file is not part of a capture and is never loaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import UUID, uuid4

from pcileechfwgenerator.exceptions import ConfigurationError
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

if TYPE_CHECKING:
    # device_clone's package initializer imports snapshot consumers.
    from pcileechfwgenerator.device_clone.register_evidence import (
        LearningManifest,
        LearningStatus,
    )

CONTEXT_NAME = "device_context.json"
MSIX_NAME = "msix_data.json"
LEARNING_NAME: Final = "register_learning.json"
SYSFS_PCI_ROOT = Path("/sys/bus/pci/devices")
# Real captures are a few KiB; the cap bounds memory use on hostile input.
MAX_CAPTURE_FILE_BYTES = 1 << 20
MAX_LEARNING_FILE_BYTES = 16 << 20
MAX_LEARNING_OBSERVATIONS = 100_000

# How a capture used in a build was established: read via VFIO this run,
# matched against live sysfs this run, or neither (offline input, or a reused
# capture whose donor is absent). Only "unverified" builds need the risk acceptance.
CaptureTrust = Literal["live", "verified", "unverified"]


class SnapshotError(ConfigurationError):
    """A saved donor capture cannot safely be used by the requested build."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Donor capture rejected: {reason}. "
            "Collect the requested donor into a new --datastore directory; "
            "existing captures are never overwritten."
        )


def normalize_bdf(value: str) -> str:
    """Normalize a PCI address, accepting an omitted zero domain."""
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[01][0-9a-fA-F]\.[0-7]", value
    ):
        raise SnapshotError("invalid PCI address")
    return (value if value.count(":") == 2 else "0000:" + value).lower()


def device_identity(config: bytes) -> dict[str, int]:
    """Extract stable identification fields, excluding mutable PCI registers.

    Subsystem IDs exist only in type-0 headers; in a type-1 bridge header the
    same bytes are the mutable prefetchable-base upper bits.
    """
    if len(config) < 64:
        raise SnapshotError("config space is shorter than the PCI header")
    identity = {
        "vendor_id": int.from_bytes(config[0:2], "little"),
        "device_id": int.from_bytes(config[2:4], "little"),
        "revision_id": config[8],
        "class_code": int.from_bytes(config[9:12], "little"),
    }
    if config[0x0E] & 0x7F == 0:
        identity["subsystem_vendor_id"] = int.from_bytes(config[44:46], "little")
        identity["subsystem_device_id"] = int.from_bytes(config[46:48], "little")
    return identity


class CaptureIdentity(BaseModel):
    """Identity and integrity fields present in both files of schema version 1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    capture_id: UUID
    collected_at: AwareDatetime
    bdf: str
    vendor_id: int = Field(strict=True, ge=0, le=0xFFFF)
    device_id: int = Field(strict=True, ge=0, le=0xFFFF)
    revision_id: int = Field(strict=True, ge=0, le=0xFF)
    class_code: int = Field(strict=True, ge=0, le=0xFFFFFF)
    subsystem_vendor_id: int | None = Field(default=None, strict=True, ge=0, le=0xFFFF)
    subsystem_device_id: int | None = Field(default=None, strict=True, ge=0, le=0xFFFF)
    config_space_size: int = Field(strict=True, ge=64, le=4096)
    config_space_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("bdf")
    @classmethod
    def canonical_bdf(cls, value: str) -> str:
        return normalize_bdf(value)


@dataclass(frozen=True)
class DonorSnapshot:
    """A validated context, MSI-X sidecar and optional learning evidence."""

    identity: CaptureIdentity
    context: dict[str, Any]
    msix: dict[str, Any]
    config_space: bytes
    learning: LearningManifest | None = None
    # Keep the original encoding so an offline import preserves the checksum.
    _learning_json: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def learning_status(self) -> LearningStatus:
        """Legacy captures have absent evidence, not an invented manifest."""
        return "absent" if self.learning is None else self.learning.status


class _LearningReference(BaseModel):
    """Bounded context reference; all fields are checked against the artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: int
    file: Literal["register_learning.json"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(gt=0, le=MAX_LEARNING_FILE_BYTES)
    status: str
    observation_count: int = Field(ge=0, le=MAX_LEARNING_OBSERVATIONS)
    experiment_count: int = Field(ge=0)
    classification_count: int = Field(ge=0)

    @field_validator("schema_version")
    @classmethod
    def supported_schema(cls, value: int) -> int:
        from pcileechfwgenerator.device_clone.register_evidence import (
            LEARNING_SCHEMA_VERSION,
        )

        if value != LEARNING_SCHEMA_VERSION:
            raise ValueError("unsupported learning schema")
        return value


def _learning_reference(
    learning: LearningManifest, document: bytes
) -> _LearningReference:
    try:
        return _LearningReference(
            schema_version=learning.schema_version,
            file=LEARNING_NAME,
            sha256=hashlib.sha256(document).hexdigest(),
            byte_size=len(document),
            status=learning.status,
            observation_count=len(learning.observations),
            experiment_count=len(learning.experiments),
            classification_count=len(learning.classifications),
        )
    except ValidationError:
        raise SnapshotError(
            "invalid or oversized register learning reference"
        ) from None


def _validate_learning(
    document: bytes, identity: CaptureIdentity, config: bytes
) -> LearningManifest:
    from pcileechfwgenerator.device_clone.register_evidence import LearningManifest

    if len(document) > MAX_LEARNING_FILE_BYTES:
        raise SnapshotError("register learning file is too large")
    try:
        # Bound the event count before allocating Pydantic observation models.
        raw = json.loads(document)
        if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int:
            raise ValueError("invalid learning schema")
        observations = raw.get("observations", [])
        if not isinstance(observations, list):
            raise ValueError("invalid observations")
        if len(observations) > MAX_LEARNING_OBSERVATIONS:
            raise SnapshotError("register learning observation count is too large")
        del raw, observations
        learning = LearningManifest.model_validate_json(document)
    except (ValueError, TypeError, RecursionError):
        # Never expose Pydantic input values through CLI root-cause formatting.
        raise SnapshotError("invalid register learning schema or JSON") from None
    if learning.context.capture_id != identity.capture_id:
        raise SnapshotError("register learning belongs to a different capture")
    if normalize_bdf(learning.context.bdf) != identity.bdf:
        raise SnapshotError("register learning PCI address conflicts with capture")
    expected: dict[str, int | None] = dict(device_identity(config))
    for key in ("subsystem_vendor_id", "subsystem_device_id"):
        expected.setdefault(key, None)
    if any(
        getattr(learning.context.donor, key) != value for key, value in expected.items()
    ):
        raise SnapshotError("register learning donor identity conflicts with capture")
    return learning


def _read_learning(
    context_path: Path,
    context: dict[str, Any],
    identity: CaptureIdentity,
    config: bytes,
) -> tuple[LearningManifest | None, bytes | None]:
    if "register_learning" not in context:
        return None, None
    try:
        reference = _LearningReference.model_validate(context["register_learning"])
    except ValidationError:
        raise SnapshotError("invalid register learning reference") from None
    path = context_path.with_name(reference.file)
    try:
        # Check before open as well as on the descriptor; never follow symlinks,
        # block on a FIFO, or trust a size check followed by an unbounded read.
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotError("register learning file is not a regular file")
        if info.st_size > MAX_LEARNING_FILE_BYTES:
            raise SnapshotError("register learning file is too large")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SnapshotError("register learning file is not a regular file")
            if info.st_size > MAX_LEARNING_FILE_BYTES:
                raise SnapshotError("register learning file is too large")
            document = stream.read(MAX_LEARNING_FILE_BYTES + 1)
    except OSError:
        raise SnapshotError("missing or unreadable register learning file") from None
    if len(document) > MAX_LEARNING_FILE_BYTES:
        raise SnapshotError("register learning file is too large")
    if len(document) != reference.byte_size:
        raise SnapshotError("register learning byte size mismatch")
    if hashlib.sha256(document).hexdigest() != reference.sha256:
        raise SnapshotError("register learning checksum mismatch")
    learning = _validate_learning(document, identity, config)
    if _learning_reference(learning, document) != reference:
        raise SnapshotError("register learning metadata or counts mismatch")
    return learning, document


_HEX_FIELD = re.compile(r"0x[0-9a-fA-F]+")


def _validate_payload(
    payload: dict[str, Any], identity: CaptureIdentity, config: bytes
) -> None:
    """Reject conflicting copies of identity/config in supported payload shapes."""
    if "bdf" in payload and normalize_bdf(payload["bdf"]) != identity.bdf:
        raise SnapshotError("payload PCI address conflicts with capture identity")
    if "config_space_hex" in payload:
        try:
            config_copy = bytes.fromhex(payload["config_space_hex"])
        except (ValueError, TypeError):
            raise SnapshotError("malformed config space hex") from None
        if config_copy != config:
            raise SnapshotError("conflicting config space copies")
    for key, expected in device_identity(config).items():
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                # Decimal strings would be silently misread as hex.
                if not _HEX_FIELD.fullmatch(value):
                    raise SnapshotError(f"invalid {key}")
                value = int(value, 16)
            if type(value) is not int or value != expected:
                raise SnapshotError(f"{key} conflicts with captured config space")
    # device_info is derived (e.g. substituted subsystem IDs), not identity.
    for key in ("template_context", "device_config"):
        if key in payload:
            if not isinstance(payload[key], dict):
                raise SnapshotError(f"invalid {key}")
            _validate_payload(payload[key], identity, config)


def _read_capture(path: Path) -> tuple[dict[str, Any], CaptureIdentity, bytes]:
    if path.is_symlink():
        raise SnapshotError(f"{path.name} is a symlink")
    try:
        too_large = path.stat().st_size > MAX_CAPTURE_FILE_BYTES
    except OSError:
        raise SnapshotError(f"missing or unreadable {path.name}") from None
    if too_large:
        raise SnapshotError(f"{path.name} is too large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SnapshotError(f"missing or unreadable {path.name}") from None
    if not isinstance(payload, dict) or "capture" not in payload:
        raise SnapshotError(f"legacy or unidentified {path.name}")
    try:
        identity = CaptureIdentity.model_validate(payload["capture"])
        config = bytes.fromhex(payload["config_space_hex"])
    except (ValidationError, ValueError, TypeError, KeyError):
        # CLI root-cause formatting must retain recovery instructions and must
        # not print Pydantic's input values (which contain donor data).
        raise SnapshotError(
            f"invalid capture metadata or config in {path.name}"
        ) from None
    if len(config) != identity.config_space_size:
        raise SnapshotError(f"config space length mismatch in {path.name}")
    if hashlib.sha256(config).hexdigest() != identity.config_space_sha256:
        raise SnapshotError(f"config space checksum mismatch in {path.name}")
    expected: dict[str, int | None] = dict(device_identity(config))
    for key in ("subsystem_vendor_id", "subsystem_device_id"):
        expected.setdefault(key, None)
    for key, value in expected.items():
        if getattr(identity, key) != value:
            raise SnapshotError(f"capture {key} conflicts with config space")
    _validate_payload(payload, identity, config)
    return payload, identity, config


def load_snapshot(
    context_path: Path, msix_path: Path, expected_bdf: str
) -> DonorSnapshot:
    """Load a capture and every referenced artifact, including optional learning.

    Schema-v1 captures predating learning remain valid. Unreferenced learning
    files are ignored, even if corrupt; they are not trusted capture inputs.
    """
    context, identity, config = _read_capture(context_path)
    if identity.bdf != normalize_bdf(expected_bdf):
        raise SnapshotError("capture PCI address does not match the requested donor")
    msix, msix_identity, msix_config = _read_capture(msix_path)
    if msix_identity != identity or msix_config != config:
        raise SnapshotError(
            "device context and MSI-X sidecar belong to different captures"
        )
    if not isinstance(msix.get("msix_info"), dict):
        raise SnapshotError("invalid MSI-X sidecar")
    _check_msix_matches_config(msix["msix_info"], config)
    learning, document = _read_learning(context_path, context, identity, config)
    return DonorSnapshot(identity, context, msix, config, learning, document)


def _check_msix_matches_config(msix_info: dict[str, Any], config: bytes) -> None:
    """Reject sidecar MSI-X layout that the checksummed config does not encode.

    The SHA pins only the config bytes, so a hand-edited ``msix_info`` would
    otherwise reach live MSI-X injection. Re-derive with the collectors' own
    parser (not the offline-only strict walker, which rejects captures live
    collection legitimately writes) so every genuine sidecar still matches.
    ``{}`` is what collectors write on parse failure and is safe: no MSI-X
    data is preloaded from it.
    """
    if not msix_info:
        return
    from pcileechfwgenerator.device_clone.msix_capability import (
        parse_msix_capability,
    )

    expected = parse_msix_capability(config.hex())
    present = bool(expected["table_size"])
    for name, value in expected.items():
        if name not in msix_info and not (present or value):
            continue  # partial "absent" dicts are accepted, as offline does
        actual = msix_info.get(name)
        # Exact types: 1 == True and 4096.0 == 4096 would otherwise pass and
        # reach SV rendering / hex formatting as the wrong type.
        if type(actual) is not type(value) or actual != value:
            raise SnapshotError(
                f"MSI-X {name} in the sidecar conflicts with the captured config "
                "space; recollect the donor"
            )


def load_configured_snapshot(
    expected_bdf: str, *, include_msix_only: bool = False
) -> DonorSnapshot | None:
    """Resolve configured paths; explicit missing files never mean live fallback.

    Only MSI-X consumers use MSIX_DATA_PATH alone. Local builds intentionally
    use that sidecar while collecting their full template context live.
    """
    context_env = os.environ.get("DEVICE_CONTEXT_PATH")
    msix_env = os.environ.get("MSIX_DATA_PATH")
    if context_env:
        context = Path(context_env)
        msix = Path(msix_env) if msix_env else context.with_name(MSIX_NAME)
    elif msix_env and include_msix_only:
        msix = Path(msix_env)
        context = msix.with_name(CONTEXT_NAME)
    else:
        context = Path("/app/output") / CONTEXT_NAME
        msix = context.with_name(MSIX_NAME)
        if not (
            context.exists()
            or context.is_symlink()
            or msix.exists()
            or msix.is_symlink()
        ):
            return None
    return load_snapshot(context, msix, expected_bdf)


def verify_current_device(snapshot: DonorSnapshot) -> Literal["verified", "absent"]:
    """Check a reused host capture against a read-only live PCI header.

    Returns "absent" when no device exists at the captured address; callers
    decide whether that permits an unverified build. A different device at
    the address, or an unreadable header, is always an error.
    """
    path = SYSFS_PCI_ROOT / snapshot.identity.bdf / "config"
    try:
        with path.open("rb") as stream:
            config = stream.read(64)
    except FileNotFoundError:
        return "absent"
    except OSError:
        raise SnapshotError("cannot verify the current donor PCI identity") from None
    if device_identity(config) != device_identity(snapshot.config_space):
        raise SnapshotError("the device at this PCI address has changed identity")
    return "verified"


def ensure_capture_destination(directory: Path) -> None:
    """Refuse occupied or partially written capture destinations before probing."""
    for name in (CONTEXT_NAME, MSIX_NAME, LEARNING_NAME):
        path = directory / name
        if path.exists() or path.is_symlink():
            raise SnapshotError(f"destination already contains {name}")


def write_snapshot(
    directory: Path,
    bdf: str,
    config: bytes,
    msix_info: dict[str, Any],
    context: dict[str, Any] | None = None,
    *,
    learning: LearningManifest | None = None,
    capture_id: UUID | None = None,
) -> DonorSnapshot:
    """Publish a complete capture without replacing any existing destination.

    Supply the ID allocated for the learning session as ``capture_id``. Without
    it a fresh ID is generated, retaining the legacy writer behavior. Learning
    must match that ID, BDF and the config header's donor identity. Its reference
    is generated here, never accepted from the caller's context dictionary.

    All temporary files are serialized and fsynced before publication. A crash
    between links leaves orphan sidecars, not a valid capture. Concurrent
    collectors cannot replace one another's files.
    """
    ensure_capture_destination(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        identity = CaptureIdentity(
            schema_version=1,
            capture_id=uuid4() if capture_id is None else capture_id,
            collected_at=datetime.now(timezone.utc),
            bdf=normalize_bdf(bdf),
            config_space_size=len(config),
            config_space_sha256=hashlib.sha256(config).hexdigest(),
            **device_identity(config),
        )
    except ValidationError:
        raise SnapshotError("invalid collected config space") from None
    metadata = identity.model_dump(mode="json", exclude_none=True)
    payload = dict(context or {})
    if "register_learning" in payload:
        raise SnapshotError("supply register learning through the learning argument")
    document = None
    if learning is not None:
        from pcileechfwgenerator.device_clone.register_evidence import LearningManifest

        if not isinstance(learning, LearningManifest):
            raise SnapshotError("invalid collected register learning")
        if len(learning.observations) > MAX_LEARNING_OBSERVATIONS:
            raise SnapshotError("register learning observation count is too large")
        document = learning.model_dump_json().encode("utf-8")
        learning = _validate_learning(document, identity, config)
        payload["register_learning"] = _learning_reference(
            learning, document
        ).model_dump()
    from .host_binding import HOST_BINDING_KEY, binding_record

    # Always this host's binding, never one supplied by the caller.
    payload.pop(HOST_BINDING_KEY, None)
    binding = binding_record()
    if binding is not None:
        payload[HOST_BINDING_KEY] = binding
    _validate_payload(payload, identity, config)
    common = {
        "capture": metadata,
        "bdf": identity.bdf,
        "config_space_hex": config.hex(),
    }
    payload.update(common)
    payload.update(device_identity(config))
    sidecar = {**common, "msix_info": msix_info}
    if not isinstance(msix_info, dict):
        raise SnapshotError("invalid collected MSI-X data")
    snapshot = DonorSnapshot(identity, payload, sidecar, config, learning, document)
    # Offline-only checks run when a capture is loaded for offline use, never
    # here: a live collect must not reject what the live build would accept.
    publish_snapshot(directory, snapshot)
    return snapshot


def publish_snapshot(directory: Path, snapshot: DonorSnapshot) -> None:
    """Exclusively publish a validated capture, retaining sidecar bytes and identity."""
    ensure_capture_destination(directory)
    directory.mkdir(parents=True, exist_ok=True)
    documents: list[bytes] = []
    names = []
    if snapshot.learning is not None:
        document = snapshot._learning_json
        if document is None:
            document = snapshot.learning.model_dump_json().encode("utf-8")
        learning = _validate_learning(
            document, snapshot.identity, snapshot.config_space
        )
        if learning != snapshot.learning:
            raise SnapshotError("register learning conflicts with serialized evidence")
        try:
            reference = _LearningReference.model_validate(
                snapshot.context.get("register_learning")
            )
        except ValidationError:
            raise SnapshotError("invalid register learning reference") from None
        if reference != _learning_reference(learning, document):
            raise SnapshotError("register learning reference conflicts with evidence")
        documents.append(document)
        names.append(LEARNING_NAME)
    elif "register_learning" in snapshot.context:
        raise SnapshotError("register learning reference has no evidence")
    try:
        documents.extend(
            json.dumps(data, indent=2).encode("utf-8")
            for data in (snapshot.msix, snapshot.context)
        )
    except (TypeError, ValueError):
        raise SnapshotError("capture context is not JSON-serialisable") from None
    names.extend((MSIX_NAME, CONTEXT_NAME))
    temporary: list[Path] = []
    try:
        for document in documents:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=".capture-",
                delete=False,
            ) as stream:
                temporary.append(Path(stream.name))
                stream.write(document)
                stream.flush()
                # The Stage 2 container must be able to read the host capture.
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
        for temp, name in zip(temporary, names):
            os.link(temp, directory / name)
            # Persist sidecar before the context becomes the commit point.
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except OSError:
        raise SnapshotError(
            "capture publication failed or destination became occupied"
        ) from None
    finally:
        for temp in temporary:
            temp.unlink(missing_ok=True)
    from .offline_notice import write_capture_warning

    write_capture_warning(directory)
