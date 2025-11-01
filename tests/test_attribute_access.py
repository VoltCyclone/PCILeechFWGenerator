"""Tests for the attribute access utility module."""

import pytest

from src.utils.attribute_access import (copy_attrs, get_attr_or_raise,
                                        get_multiple_attrs, has_attr,
                                        require_attrs, safe_get_attr,
                                        safe_get_nested)


class MockObject:
    """Mock object with attributes for testing."""

    def __init__(self):
        self.foo = "bar"
        self.num = 42
        self.nested = NestedObject()


class NestedObject:
    """Nested object for testing nested attribute access."""

    def __init__(self):
        self.value = "nested_value"
        self.deep = {"level": 3}


class TestSafeGetAttr:
    """Tests for safe_get_attr function."""

    def test_dict_access(self):
        """Test accessing attributes from a dictionary."""
        d = {"foo": "bar", "num": 42}
        assert safe_get_attr(d, "foo") == "bar"
        assert safe_get_attr(d, "num") == 42
        assert safe_get_attr(d, "missing") is None
        assert safe_get_attr(d, "missing", "default") == "default"

    def test_object_access(self):
        """Test accessing attributes from an object."""
        obj = MockObject()
        assert safe_get_attr(obj, "foo") == "bar"
        assert safe_get_attr(obj, "num") == 42
        assert safe_get_attr(obj, "missing") is None
        assert safe_get_attr(obj, "missing", "default") == "default"

    def test_mixed_types(self):
        """Test with various data types."""
        # Test with None
        assert safe_get_attr(None, "attr", "default") == "default"

        # Test with list (no attributes)
        lst = [1, 2, 3]
        assert safe_get_attr(lst, "missing", "default") == "default"


class TestHasAttr:
    """Tests for has_attr function."""

    def test_dict_has_attr(self):
        """Test checking attributes in a dictionary."""
        d = {"foo": "bar", "num": 42, "none_val": None}
        assert has_attr(d, "foo") is True
        assert has_attr(d, "num") is True
        assert has_attr(d, "none_val") is True  # Key exists even if value is None
        assert has_attr(d, "missing") is False

    def test_object_has_attr(self):
        """Test checking attributes in an object."""
        obj = MockObject()
        assert has_attr(obj, "foo") is True
        assert has_attr(obj, "num") is True
        assert has_attr(obj, "nested") is True
        assert has_attr(obj, "missing") is False


class TestGetAttrOrRaise:
    """Tests for get_attr_or_raise function."""

    def test_dict_get_or_raise(self):
        """Test getting attributes from dict with error on missing."""
        d = {"foo": "bar", "num": 42}
        assert get_attr_or_raise(d, "foo") == "bar"
        assert get_attr_or_raise(d, "num") == 42

        with pytest.raises(AttributeError) as exc_info:
            get_attr_or_raise(d, "missing")
        assert "missing" in str(exc_info.value)
        assert "dictionary" in str(exc_info.value)

    def test_object_get_or_raise(self):
        """Test getting attributes from object with error on missing."""
        obj = MockObject()
        assert get_attr_or_raise(obj, "foo") == "bar"
        assert get_attr_or_raise(obj, "num") == 42

        with pytest.raises(AttributeError) as exc_info:
            get_attr_or_raise(obj, "missing")
        assert "missing" in str(exc_info.value)
        assert "MockObject" in str(exc_info.value)

    def test_custom_error_message(self):
        """Test with custom error message."""
        d = {"foo": "bar"}
        with pytest.raises(AttributeError) as exc_info:
            get_attr_or_raise(d, "missing", "Custom error: attribute not found")
        assert str(exc_info.value) == "Custom error: attribute not found"


class TestSafeGetNested:
    """Tests for safe_get_nested function."""

    def test_nested_dict_access(self):
        """Test accessing nested attributes in dictionaries."""
        d = {"level1": {"level2": {"value": 42, "name": "test"}}}
        assert safe_get_nested(d, "level1", "level2", "value") == 42
        assert safe_get_nested(d, "level1", "level2", "name") == "test"
        assert safe_get_nested(d, "level1", "missing", "value") is None
        assert (
            safe_get_nested(d, "level1", "missing", "value", default="default")
            == "default"
        )

    def test_nested_object_access(self):
        """Test accessing nested attributes in objects."""
        obj = MockObject()
        assert safe_get_nested(obj, "nested", "value") == "nested_value"
        assert safe_get_nested(obj, "nested", "deep", "level") == 3
        assert safe_get_nested(obj, "nested", "missing") is None
        assert safe_get_nested(obj, "nested", "missing", default="default") == "default"

    def test_mixed_nested_access(self):
        """Test accessing nested attributes in mixed dict/object structures."""
        d = {"obj": MockObject()}
        assert safe_get_nested(d, "obj", "foo") == "bar"
        assert safe_get_nested(d, "obj", "nested", "value") == "nested_value"

        obj = MockObject()
        obj.data = {"key": "value"}
        assert safe_get_nested(obj, "data", "key") == "value"


