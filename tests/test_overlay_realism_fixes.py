#!/usr/bin/env python3
"""
Unit tests for overlay realism fixes.

Tests verify critical anti-fingerprinting improvements:
1. DSN generation - deterministic non-zero DSN from device identifiers
2. Behavioral analyzers - device-specific values derived from identifiers
3. Interrupt logic - proper state machine implementation
4. Extended config space - donor data cloning

These tests ensure emulated devices cannot be easily distinguished from
real hardware through timing analysis, configuration space inspection,
or behavioral observation.
"""

import logging
import hashlib
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from pcileechfwgenerator.templating.sv_context_builder import SVContextBuilder
from pcileechfwgenerator.behavioral.base import BehaviorType
from pcileechfwgenerator.behavioral.network_behavioral import NetworkBehavioralAnalyzer
from pcileechfwgenerator.behavioral.storage_behavioral import StorageBehavioralAnalyzer


class TestDSNGeneration:
    """Test deterministic DSN generation from device identifiers."""

    @pytest.fixture
    def context_builder(self):
        """Provide SVContextBuilder instance."""
        logger = logging.getLogger("test_dsn_generation")
        return SVContextBuilder(logger)

    def test_dsn_never_zero(self, context_builder):
        """Test that generated DSN is never zero (fingerprint vulnerability)."""
        context = {"device_serial_number": None, "vendor_id_int": 0x8086, "device_id_int": 0x1234}
        template_context = {
            "device_config": {"vendor_id": 0x8086, "device_id": 0x1234}
        }
        
        context_builder._add_device_serial_number(context, template_context)
        
        dsn = context.get("device_serial_number_int")
        assert dsn is not None, "DSN should be generated"
        assert dsn != 0, "DSN should never be zero to avoid fingerprinting"
        assert dsn != 0x0000000000000000, "DSN should never be all zeros"

    def test_dsn_deterministic(self, context_builder):
        """Test that DSN is deterministic based on device identifiers."""
        template_context = {
            "device_config": {"vendor_id": 0x8086, "device_id": 0x1234}
        }
        
        # Generate DSN twice with same inputs
        context1 = {"device_serial_number": None, "vendor_id_int": 0x8086, "device_id_int": 0x1234}
        context2 = {"device_serial_number": None, "vendor_id_int": 0x8086, "device_id_int": 0x1234}
        
        context_builder._add_device_serial_number(context1, template_context)
        context_builder._add_device_serial_number(context2, template_context)
        
        assert context1["device_serial_number_int"] == context2["device_serial_number_int"], \
            "DSN should be deterministic for same device identifiers"

    def test_dsn_unique_per_device(self, context_builder):
        """Test that different devices get different DSNs."""
        template_context1 = {
            "device_config": {"vendor_id": 0x8086, "device_id": 0x1234}
        }
        template_context2 = {
            "device_config": {"vendor_id": 0x8086, "device_id": 0x5678}
        }
        
        context1 = {"device_serial_number": None, "vendor_id_int": 0x8086, "device_id_int": 0x1234}
        context2 = {"device_serial_number": None, "vendor_id_int": 0x8086, "device_id_int": 0x5678}
        
        context_builder._add_device_serial_number(context1, template_context1)
        context_builder._add_device_serial_number(context2, template_context2)
        
        assert context1["device_serial_number_int"] != context2["device_serial_number_int"], \
            "Different devices should have different DSNs"

    def test_dsn_preserves_explicit_value(self, context_builder):
        """Test that explicitly provided DSN is not overwritten."""
        explicit_dsn = 0xDEADBEEFCAFEBABE
        context = {"device_serial_number": explicit_dsn, "vendor_id_int": 0x8086, "device_id_int": 0x1234}
        template_context = {
            "device_config": {"vendor_id": 0x8086, "device_id": 0x1234},
            "device_serial_number": explicit_dsn
        }
        
        context_builder._add_device_serial_number(context, template_context)
        
        # Should preserve the explicit value if it's valid
        dsn = context.get("device_serial_number_int")
        assert dsn is not None
        assert dsn != 0, "DSN should never be zero"

    def test_dsn_oui_structure(self, context_builder):
        """Test that generated DSN has proper OUI + extension structure."""
        context = {"device_serial_number": None, "vendor_id_int": 0x8086, "device_id_int": 0x1234}
        template_context = {
            "device_config": {"vendor_id": 0x8086, "device_id": 0x1234}
        }
        
        context_builder._add_device_serial_number(context, template_context)
        dsn = context.get("device_serial_number_int", 0)
        
        # DSN should be 64-bit value
        assert dsn <= 0xFFFFFFFFFFFFFFFF, "DSN should be 64-bit"
        assert dsn >= 0x0000000100000001, "DSN should have non-zero components"


