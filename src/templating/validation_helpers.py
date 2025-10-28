#!/usr/bin/env python3
"""
Shared validation helpers for templating system.

Consolidates duplicate validation logic from multiple generator modules.
"""

import logging
from typing import Any, Dict

from src.exceptions import TemplateRenderError

from src.string_utils import log_error_safe, safe_format


def validate_device_identifiers(
    context: Dict[str, Any],
    logger: logging.Logger,
    prefix: str = "VALIDATION"
) -> None:
    """
    Validate that required device identifiers are present in context.
    
    This validation is used across multiple generators (overlay, module, etc.)
    to ensure donor-bound device IDs are present.
    
    Args:
        context: Template context to validate
        logger: Logger instance for error reporting
        prefix: Logging prefix
        
    Raises:
        TemplateRenderError: If required identifiers are missing
    """
    device_cfg = context.get("device_config") or {}
    device_obj = context.get("device") or {}

    vid = device_obj.get("vendor_id") or device_cfg.get("vendor_id")
    did = device_obj.get("device_id") or device_cfg.get("device_id")

    if not vid or not did:
        error_msg = safe_format(
            "Missing required device identifiers: "
            "vendor_id={vid}, device_id={did}",
            vid=str(vid) if vid else "MISSING",
            did=str(did) if did else "MISSING",
        )
        log_error_safe(logger, error_msg, prefix=prefix)
        raise TemplateRenderError(error_msg)


def validate_config_space(
    context: Dict[str, Any],
    logger: logging.Logger,
    prefix: str = "VALIDATION"
) -> None:
    """
    Validate that config_space data is present in context.
    
    Args:
        context: Template context to validate
        logger: Logger instance for error reporting
        prefix: Logging prefix
        
    Raises:
        TemplateRenderError: If config_space is missing
    """
    config_space = context.get("config_space")
    if not config_space:
        error_msg = "Missing required config_space in context"
        log_error_safe(logger, error_msg, prefix=prefix)
        raise TemplateRenderError(error_msg)


def validate_template_context(
    context: Dict[str, Any],
    logger: logging.Logger,
    prefix: str = "VALIDATION",
    require_config_space: bool = True
) -> None:
    """
    Comprehensive validation of template context.
    
    Combines device identifier and config space validation in one call.
    
    Args:
        context: Template context to validate
        logger: Logger instance for error reporting
        prefix: Logging prefix
        require_config_space: Whether to validate config_space presence
        
    Raises:
        TemplateRenderError: If required fields are missing
    """
    validate_device_identifiers(context, logger, prefix)
    if require_config_space:
        validate_config_space(context, logger, prefix)
