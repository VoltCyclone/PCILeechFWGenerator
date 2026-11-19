"""Build records distinguish live IP readback from planned post-startup state."""

import json
import subprocess

import pytest
from pcileechfwgenerator.vivado_handling.build_evidence import (
    record_build_success,
    write_build_manifest,
)


def fixture(
    tmp_path,
    *,
    initial=True,
    final=True,
    qualification=None,
    verified=False,
    clock=62500000,
    trim=False,
):
    policy = {
        "cpl_timeout_ranges": "ABCD",
        "cpl_timeout_disable_supported": final,
        "extended_tag_supported": False,
        "drp_ready_timeout_cycles": 64,
        "startup_qualification": qualification,
    }
    resolved = policy | {
        "clk_pcie_hz": clock,
        "trim_tlp_digest": trim,
        "ecrc_check_enabled": False,
        "flow_control_interface_enabled": True,
        "ip_initial_cpl_timeout_disable_supported": initial,
        "startup_verified": verified,
    }
    script = tmp_path / "build_all.tcl"
    script.write_text("# test input\n")
    policy_path = tmp_path / "requester_policy.json"
    policy_path.write_text(json.dumps(policy))
    write_build_manifest(
        tmp_path,
        board="pcileech_100t484_x1",
        part="xc7a100tfgg484-1",
        inputs=[script, policy_path],
    )
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "image.bit").write_bytes(b"test-image")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "tool_version.txt").write_text("2026.1")
    (tmp_path / "requester_plan_resolved.json").write_text(json.dumps(resolved))
    return script


def test_success_records_actual_ip_initial_and_unverified_final_target(tmp_path):
    script = fixture(tmp_path)
    record_build_success(script)
    record = json.loads((tmp_path / "output" / "build_result.json").read_text())
    assert (
        record["requester"]["plan"]["ip_initial_cpl_timeout_disable_supported"] is True
    )
    assert record["requester"]["plan"]["cpl_timeout_disable_supported"] is True
    assert record["requester"]["drp_startup_verified"] is False
    assert record["requester"]["model_evidence"] == {}


@pytest.mark.parametrize(
    "values",
    [
        {"initial": False},
        {"initial": False, "qualification": {"id": "not-qualified"}},
        {"qualification": {"id": "not-qualified"}},
        {"initial": 1},
        {"verified": True},
        {
            "initial": True,
            "final": False,
            "qualification": {"id": "unsupported-reverse-transform"},
        },
    ],
)
def test_publication_refuses_unqualified_transform_or_false_runtime_claim(
    tmp_path, values
):
    script = fixture(tmp_path, **values)
    with pytest.raises(subprocess.CalledProcessError):
        record_build_success(script)
    assert not (tmp_path / "output" / "build_result.json").exists()


def test_qualified_transition_keeps_model_proof_separate_from_runtime(tmp_path):
    from pcileechfwgenerator.device_clone.requester_tracking import (
        QUALIFIED_STARTUP_MODEL_SHA256,
        QUALIFIED_STARTUP_SOURCE_SHA256,
        RequesterStartupQualification,
    )

    qualification = RequesterStartupQualification(
        qualification_id="captaindma100t-gen1-abcd-drp64-v1",
        scope="controller_model_only",
        board="pcileech_100t484_x1",
        source_sha256=dict(QUALIFIED_STARTUP_SOURCE_SHA256),
        model_evidence_sha256=dict(QUALIFIED_STARTUP_MODEL_SHA256),
    )
    script = fixture(
        tmp_path, initial=False, qualification=qualification.model_dump(mode="json")
    )
    record_build_success(script)
    record = json.loads((tmp_path / "output" / "build_result.json").read_text())[
        "requester"
    ]
    assert record["plan"]["ip_initial_cpl_timeout_disable_supported"] is False
    assert record["plan"]["cpl_timeout_disable_supported"] is True
    assert record["drp_startup_verified"] is False
    assert record["model_evidence"]["scope"] == "controller_model_only"
    assert record["model_evidence"]["model_evidence_sha256"]["proof-summary.json"] == (
        "5a52c57b27c2701088f9944d29e18ce8d66b348c3d3182fb68ee483227dd7138"
    )
