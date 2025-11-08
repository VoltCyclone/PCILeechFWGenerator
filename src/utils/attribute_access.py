#!/usr/bin/env python3
"""Utility functions for unified attribute access across dict and object types.

This module provides helper functions to standardize the pattern of accessing
attributes from objects that could be either dictionaries or regular objects
with attributes. This eliminates repetitive isinstance/hasattr checks throughout
the codebase.
"""

import collections.abc
from typing import Any, Optional, TypeVar, Union, cast

T = TypeVar("T")


def safe_get_attr(obj: Any, attr_name: str, default: Optional[T] = None) -> Optional[T]:
    """Safely get an attribute from either a dict or object.

    This function handles both dictionary access (via keys) and object
    attribute access (via getattr), returning the value if found or
    the default if not.

    Args:
        obj: The object to get the attribute from (dict or object)
        attr_name: The name of the attribute/key to retrieve
        default: The default value to return if attribute not found

    Returns:
        The attribute value if found, otherwise the default value

    Examples:
        >>> # Works with dictionaries
        >>> d = {"foo": "bar"}
        >>> safe_get_attr(d, "foo")
        'bar'
        >>> safe_get_attr(d, "missing", "default")
        'default'

        >>> # Works with objects
        >>> class MyObj:
        ...     def __init__(self):
        ...         self.foo = "bar"
        >>> obj = MyObj()
        >>> safe_get_attr(obj, "foo")
        'bar'
        >>> safe_get_attr(obj, "missing", "default")
        'default'
    """
    if isinstance(obj, dict):
        return cast(Optional[T], obj.get(attr_name, default))
    else:
        return cast(Optional[T], getattr(obj, attr_name, default))


def has_attr(obj: Any, attr_name: str) -> bool:
    """Check if an object has an attribute (works for both dict and object).

    Args:
        obj: The object to check (dict or object)
        attr_name: The name of the attribute/key to check for

    Returns:
        True if the attribute exists, False otherwise

    Examples:
        >>> # Works with dictionaries
        >>> d = {"foo": "bar"}
        >>> has_attr(d, "foo")
        True
        >>> has_attr(d, "missing")
        False

        >>> # Works with objects
        >>> class MyObj:
        ...     def __init__(self):
        ...         self.foo = "bar"
        >>> obj = MyObj()
        >>> has_attr(obj, "foo")
        True
        >>> has_attr(obj, "missing")
        False
    """
    if isinstance(obj, dict):
        return attr_name in obj
    else:
        return hasattr(obj, attr_name)


def get_attr_or_raise(
    obj: Any, attr_name: str, error_message: Optional[str] = None
) -> Any:
    """Get an attribute from either a dict or object, raising an error if not found.

    Args:
        obj: The object to get the attribute from (dict or object)
        attr_name: The name of the attribute/key to retrieve
        error_message: Optional custom error message

    Returns:
        The attribute value

    Raises:
        AttributeError: If the attribute is not found

    Examples:
        >>> d = {"foo": "bar"}
        >>> get_attr_or_raise(d, "foo")
        'bar'
        >>> get_attr_or_raise(d, "missing")  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        AttributeError: Attribute 'missing' not found
    """
    if isinstance(obj, dict):
        if attr_name in obj:
            return obj[attr_name]
    else:
        if hasattr(obj, attr_name):
            return getattr(obj, attr_name)

    if error_message is None:
        obj_type = "dictionary" if isinstance(obj, dict) else type(obj).__name__
        error_message = f"Attribute '{attr_name}' not found in {obj_type}"

    raise AttributeError(error_message)


