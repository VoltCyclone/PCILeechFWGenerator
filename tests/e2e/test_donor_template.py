"""End-to-end coverage for the ``donor-template`` subcommand.

This is the most "complete" subcommand that doesn't require root/VFIO/
hardware: it generates a JSON template, optionally validates one, and
emits structured output. Perfect for CI smoke coverage of the templating
pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_donor_template_generates_valid_json(
    pcileech_cli, isolated_workdir: Path
) -> None:
    output = isolated_workdir / "donor.json"
    result = pcileech_cli(
        "donor-template",
        "--save-to",
        str(output),
        cwd=isolated_workdir,
    )
    assert result.returncode == 0, (
        f"donor-template exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert output.exists(), f"expected {output} to be created"

    payload = json.loads(output.read_text())
    assert isinstance(payload, dict), "donor template must be a JSON object"
    assert payload, "donor template must not be empty"


def test_donor_template_compact_is_single_line(
    pcileech_cli, isolated_workdir: Path
) -> None:
    """``--compact`` should drop indentation (one line, no leading spaces
    on continuation lines)."""
    output = isolated_workdir / "compact.json"
    result = pcileech_cli(
        "donor-template",
        "--save-to",
        str(output),
        "--compact",
        cwd=isolated_workdir,
    )
    assert result.returncode == 0
    raw = output.read_text()
    # Compact JSON has no newlines after braces / commas.
    assert "\n" not in raw.strip().rstrip("\n")
    # Should still be valid JSON.
    json.loads(raw)


def test_donor_template_blank_yields_minimal_schema(
    pcileech_cli, isolated_workdir: Path
) -> None:
    output = isolated_workdir / "blank.json"
    result = pcileech_cli(
        "donor-template",
        "--save-to",
        str(output),
        "--blank",
        cwd=isolated_workdir,
    )
    assert result.returncode == 0
    payload = json.loads(output.read_text())
    assert isinstance(payload, dict)
    # The blank template still has to expose at least vendor/device fields
    # for downstream rendering, even if values are placeholders.
    flat_keys = set(_flatten_keys(payload))
    assert any(
        "vendor" in k.lower() for k in flat_keys
    ), f"blank template missing a vendor field; keys={sorted(flat_keys)[:20]}"


def test_default_template_fails_validation_until_filled(
    pcileech_cli, isolated_workdir: Path
) -> None:
    """A freshly generated template ships with ``vendor_id`` /
    ``device_id`` set to ``None`` as fill-in placeholders. Validating it
    unedited must fail (that is the validator's job) and the error
    message must distinguish "unset placeholder" from "missing key" so
    users know they just need to edit, not regenerate."""
    output = isolated_workdir / "self.json"
    gen = pcileech_cli(
        "donor-template", "--save-to", str(output), cwd=isolated_workdir
    )
    assert gen.returncode == 0

    validate = pcileech_cli(
        "donor-template", "--validate", str(output), cwd=isolated_workdir
    )
    assert validate.returncode != 0, (
        "validator accepted a fresh template with placeholder vendor/device "
        "IDs — that defeats the purpose of validation"
    )
    text = validate.combined
    assert "vendor_id" in text and "device_id" in text
    assert "placeholder" in text.lower() or "unset" in text.lower(), (
        f"error message should mention 'placeholder' / 'unset'; got: {text!r}"
    )
    assert "Traceback" not in text, (
        "validator emitted a Python traceback instead of a clean error"
    )


def test_filled_template_validates_successfully(
    pcileech_cli, isolated_workdir: Path
) -> None:
    """Generate → fill in vendor/device IDs → validate should pass.
    This is the supported end-to-end workflow for the donor template."""
    output = isolated_workdir / "self.json"
    gen = pcileech_cli(
        "donor-template", "--save-to", str(output), cwd=isolated_workdir
    )
    assert gen.returncode == 0

    payload = json.loads(output.read_text())
    payload["device_info"]["identification"]["vendor_id"] = "0x10DE"
    payload["device_info"]["identification"]["device_id"] = "0x1234"
    output.write_text(json.dumps(payload, indent=2))

    validate = pcileech_cli(
        "donor-template", "--validate", str(output), cwd=isolated_workdir
    )
    assert validate.returncode == 0, (
        f"filled template should validate successfully\n"
        f"stdout: {validate.stdout}\nstderr: {validate.stderr}"
    )


def test_donor_template_validate_rejects_garbage(
    pcileech_cli, isolated_workdir: Path
) -> None:
    bogus = isolated_workdir / "bogus.json"
    bogus.write_text("not even json")
    result = pcileech_cli(
        "donor-template",
        "--validate",
        str(bogus),
        cwd=isolated_workdir,
    )
    assert result.returncode != 0


def _flatten_keys(node, prefix: str = ""):
    """Yield dotted-path keys for assertions on nested templates."""
    if isinstance(node, dict):
        for key, value in node.items():
            full = f"{prefix}.{key}" if prefix else key
            yield full
            yield from _flatten_keys(value, full)
    elif isinstance(node, list):
        for item in node:
            yield from _flatten_keys(item, prefix)
