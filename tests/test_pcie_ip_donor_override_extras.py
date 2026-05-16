"""Tests for the expanded PCIe IP donor override (gaps A4/A6/A7/A8/C1/C2/C3/D2).

The original five-ID override is covered by test_pcie_ip_donor_override.py.
This module tests the additional CONFIG.* keys we emit when the donor profile
exposes class code, MPS, MSI-X layout, link speed/width, AER, ARI,
completion-timeout, and DSN values.
"""
import sys
import textwrap
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from pcileechfwgenerator.vivado_handling.fifo_donor_patcher import DonorIDs
from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
    DonorPCIeIPConfig,
    generate_pcie_ip_override_tcl,
)


def _intel_donor() -> DonorIDs:
    return DonorIDs(
        vendor_id=0x8086,
        device_id=0x1533,
        subsystem_vendor_id=0x8086,
        subsystem_id=0x0001,
        revision_id=0x03,
    )


class TestDonorPCIeIPConfigDataclass:
    def test_all_fields_default_to_none(self):
        cfg = DonorPCIeIPConfig()
        # Every field must default to None so a partial donor profile is
        # safe — None means "don't emit a CONFIG.<key> line for this".
        assert cfg.class_code is None
        assert cfg.max_payload_size is None
        assert cfg.link_speed is None
        assert cfg.link_width is None
        assert cfg.msix_enabled is None
        assert cfg.msix_table_size is None
        assert cfg.msix_table_bir is None
        assert cfg.msix_table_offset is None
        assert cfg.msix_pba_bir is None
        assert cfg.msix_pba_offset is None
        assert cfg.aer_enabled is None
        assert cfg.ari_forwarding_supported is None
        assert cfg.cpl_timeout_ranges is None
        assert cfg.cpl_timeout_disable_supported is None
        assert cfg.dsn_value is None

    def test_is_frozen(self):
        cfg = DonorPCIeIPConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.class_code = 0x020000  # type: ignore[misc]


class TestGenerateTclWithEmptyExtra:
    """An all-None extra must produce exactly the same output as no extra at all."""

    def test_none_extra_matches_baseline(self):
        baseline = generate_pcie_ip_override_tcl(_intel_donor())
        with_empty = generate_pcie_ip_override_tcl(
            _intel_donor(), extra=DonorPCIeIPConfig()
        )
        assert baseline == with_empty


class TestExtraEmissionMechanism:
    """The TCL builder emits one CONFIG.<key> line per non-None extra field."""

    def test_omits_keys_for_none_fields(self):
        # An extra with every field None must not introduce any new CONFIG.*
        # lines beyond the original five.
        tcl = generate_pcie_ip_override_tcl(
            _intel_donor(), extra=DonorPCIeIPConfig()
        )
        config_lines = [
            line for line in tcl.splitlines()
            if line.strip().startswith("CONFIG.")
        ]
        assert len(config_lines) == 5  # Vendor, Device, SVID, SID, Rev

    def test_emits_backslash_continuation_consistently(self):
        # All CONFIG.* lines must end with " \\" so Vivado's set_property -dict
        # parses the list across newlines. Regressing this is a synthesis-time
        # syntax error that's annoying to debug.
        tcl = generate_pcie_ip_override_tcl(_intel_donor())
        for line in tcl.splitlines():
            stripped = line.rstrip()
            if "CONFIG." in stripped:
                assert stripped.endswith("\\"), (
                    f"missing line-continuation backslash: {line!r}"
                )