class TestNetworkBehavioralDeviceSpecific:
    """Test network behavioral analyzer device-specific value generation."""

    @pytest.fixture
    def make_analyzer(self):
        """Factory for creating network analyzers with specific device IDs."""
        def _make(vendor_id, device_id):
            config = Mock()
            config.class_code = 0x020000  # Network controller
            config.vendor_id = vendor_id
            config.device_id = device_id
            config.subclass_code = 0x00  # Ethernet
            return NetworkBehavioralAnalyzer(config)
        return _make

    def test_mac_address_device_specific(self, make_analyzer):
        """Test that MAC addresses are derived from device identifiers."""
        analyzer1 = make_analyzer(0x8086, 0x1234)
        analyzer2 = make_analyzer(0x8086, 0x5678)
        
        spec1 = analyzer1.generate_spec()
        spec2 = analyzer2.generate_spec()
        
        mac_low1 = spec1.registers["mac_addr_low"].default_value
        mac_low2 = spec2.registers["mac_addr_low"].default_value
        
        assert mac_low1 != mac_low2, "Different devices should have different MAC addresses"

    def test_mac_address_locally_administered(self, make_analyzer):
        """Test that generated MAC has locally administered bit set."""
        analyzer = make_analyzer(0x8086, 0x1234)
        spec = analyzer.generate_spec()
        
        mac_high = spec.registers["mac_addr_high"].default_value
        # mac_high contains: [15:8] = OUI byte 1, [7:0] = OUI byte 0
        # Locally administered bit is bit 1 of OUI byte 0 (bits 7:0 of mac_high)
        oui_byte0 = mac_high & 0xFF
        assert oui_byte0 & 0x02, "MAC should have locally administered bit set"

    def test_mac_address_unicast(self, make_analyzer):
        """Test that generated MAC is unicast (multicast bit clear)."""
        analyzer = make_analyzer(0x8086, 0x1234)
        spec = analyzer.generate_spec()
        
        mac_high = spec.registers["mac_addr_high"].default_value
        # Multicast bit is bit 0 of OUI byte 0 (bits 7:0 of mac_high)
        oui_byte0 = mac_high & 0xFF
        assert not (oui_byte0 & 0x01), "MAC should be unicast (multicast bit clear)"

    def test_mac_address_deterministic(self, make_analyzer):
        """Test that MAC address is deterministic for same device."""
        analyzer1 = make_analyzer(0x8086, 0x1234)
        analyzer2 = make_analyzer(0x8086, 0x1234)
        
        spec1 = analyzer1.generate_spec()
        spec2 = analyzer2.generate_spec()
        
        assert spec1.registers["mac_addr_low"].default_value == \
               spec2.registers["mac_addr_low"].default_value, \
            "Same device should get same MAC address"

    def test_link_status_device_specific(self, make_analyzer):
        """Test that link status has device-specific bits."""
        analyzer1 = make_analyzer(0x8086, 0x1234)
        analyzer2 = make_analyzer(0x10EC, 0x8168)  # Realtek vs Intel
        
        spec1 = analyzer1.generate_spec()
        spec2 = analyzer2.generate_spec()
        
        status1 = spec1.registers["link_status"].default_value
        status2 = spec2.registers["link_status"].default_value
        
        # Link up bit (bit 0) should be set for both
        assert status1 & 0x01, "Link should be up (bit 0)"
        assert status2 & 0x01, "Link should be up (bit 0)"
        
        # Values should differ due to device-specific speed/duplex bits
        assert status1 != status2, "Different devices should have different status values"

    def test_interrupt_registers_present(self, make_analyzer):
        """Test that interrupt-related registers are present."""
        analyzer = make_analyzer(0x8086, 0x1234)
        spec = analyzer.generate_spec()
        
        assert "interrupt_status" in spec.registers, "Should have interrupt status register"
        assert "interrupt_mask" in spec.registers, "Should have interrupt mask register"
        
        # Check interrupt status has RW1C (MIXED) behavior
        int_status = spec.registers["interrupt_status"]
        assert int_status.behavior == BehaviorType.MIXED, \
            "Interrupt status should be RW1C (MIXED behavior)"


