#!/usr/bin/env python3
"""Test suite for device signature validation."""

import pytest

from pcileechfwgenerator.templating.device_signature_validator import (
    ensure_valid_device_signature, validate_device_signature)


class TestDeviceSignatureValidator:
    """Test suite for device signature validation functionality."""

    @pytest.mark.parametrize(
        "signature,expected",
        [
            ("32'h12345678", True),
            ("'h12345678", True),
            ("0x12345678", True),
            ("1234:5678", True),
            ("1234:5678:01", True),
            ("", False),
            (None, False),
            (123, False),
            ("invalid", False),
            ("h12345678", False),  # Missing quote
        ],
    )
    def test_validate_device_signature(self, signature, expected):
        """Test the device signature validator with various inputs."""
        is_valid, _ = validate_device_signature(signature)
        assert is_valid == expected

    def test_ensure_valid_device_signature_missing(self):
        """Test that missing device_signature raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            ensure_valid_device_signature({})
        assert "missing from template context" in str(exc_info.value)

    def test_ensure_valid_device_signature_none(self):
        """Test that None device_signature raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            ensure_valid_device_signature({"device_signature": None})
        assert "Invalid device_signature" in str(exc_info.value)

    def test_ensure_valid_device_signature_empty(self):
        """Test that empty device_signature raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            ensure_valid_device_signature({"device_signature": ""})
        assert "Invalid device_signature" in str(exc_info.value)

    def test_ensure_valid_device_signature_invalid_format(self):
        """Test that invalid format device_signature raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            ensure_valid_device_signature({"device_signature": "invalid"})
        assert "Invalid device_signature" in str(exc_info.value)

    def test_ensure_valid_device_signature_valid(self):
        """Test that valid device_signature doesn't raise an error."""
        # Should not raise any exception
        ensure_valid_device_signature({"device_signature": "32'h12345678"})
        ensure_valid_device_signature({"device_signature": "1234:5678"})