class TestGetMultipleAttrs:
    """Tests for get_multiple_attrs function."""

    def test_get_multiple_from_dict(self):
        """Test getting multiple attributes from a dictionary."""
        d = {"foo": "bar", "num": 42, "flag": True}
        result = get_multiple_attrs(d, "foo", "num", "missing")
        assert result == {"foo": "bar", "num": 42, "missing": None}

    def test_get_multiple_from_object(self):
        """Test getting multiple attributes from an object."""
        obj = MockObject()
        result = get_multiple_attrs(obj, "foo", "num", "missing")
        assert result == {"foo": "bar", "num": 42, "missing": None}

    def test_get_multiple_with_defaults(self):
        """Test getting multiple attributes with default values."""
        d = {"foo": "bar"}
        defaults = {"missing1": "default1", "missing2": "default2"}
        result = get_multiple_attrs(d, "foo", "missing1", "missing2", defaults=defaults)
        assert result == {"foo": "bar", "missing1": "default1", "missing2": "default2"}


class TestRequireAttrs:
    """Tests for require_attrs function."""

    def test_require_all_present(self):
        """Test requiring attributes when all are present."""
        d = {"foo": "bar", "num": 42}
        result = require_attrs(d, "foo", "num")
        assert result == {"foo": "bar", "num": 42}

        obj = MockObject()
        result = require_attrs(obj, "foo", "num")
        assert result == {"foo": "bar", "num": 42}

    def test_require_missing_single(self):
        """Test requiring attributes when one is missing."""
        d = {"foo": "bar"}
        with pytest.raises(AttributeError) as exc_info:
            require_attrs(d, "foo", "missing")
        assert "Missing required attribute: 'missing'" in str(exc_info.value)

    def test_require_missing_multiple(self):
        """Test requiring attributes when multiple are missing."""
        d = {"foo": "bar"}
        with pytest.raises(AttributeError) as exc_info:
            require_attrs(d, "foo", "missing1", "missing2")
        assert "Missing required attributes:" in str(exc_info.value)
        assert "'missing1'" in str(exc_info.value)
        assert "'missing2'" in str(exc_info.value)

    def test_require_with_prefix(self):
        """Test requiring attributes with error prefix."""
        d = {"foo": "bar"}
        with pytest.raises(AttributeError) as exc_info:
            require_attrs(d, "missing", error_prefix="Config validation")
        assert "Config validation: Missing required attribute: 'missing'" in str(
            exc_info.value
        )


class TestCopyAttrs:
    """Tests for copy_attrs function."""

    def test_copy_to_dict(self):
        """Test copying attributes to a dictionary."""
        source = {"foo": "bar", "num": 42}
        target = {}
        copy_attrs(source, target, "foo", "num")
        assert target == {"foo": "bar", "num": 42}

    def test_copy_to_object(self):
        """Test copying attributes to an object."""
        source = {"foo": "bar", "num": 42}
        target = type("Target", (), {})()
        copy_attrs(source, target, "foo", "num")
        assert target.foo == "bar"
        assert target.num == 42

    def test_copy_from_object(self):
        """Test copying attributes from an object."""
        source = MockObject()
        target = {}
        copy_attrs(source, target, "foo", "num")
        assert target == {"foo": "bar", "num": 42}

    def test_copy_skip_missing(self):
        """Test copying with skip_missing=True."""
        source = {"foo": "bar"}
        target = {}
        copy_attrs(source, target, "foo", "missing", skip_missing=True)
        assert target == {"foo": "bar"}

    def test_copy_error_on_missing(self):
        """Test copying with error on missing attribute."""
        source = {"foo": "bar"}
        target = {}
        with pytest.raises(AttributeError) as exc_info:
            copy_attrs(source, target, "foo", "missing", skip_missing=False)
        assert "Source missing attribute: 'missing'" in str(exc_info.value)


class TestRealWorldUsage:
    """Tests demonstrating real-world usage patterns from the codebase."""

    def test_behavior_profile_access(self):
        """Test pattern from systemverilog_generator.py."""
        # Simulate behavior profile as dict
        behavior_profile_dict = {
            "register_accesses": [
                {"register": "STATUS", "offset": 0x10, "operation": "read"},
                {"register": "CONTROL", "offset": 0x14, "operation": "write"},
            ],
            "variance_metadata": {"model": "standard"},
        }

        # Access register_accesses
        register_accesses = safe_get_attr(
            behavior_profile_dict, "register_accesses", []
        )
        assert len(register_accesses) == 2

        # Access variance metadata
        variance_model = safe_get_attr(behavior_profile_dict, "variance_metadata", None)
        assert variance_model["model"] == "standard"

        # Process register accesses
        for access in register_accesses:
            assert has_attr(access, "register")
            reg_name = safe_get_attr(access, "register")
            offset = safe_get_attr(access, "offset", None)
            operation = safe_get_attr(access, "operation", None)

            assert reg_name in ["STATUS", "CONTROL"]
            assert offset is not None
            assert operation in ["read", "write"]

    def test_bar_data_access(self):
        """Test pattern from pcileech_context.py."""
        # Simulate BAR data as dict
        bar_data = {
            "type": "memory",
            "size": 4096,
            "address": 0xF0000000,
            "prefetchable": True,
            "64bit": False,
        }

        # Access BAR properties
        bar_type = safe_get_attr(bar_data, "type", "unknown")
        bar_size = safe_get_attr(bar_data, "size", 0)
        bar_address = safe_get_attr(bar_data, "address", 0)
        bar_prefetchable = safe_get_attr(bar_data, "prefetchable", False)
        bar_64bit = safe_get_attr(bar_data, "64bit", False)

        assert bar_type == "memory"
        assert bar_size == 4096
        assert bar_address == 0xF0000000
        assert bar_prefetchable is True
        assert bar_64bit is False

        # Check for optional attributes
        if has_attr(bar_data, "type") and safe_get_attr(bar_data, "size", 0) > 0:
            assert bar_type == "memory" and bar_size > 0