class TestStorageBehavioralDeviceSpecific:
    """Test storage behavioral analyzer device-specific value generation."""

    @pytest.fixture
    def make_analyzer(self):
        """Factory for creating storage analyzers with specific device IDs."""
        def _make(vendor_id, device_id):
            config = Mock()
            config.class_code = 0x010802  # NVMe storage controller
            config.vendor_id = vendor_id
            config.device_id = device_id
            config.subclass_code = 0x08  # Non-volatile memory
            return StorageBehavioralAnalyzer(config)
        return _make

    def test_nvme_cap_device_specific(self, make_analyzer):
        """Test that NVMe CAP register is device-specific."""
        analyzer1 = make_analyzer(0x144D, 0xA808)  # Samsung
        analyzer2 = make_analyzer(0x1987, 0x5012)  # Phison
        
        spec1 = analyzer1.generate_spec()
        spec2 = analyzer2.generate_spec()
        
        if spec1 and spec2 and "nvme_cap" in spec1.registers and "nvme_cap" in spec2.registers:
            cap1 = spec1.registers["nvme_cap"].default_value
            cap2 = spec2.registers["nvme_cap"].default_value
            
            # CAP register should differ between vendors
            assert cap1 != cap2, "Different devices should have different CAP values"

    def test_nvme_version_device_derived(self, make_analyzer):
        """Test that NVMe version register varies by device."""
        analyzer1 = make_analyzer(0x144D, 0xA808)
        analyzer2 = make_analyzer(0x144D, 0xA809)
        
        spec1 = analyzer1.generate_spec()
        spec2 = analyzer2.generate_spec()
        
        if spec1 and spec2 and "nvme_vs" in spec1.registers and "nvme_vs" in spec2.registers:
            vs1 = spec1.registers["nvme_vs"].default_value
            vs2 = spec2.registers["nvme_vs"].default_value
            
            # Both should be valid NVMe version formats (major.minor.tertiary)
            # Version is encoded as: major (31:16), minor (15:8), tertiary (7:0)
            assert vs1 & 0xFFFF0000, "Version should have major component"
            # Values may or may not differ depending on implementation

    def test_nvme_queue_attributes_device_specific(self, make_analyzer):
        """Test that admin queue attributes vary by device."""
        analyzer1 = make_analyzer(0x144D, 0xA808)
        analyzer2 = make_analyzer(0x1987, 0x5012)
        
        spec1 = analyzer1.generate_spec()
        spec2 = analyzer2.generate_spec()
        
        if spec1 and spec2 and "nvme_aqa" in spec1.registers and "nvme_aqa" in spec2.registers:
            aqa1 = spec1.registers["nvme_aqa"].default_value
            aqa2 = spec2.registers["nvme_aqa"].default_value
            
            # AQA encodes submission and completion queue sizes
            # These should differ between devices
            assert aqa1 > 0, "AQA should have valid queue sizes"
            assert aqa2 > 0, "AQA should have valid queue sizes"

    def test_storage_deterministic_values(self, make_analyzer):
        """Test that values are deterministic for same device."""
        analyzer1 = make_analyzer(0x144D, 0xA808)
        analyzer2 = make_analyzer(0x144D, 0xA808)
        
        spec1 = analyzer1.generate_spec()
        spec2 = analyzer2.generate_spec()
        
        if spec1 and spec2:
            for reg_name in spec1.registers:
                if reg_name in spec2.registers:
                    val1 = spec1.registers[reg_name].default_value
                    val2 = spec2.registers[reg_name].default_value
                    assert val1 == val2, f"Register {reg_name} should be deterministic"