class TestClassCodeEmission:
    def test_emits_three_class_code_keys_when_set(self):
        # 0x020000 = Ethernet controller (Intel 82574L donor)
        extra = DonorPCIeIPConfig(class_code=0x020000)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        # Xilinx wants two-hex-digit strings, no 0x prefix.
        assert "CONFIG.Class_Code_Base 02" in tcl
        assert "CONFIG.Class_Code_Sub 00" in tcl
        assert "CONFIG.Class_Code_Interface 00" in tcl

    def test_nvme_class_code_unpacks_correctly(self):
        # 0x010802 = NVMe (base=01 mass storage, sub=08 NVM, interface=02 NVMe)
        extra = DonorPCIeIPConfig(class_code=0x010802)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.Class_Code_Base 01" in tcl
        assert "CONFIG.Class_Code_Sub 08" in tcl
        assert "CONFIG.Class_Code_Interface 02" in tcl

    def test_class_code_none_emits_nothing(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "Class_Code_Base" not in tcl
        assert "Class_Code_Sub" not in tcl
        assert "Class_Code_Interface" not in tcl

    def test_class_code_out_of_range_raises(self):
        # 24-bit field; values that don't fit must surface, not silently truncate.
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(class_code=0x1000000),
            )


class TestMaxPayloadSizeEmission:
    @pytest.mark.parametrize("mps,token", [
        (128, "128_bytes"),
        (256, "256_bytes"),
        (512, "512_bytes"),
        (1024, "1024_bytes"),
        (2048, "2048_bytes"),
        (4096, "4096_bytes"),
    ])
    def test_emits_valid_mps_token(self, mps, token):
        extra = DonorPCIeIPConfig(max_payload_size=mps)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert f"CONFIG.Max_Payload_Size {token}" in tcl

    def test_invalid_mps_raises(self):
        # Anything not in the Xilinx-accepted set must fail loudly rather
        # than silently emitting a token Vivado will reject during IP elab.
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(), extra=DonorPCIeIPConfig(max_payload_size=384)
            )

    def test_none_emits_nothing(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "Max_Payload_Size" not in tcl


class TestLinkCapEmission:
    @pytest.mark.parametrize("gen,encoded", [
        (1, 1),  # Gen1 -> 2.5 GT/s -> encoding 1
        (2, 2),  # Gen2 -> 5.0 GT/s -> encoding 2
        (3, 4),  # Gen3 -> 8.0 GT/s -> encoding 4
        (4, 8),  # Gen4 -> 16.0 GT/s -> encoding 8
    ])
    def test_emits_link_speed_per_pcie_encoding(self, gen, encoded):
        extra = DonorPCIeIPConfig(link_speed=gen)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert f"CONFIG.LINK_CAP_MAX_LINK_SPEED {encoded}" in tcl

    def test_invalid_link_speed_raises(self):
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(), extra=DonorPCIeIPConfig(link_speed=6)
            )

    def test_emits_link_speed_code_directly_when_set(self):
        # Direct spec-encoded value (skips the generation→encoding map).
        extra = DonorPCIeIPConfig(link_speed_code=4)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.LINK_CAP_MAX_LINK_SPEED 4" in tcl

    def test_raises_if_both_link_speed_and_link_speed_code_set(self):
        with pytest.raises(ValueError, match=r"link_speed.*link_speed_code"):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(link_speed=3, link_speed_code=4),
            )

    @pytest.mark.parametrize("bad_code", [3, 5, 6, 7, 9, 17])
    def test_invalid_link_speed_code_raises(self, bad_code):
        # Spec-encoded value must be one of {1, 2, 4, 8, 16}.
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(link_speed_code=bad_code),
            )

    @pytest.mark.parametrize("width", [1, 2, 4, 8, 16])
    def test_emits_link_width_as_integer(self, width):
        extra = DonorPCIeIPConfig(link_width=width)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert f"CONFIG.LINK_CAP_MAX_LINK_WIDTH {width}" in tcl

    def test_invalid_link_width_raises(self):
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(), extra=DonorPCIeIPConfig(link_width=3)
            )

    def test_none_emits_neither(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "LINK_CAP_MAX_LINK_SPEED" not in tcl
        assert "LINK_CAP_MAX_LINK_WIDTH" not in tcl


class TestMsixBundleEmission:
    def test_emits_full_bundle_when_enabled(self):
        extra = DonorPCIeIPConfig(
            msix_enabled=True,
            msix_table_size=16,
            msix_table_bir=2,
            msix_table_offset=0x2000,
            msix_pba_bir=2,
            msix_pba_offset=0x3000,
        )
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.MSIx_Enabled true" in tcl
        assert "CONFIG.MSIx_Table_Size 16" in tcl
        assert "CONFIG.MSIx_Table_BIR BAR_2" in tcl
        assert "CONFIG.MSIx_Table_Offset 8192" in tcl  # 0x2000 = 8192 decimal
        assert "CONFIG.MSIx_PBA_BIR BAR_2" in tcl
        assert "CONFIG.MSIx_PBA_Offset 12288" in tcl

    def test_emits_disabled_when_explicit_false(self):
        # An explicit msix_enabled=False asserts the donor has no MSI-X; emit
        # only the enable=false line and skip the layout fields.
        extra = DonorPCIeIPConfig(msix_enabled=False)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.MSIx_Enabled false" in tcl
        assert "MSIx_Table_Size" not in tcl
        assert "MSIx_Table_BIR" not in tcl

    def test_raises_when_enabled_but_layout_missing(self):
        # Match pins the actual contract — the error must enumerate the
        # missing fields via "missing: ..." (UX win for partial donor profiles).
        with pytest.raises(ValueError, match=r"missing: msix_table_size"):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(msix_enabled=True),
            )

    @pytest.mark.parametrize("bad_bir", [-1, 6, 8])
    def test_raises_on_invalid_bir(self, bad_bir):
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(
                    msix_enabled=True,
                    msix_table_size=4,
                    msix_table_bir=bad_bir,
                    msix_table_offset=0,
                    msix_pba_bir=0,
                    msix_pba_offset=0x1000,
                ),
            )

    def test_raises_on_zero_table_size(self):
        # The PCIe spec encodes MSI-X table size as N-1, so size=0 means a
        # single-vector device. We expect callers to feed the post-decoded
        # vector count (>=1); zero is meaningless and likely a bug.
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(
                    msix_enabled=True,
                    msix_table_size=0,
                    msix_table_bir=0,
                    msix_table_offset=0,
                    msix_pba_bir=0,
                    msix_pba_offset=0,
                ),
            )

    def test_none_emits_nothing(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "MSIx_Enabled" not in tcl


class TestAerAndAriEmission:
    @pytest.mark.parametrize("value,token", [(True, "true"), (False, "false")])
    def test_emits_aer_enabled(self, value, token):
        extra = DonorPCIeIPConfig(aer_enabled=value)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert f"CONFIG.AER_Enabled {token}" in tcl

    @pytest.mark.parametrize("value,token", [(True, "true"), (False, "false")])
    def test_emits_ari_forwarding(self, value, token):
        extra = DonorPCIeIPConfig(ari_forwarding_supported=value)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert f"CONFIG.ARI_Forwarding_Supported {token}" in tcl

    def test_none_emits_neither(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "AER_Enabled" not in tcl
        assert "ARI_Forwarding_Supported" not in tcl


class TestCplTimeoutEmission:
    @pytest.mark.parametrize("token", [
        "none", "A", "B", "C", "D", "AB", "BC", "BCD", "ABCD",
    ])
    def test_emits_valid_range_token(self, token):
        extra = DonorPCIeIPConfig(cpl_timeout_ranges=token)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        # Xilinx prefixes the token with "Range_" for non-"none" values; "none"
        # passes through verbatim.
        if token == "none":
            assert "CONFIG.Cpl_Timeout_Range none" in tcl
        else:
            assert f"CONFIG.Cpl_Timeout_Range Range_{token}" in tcl

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(cpl_timeout_ranges="ZZ"),
            )

    @pytest.mark.parametrize("value,token", [(True, "true"), (False, "false")])
    def test_emits_cpl_timeout_disable(self, value, token):
        extra = DonorPCIeIPConfig(cpl_timeout_disable_supported=value)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert f"CONFIG.Cpl_Timeout_Disable_Sup {token}" in tcl

    def test_none_emits_neither(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "Cpl_Timeout_Range" not in tcl
        assert "Cpl_Timeout_Disable_Sup" not in tcl


class TestDsnEmission:
    def test_emits_both_halves(self):
        # Intel OUI 0x001B21, extension 0x01, upper 0xDEADBEEF
        dsn = (0xDEADBEEF << 32) | 0x01001B21
        extra = DonorPCIeIPConfig(dsn_value=dsn)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.DSN_HEX1 01001B21" in tcl
        assert "CONFIG.DSN_HEX2 DEADBEEF" in tcl

    def test_emits_zero_dsn_safely(self):
        # DSN of zero is unusual but legal; donor profile may report it on
        # devices with no real OUI assignment. Both halves should be emitted
        # as zero-padded 8-digit hex.
        extra = DonorPCIeIPConfig(dsn_value=0)
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=extra)
        assert "CONFIG.DSN_HEX1 00000000" in tcl
        assert "CONFIG.DSN_HEX2 00000000" in tcl

    def test_raises_on_oversized_dsn(self):
        with pytest.raises(ValueError):
            generate_pcie_ip_override_tcl(
                _intel_donor(),
                extra=DonorPCIeIPConfig(dsn_value=1 << 64),
            )

    def test_none_emits_nothing(self):
        tcl = generate_pcie_ip_override_tcl(_intel_donor(), extra=DonorPCIeIPConfig())
        assert "DSN_HEX1" not in tcl
        assert "DSN_HEX2" not in tcl


_FAKE_GENERATE_PROJECT = textwrap.dedent(
    """\
    create_project pcileech ./vivado_project -part xc7a75tfgg484-2
    """
)


class TestApplyWithExtra:
    def test_extra_is_forwarded_to_override_file(self, tmp_path):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            apply_pcie_ip_donor_override,
        )
        (tmp_path / "vivado_generate_project.tcl").write_text(_FAKE_GENERATE_PROJECT)

        extra = DonorPCIeIPConfig(
            class_code=0x020000,
            aer_enabled=True,
            link_speed=2,
            link_width=4,
        )
        result = apply_pcie_ip_donor_override(tmp_path, _intel_donor(), extra=extra)

        body = result["override_path"].read_text()
        assert "CONFIG.Class_Code_Base 02" in body
        assert "CONFIG.AER_Enabled true" in body
        assert "CONFIG.LINK_CAP_MAX_LINK_SPEED 2" in body
        assert "CONFIG.LINK_CAP_MAX_LINK_WIDTH 4" in body

    def test_extra_default_none_preserves_original_behavior(self, tmp_path):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            apply_pcie_ip_donor_override,
        )
        (tmp_path / "vivado_generate_project.tcl").write_text(_FAKE_GENERATE_PROJECT)

        # No `extra=` kwarg — must behave exactly like the pre-change code path.
        result = apply_pcie_ip_donor_override(tmp_path, _intel_donor())
        body = result["override_path"].read_text()
        # Only the five identification lines, no extras.
        config_lines = [
            line for line in body.splitlines()
            if line.strip().startswith("CONFIG.")
        ]
        assert len(config_lines) == 5


