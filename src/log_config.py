#!/usr/bin/env python3
"""Centralized logging setup with color support."""

import logging
import os
import re
import sys

from typing import Optional


# ANSI escape sequence pattern for stripping color codes from file logs
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _StripAnsiFilter(logging.Filter):
    """Filter that strips ANSI color codes from log messages for file output."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _ANSI_RE.sub("", record.msg)
        return True


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = "generate.log",
    *,
    force: bool = False,
) -> None:
    """Setup logging with color support using colorlog.

    Args:
        level: Logging level (default: INFO)
        log_file: Optional log file path (default: generate.log)
        force: If True, reconfigure even if already set up (default: False)
    
    Note:
        Console output uses a minimal formatter since string_utils.py handles
        timestamp/level formatting. File output includes full context.
        
        Environment variables:
        - LOG_LEVEL: Override the level parameter (accepts int or name like "DEBUG")
        - LOG_FILE: Override the log_file parameter
    """
    # Respect prior setup unless force=True
    root_logger = logging.getLogger()
    if getattr(root_logger, "_myapp_logging_configured", False) and not force:
        # Allow runtime level bumps without rebuilding handlers
        root_logger.setLevel(level)
        return
    
    # Clear any existing handlers to avoid conflicts
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Env overrides
    env_level = os.getenv("LOG_LEVEL")
    if env_level:
        if env_level.isdigit():
            level = int(env_level)
        else:
            level = getattr(logging, env_level.upper(), level)
    
    log_file = os.getenv("LOG_FILE", log_file)
    
    handlers = []

    # Console handler with minimal formatting
    # string_utils.py safe_log_format() already adds timestamp, level, and prefix
    console_handler = logging.StreamHandler(sys.stdout)
    
    # Use simple formatter that just outputs the message
    # Color is handled by string_utils.py format_padded_message()
    console_formatter = logging.Formatter("%(message)s")

    console_handler.setFormatter(console_formatter)
    handlers.append(console_handler)

    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(
            log_file, mode="a", encoding="utf-8", delay=True
        )
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        file_handler.addFilter(_StripAnsiFilter())
        handlers.append(file_handler)

    # Configure root logger
    root_logger.setLevel(level)
    for handler in handlers:
        root_logger.addHandler(handler)

    # Suppress noisy loggers
    for noisy in ("urllib3", "requests", "botocore", "boto3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    
    root_logger._myapp_logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
