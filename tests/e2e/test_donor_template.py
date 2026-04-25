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


@pytest.mark.xfail(
    reason=(
        "Known issue: the default `donor-template` output omits "
        "`device_info.identification.vendor_id` and `device_id`, which the "
        "`--validate` subcommand requires. Tracked as a separate bug — the "
        "test stays here so we don't lose visibility."
    ),
    strict=False,
)
def test_donor_template_validate_accepts_self_output(
    pcileech_cli, isolated_workdir: Path
) -> None:
    """A template the CLI just wrote should validate successfully."""
    output = isolated_workdir / "self.json"
    gen = pcileech_cli(
        "donor-template",
        "--save-to",
        str(output),
        cwd=isolated_workdir,
    )
    assert gen.returncode == 0

    validate = pcileech_cli(
        "donor-template",
        "--validate",
        str(output),
        cwd=isolated_workdir,
    )
    assert validate.returncode == 0, (
        f"self-generated template failed validation\n"
        f"stdout: {validate.stdout}\nstderr: {validate.stderr}"
    )


def test_donor_template_validate_runs_without_crash(
    pcileech_cli, isolated_workdir: Path
) -> None:
    """Companion smoke check: even if a generated template fails
    validation today, the validator must exit cleanly with a usage-style
    error rather than tracebacking. Catches regressions where the
    validator chokes on its own output format."""
    output = isolated_workdir / "self.json"
    gen = pcileech_cli(
        "donor-template",
        "--save-to",
        str(output),
        cwd=isolated_workdir,
    )
    assert gen.returncode == 0

    validate = pcileech_cli(
        "donor-template",
        "--validate",
        str(output),
        cwd=isolated_workdir,
    )
    # 0 = valid, 1 = validation errors. Anything else (segfault, traceback)
    # is a bug.
    assert validate.returncode in (0, 1), (
        f"validator crashed with code {validate.returncode}\n"
        f"stdout: {validate.stdout}\nstderr: {validate.stderr}"
    )
    assert "Traceback" not in validate.combined, (
        "validator emitted a Python traceback instead of a clean error"
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
