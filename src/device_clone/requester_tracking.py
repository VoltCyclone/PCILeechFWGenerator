"""Required requester policy and the plan resolved from live Vivado readbacks."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

# The controller model covers pcileech_pcie_drp_startup.sv. pcie_a7 and the
# top are pinned for how that controller is wired: its DRP ports, plan
# parameters and the endpoint reset gate. A board change to any of them must
# be re-checked before the pins move.
QUALIFIED_STARTUP_SOURCE_SHA256 = {
    "pcileech_pcie_drp_startup.sv": (
        "6d6810cdfecf75de41538d7c7a3d31bccb84488e8046e03d2a166a1e37751e2a"
    ),
    "pcileech_pcie_a7.sv": (
        "93a6a950a4162623d904f16bbd4090fc8a59339bbbababb9042a7ebb35ffde40"
    ),
    "pcileech_100t484_x1_top.sv": (
        "e875a06059400b458c645804f6bbe0cb558c4ab7c1bb60fcbf1390fd800fded5"
    ),
}
QUALIFIED_STARTUP_MODEL_SHA256 = {
    "proof-summary.json": (
        "5a52c57b27c2701088f9944d29e18ce8d66b348c3d3182fb68ee483227dd7138"
    ),
    "abcd-endpoint-model-hashes.json": (
        "374410b87e1683483e4b481041a44d87be45069b4079f266bdf0c0860debd6f0"
    ),
    # Same model run with AER_Enabled=true.
    "abcd-aer-results.json": (
        "c1d0eec92f878ded73b2baf3f069df7d7ea1d38c3679a7a7279f270801d84ae0"
    ),
    "abcd-aer-positive.log": (
        "7203dca35ace37995d0c48c5ff0c256be516124d0cf9855f0d9863d00447b424"
    ),
}


class RequesterStartupQualification(BaseModel):
    """Shipped evidence for the controller mechanism, not full-board proof."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    qualification_id: Literal["captaindma100t-gen1-abcd-drp64-v1"]
    scope: Literal["controller_model_only"]
    board: Literal["pcileech_100t484_x1"]
    source_sha256: dict[str, str]
    model_evidence_sha256: dict[str, str]

    @model_validator(mode="after")
    def _known_evidence(self) -> "RequesterStartupQualification":
        if self.source_sha256 != QUALIFIED_STARTUP_SOURCE_SHA256:
            raise ValueError("Requester startup source hashes are not qualified")
        if self.model_evidence_sha256 != QUALIFIED_STARTUP_MODEL_SHA256:
            raise ValueError("Requester startup model evidence is not qualified")
        return self


class RequesterTrackingPolicy(BaseModel):
    """Donor inputs plus explicit build policies; no inferred donor values."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cpl_timeout_ranges: Literal["A", "B", "AB", "BC", "ABC", "BCD", "ABCD"]
    cpl_timeout_disable_supported: StrictBool
    extended_tag_supported: StrictBool
    drp_ready_timeout_cycles: int = Field(ge=16, le=65535)
    startup_qualification: Optional[RequesterStartupQualification] = None

    @model_validator(mode="after")
    def _qualified_shape(self) -> "RequesterTrackingPolicy":
        if self.startup_qualification is not None and (
            self.cpl_timeout_ranges != "ABCD"
            or not self.cpl_timeout_disable_supported
            or self.extended_tag_supported
            or self.drp_ready_timeout_cycles != 64
        ):
            raise ValueError("Requester policy is outside startup qualification scope")
        return self


class RequesterTrackingPlan(RequesterTrackingPolicy):
    """A complete plan, after reading the actual IP used for this build."""

    clk_pcie_hz: Literal[62500000, 125000000, 250000000]
    trim_tlp_digest: StrictBool
    # EOF ECRC error handling is not integrated in the current RX path.
    ecrc_check_enabled: Literal[False]
    flow_control_interface_enabled: Literal[True]
    ip_initial_cpl_timeout_disable_supported: StrictBool
    startup_verified: Literal[False]

    @model_validator(mode="after")
    def _initial_state(self) -> "RequesterTrackingPlan":
        if self.startup_qualification is not None and (
            self.clk_pcie_hz != 62500000 or self.trim_tlp_digest
        ):
            raise ValueError("Resolved requester IP is outside qualification scope")
        if (
            self.ip_initial_cpl_timeout_disable_supported
            != self.cpl_timeout_disable_supported
            and self.startup_qualification is None
        ):
            raise ValueError("Unqualified requester IP-initial capability mismatch")
        return self