class TestDonorPCIeIPConfigExtractor:
    def test_extracts_all_fields_from_well_formed_result(self):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            donor_pcie_ip_config_from_result,
        )

        result = {
            "template_context": {
                "max_payload_size": 256,
                "device_serial_number_int": (0xDEADBEEF << 32) | 0x01001B21,
                # Producer writes spec-encoded link speed/width at top level.
                # 2 == Gen2 (5.0 GT/s) spec encoding, 4 == x4 lanes.
                "pcie_max_link_speed": 2,
                "pcie_max_link_width": 4,
                "device_config": {
                    "class_code": "0x020000",
                    "supports_aer": True,
                    "ari_capable": False,
                    "cpl_timeout_ranges": "BCD",
                    "cpl_timeout_disable_sup": True,
                },
            },
            "msix_data": {
                "enabled": True,
                "table_size": 16,
                "table_bir": 2,
                "table_offset": 0x2000,
                "pba_bir": 2,
                "pba_offset": 0x3000,
                "is_valid": True,
            },
        }

        cfg = donor_pcie_ip_config_from_result(result)

        assert cfg.class_code == 0x020000
        assert cfg.max_payload_size == 256
        # Extractor populates link_speed_code (spec-encoded), not link_speed (gen).
        assert cfg.link_speed_code == 2
        assert cfg.link_speed is None
        assert cfg.link_width == 4
        assert cfg.msix_enabled is True
        assert cfg.msix_table_size == 16
        assert cfg.msix_table_bir == 2
        assert cfg.msix_table_offset == 0x2000
        assert cfg.msix_pba_bir == 2
        assert cfg.msix_pba_offset == 0x3000
        assert cfg.aer_enabled is True
        assert cfg.ari_forwarding_supported is False
        assert cfg.cpl_timeout_ranges == "BCD"
        assert cfg.cpl_timeout_disable_supported is True
        assert cfg.dsn_value == (0xDEADBEEF << 32) | 0x01001B21

    def test_empty_result_returns_all_none_config(self):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            donor_pcie_ip_config_from_result,
        )
        cfg = donor_pcie_ip_config_from_result({})
        assert cfg.class_code is None
        assert cfg.max_payload_size is None
        assert cfg.msix_enabled is None
        assert cfg.dsn_value is None

    def test_invalid_msix_data_does_not_set_msix_fields(self):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            donor_pcie_ip_config_from_result,
        )
        result = {
            "template_context": {"device_config": {}},
            "msix_data": {
                "enabled": True,
                "table_size": 16,
                "is_valid": False,  # parser flagged it as bad
            },
        }
        cfg = donor_pcie_ip_config_from_result(result)
        assert cfg.msix_enabled is None
        assert cfg.msix_table_size is None

    def test_class_code_accepts_int_or_string(self):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            donor_pcie_ip_config_from_result,
        )
        for value in (0x020000, "0x020000", "020000", "131072"):
            result = {
                "template_context": {"device_config": {"class_code": value}},
            }
            cfg = donor_pcie_ip_config_from_result(result)
            assert cfg.class_code == 0x020000, f"class_code={value!r}"

    def test_malformed_values_silently_drop(self):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            donor_pcie_ip_config_from_result,
        )
        # Garbage donor data must not raise — extractor returns None for the
        # bad field and emission skips the corresponding CONFIG line.
        result = {
            "template_context": {
                "device_config": {"class_code": "not-a-number", "link_speed": "x"},
            },
        }
        cfg = donor_pcie_ip_config_from_result(result)
        assert cfg.class_code is None
        assert cfg.link_speed is None

    def test_partial_msix_data_drops_entire_msix_block(self):
        # If is_valid=True but one of the layout fields is non-coercible,
        # the extractor must return all-None for MSI-X rather than producing
        # partial state that the emitter would later reject.
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            donor_pcie_ip_config_from_result,
        )
        result = {
            "template_context": {"device_config": {}},
            "msix_data": {
                "enabled": True,
                "table_size": 16,
                "table_bir": 2,
                "table_offset": 0x2000,
                "pba_bir": 2,
                "pba_offset": "not-a-number",  # parser-unfriendly value
                "is_valid": True,
            },
        }
        cfg = donor_pcie_ip_config_from_result(result)
        # All six MSI-X fields must be None — partial state is worse than off.
        assert cfg.msix_enabled is None
        assert cfg.msix_table_size is None
        assert cfg.msix_table_bir is None
        assert cfg.msix_table_offset is None
        assert cfg.msix_pba_bir is None
        assert cfg.msix_pba_offset is None

    def test_link_speed_extracted_from_pcie_max_link_speed_top_level(self):
        from pcileechfwgenerator.vivado_handling.pcie_ip_donor_override import (
            donor_pcie_ip_config_from_result,
        )
        # Producer writes the SPEC-ENCODED value at the top level (not generation).
        # 4 == 8.0 GT/s (Gen3). The extractor must store it as link_speed_code,
        # not as the generation field.
        result = {
            "template_context": {
                "pcie_max_link_speed": 4,
                "pcie_max_link_width": 8,
                "device_config": {},
            },
        }
        cfg = donor_pcie_ip_config_from_result(result)
        assert cfg.link_speed_code == 4
        assert cfg.link_width == 8
        # The generation field stays None — caller didn't say "Gen3."
        assert cfg.link_speed is None