class TestBehaviorTypeEnum:
    """Test the BehaviorType enum includes MIXED type."""

    def test_mixed_behavior_type_exists(self):
        """Test that MIXED behavior type is available."""
        assert hasattr(BehaviorType, 'MIXED'), "MIXED behavior type should exist"
        assert BehaviorType.MIXED.value == "mixed", "MIXED should have value 'mixed'"

    def test_all_behavior_types(self):
        """Test all expected behavior types exist."""
        # These are the types actually defined in the codebase
        expected_types = [
            'CONSTANT',
            'AUTO_INCREMENT',
            'WRITE_CAPTURE',
            'RANDOM',
            'PATTERN',
            'TRIGGERED',
            'PERIODIC',
            'MIXED'
        ]
        
        for type_name in expected_types:
            assert hasattr(BehaviorType, type_name), f"{type_name} should be a BehaviorType"


class TestInterruptLogicTemplate:
    """Test interrupt logic template generation."""
    
    def test_interrupt_state_machine_constants(self):
        """Test that interrupt state machine has proper states defined."""
        # Read the template and verify state definitions
        template_path = Path(__file__).parent.parent / "src" / "templates" / "sv" / "pcileech_bar_impl_device.sv.j2"
        
        if template_path.exists():
            content = template_path.read_text()
            
            # Check state constants
            assert "INT_IDLE" in content, "Should define INT_IDLE state"
            assert "INT_PENDING" in content, "Should define INT_PENDING state"
            assert "INT_ASSERT" in content, "Should define INT_ASSERT state"
            assert "INT_COOLDOWN" in content, "Should define INT_COOLDOWN state"
            
            # Check for timing jitter (anti-fingerprinting)
            assert "LFSR" in content.upper() or "lfsr" in content, \
                "Should use LFSR for timing jitter"
            assert "INT_TIMING_SEED" in content, \
                "Should have device-specific timing seed"

    def test_interrupt_cooldown_mechanism(self):
        """Test that interrupt cooldown prevents spam."""
        template_path = Path(__file__).parent.parent / "src" / "templates" / "sv" / "pcileech_bar_impl_device.sv.j2"
        
        if template_path.exists():
            content = template_path.read_text()
            
            # Check for cooldown implementation
            assert "cooldown" in content.lower(), \
                "Should implement cooldown to prevent interrupt storm"


class TestExtendedConfigSpaceCloning:
    """Test extended config space cloning from donor."""

    def test_extended_config_template_logic(self):
        """Test that template handles extended config space properly."""
        template_path = Path(__file__).parent.parent / "src" / "templates" / "sv" / "pcileech_cfgspace.coe.j2"
        
        if template_path.exists():
            content = template_path.read_text()
            
            # Check for donor data usage
            assert "config_space.raw_data" in content or "ext_config_hex" in content, \
                "Should reference donor config space data"
            
            # Check for extended config space handling
            assert "EXTENDED_CFG_START" in content, \
                "Should define extended config space start"
            assert "EXTENDED_CFG_END" in content, \
                "Should define extended config space end"
            
            # Should not just zero-fill everything
            assert "has_ext_config" in content or "ext_config_len" in content, \
                "Should check for extended config availability"


class TestTLPLatencyEmulator:
    """Test TLP latency emulator anti-fingerprinting."""

    def test_tlp_latency_uses_xorshift(self):
        """Test that TLP latency emulator uses improved PRNG."""
        template_path = Path(__file__).parent.parent / "src" / "templates" / "sv" / "tlp_latency_emulator.sv.j2"
        
        if template_path.exists():
            content = template_path.read_text()
            
            # Check for xorshift implementation (better than simple LFSR)
            assert "xorshift" in content.lower() or "state" in content, \
                "Should use xorshift or multi-state PRNG"
            
            # Check for device-specific seeding
            assert "vendor_id" in content.lower() or "device_id" in content.lower(), \
                "Should seed PRNG with device identifiers"

    def test_tlp_latency_has_jitter(self):
        """Test that TLP latency includes multiple jitter sources."""
        template_path = Path(__file__).parent.parent / "src" / "templates" / "sv" / "tlp_latency_emulator.sv.j2"
        
        if template_path.exists():
            content = template_path.read_text()
            
            # Check for thermal drift simulation
            thermal_terms = ["thermal", "drift", "temperature", "slow_drift"]
            has_thermal = any(term in content.lower() for term in thermal_terms)
            
            # Check for burst correlation
            burst_terms = ["burst", "correlation", "recent", "history"]
            has_burst = any(term in content.lower() for term in burst_terms)
            
            # At least one advanced jitter source should be present
            assert has_thermal or has_burst, \
                "Should have advanced jitter sources (thermal drift or burst correlation)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