def safe_get_nested(
    obj: Any, *attr_path: str, default: Optional[T] = None
) -> Optional[T]:
    """Safely get a nested attribute from either dicts or objects.

    Traverses a path of attributes through nested dicts/objects,
    returning the final value or default if any step fails.

    Args:
        obj: The root object to start from
        *attr_path: Variable number of attribute names forming the path
        default: The default value to return if any attribute not found

    Returns:
        The nested attribute value if found, otherwise the default value

    Examples:
        >>> # Nested dictionaries
        >>> d = {"level1": {"level2": {"value": 42}}}
        >>> safe_get_nested(d, "level1", "level2", "value")
        42
        >>> safe_get_nested(d, "level1", "missing", "value", default="not found")
        'not found'

        >>> # Mixed dict and object nesting
        >>> class Inner:
        ...     def __init__(self):
        ...         self.value = 42
        >>> d = {"level1": Inner()}
        >>> safe_get_nested(d, "level1", "value")
        42
    """
    current = obj
    for attr in attr_path:
        if current is None:
            return default
        current = safe_get_attr(current, attr, None)
    return cast(Optional[T], current if current is not None else default)


def get_multiple_attrs(
    obj: Any, *attr_names: str, defaults: Optional[dict] = None
) -> dict:
    """Get multiple attributes from an object at once.

    Args:
        obj: The object to get attributes from
        *attr_names: Variable number of attribute names to retrieve
        defaults: Optional dict of default values for specific attributes

    Returns:
        Dictionary mapping attribute names to their values

    Examples:
        >>> d = {"foo": "bar", "num": 42}
        >>> get_multiple_attrs(d, "foo", "num", "missing")
        {'foo': 'bar', 'num': 42, 'missing': None}

        >>> # With defaults
        >>> get_multiple_attrs(d, "foo", "missing", defaults={"missing": "default"})
        {'foo': 'bar', 'missing': 'default'}
    """
    defaults = defaults or {}
    result = {}

    for attr_name in attr_names:
        if not isinstance(attr_name, collections.abc.Hashable):
            raise TypeError(
                f"Attribute name {attr_name!r} is unhashable and cannot be used as a dict key."
            )
        default_value = defaults.get(attr_name, None)
        result[attr_name] = safe_get_attr(obj, attr_name, default_value)

    return result


def require_attrs(
    obj: Any, *attr_names: str, error_prefix: Optional[str] = None
) -> dict:
    """Require that an object has all specified attributes.

    This is useful for validation scenarios where multiple attributes
    must be present.

    Args:
        obj: The object to validate
        *attr_names: Variable number of required attribute names
        error_prefix: Optional prefix for error messages

    Returns:
        Dictionary mapping attribute names to their values

    Raises:
        AttributeError: If any required attribute is missing

    Examples:
        >>> d = {"foo": "bar", "num": 42}
        >>> require_attrs(d, "foo", "num")
        {'foo': 'bar', 'num': 42}

        >>> require_attrs(d, "foo", "missing")  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        AttributeError: Missing required attribute: 'missing'
    """
    result = {}
    missing = []

    for attr_name in attr_names:
        if has_attr(obj, attr_name):
            result[attr_name] = safe_get_attr(obj, attr_name)
        else:
            missing.append(attr_name)

    if missing:
        prefix = f"{error_prefix}: " if error_prefix else ""
        if len(missing) == 1:
            raise AttributeError(
                f"{prefix}Missing required attribute: '{missing[0]}'"
            )
        else:
            attrs_list = ", ".join(f"'{attr}'" for attr in missing)
            raise AttributeError(
                f"{prefix}Missing required attributes: {attrs_list}"
            )

    return result


def copy_attrs(
    source: Any, target: Union[dict, Any], *attr_names: str, skip_missing: bool = False
) -> None:
    """Copy attributes from source to target object.

    Args:
        source: The source object to copy from
        target: The target object to copy to (dict or object)
        *attr_names: Variable number of attribute names to copy
        skip_missing: If True, skip missing attributes instead of raising error

    Raises:
        AttributeError: If skip_missing is False and attribute not found

    Examples:
        >>> source = {"foo": "bar", "num": 42}
        >>> target = {}
        >>> copy_attrs(source, target, "foo", "num")
        >>> target
        {'foo': 'bar', 'num': 42}
    """
    for attr_name in attr_names:
        if has_attr(source, attr_name):
            value = safe_get_attr(source, attr_name)
            if isinstance(target, dict):
                target[attr_name] = value
            else:
                setattr(target, attr_name, value)
        elif not skip_missing:
            raise AttributeError(f"Source missing attribute: '{attr_name}'")
