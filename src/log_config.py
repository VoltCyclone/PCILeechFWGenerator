#!/usr/bin/env python3
"""Centralized logging setup with color support."""

import logging
import sys
from typing import Optional

try:
    from colorlog import ColoredFormatter

    HAS_COLORLOG = True
except ImportError:
    HAS_COLORLOG = False


def setup_logging(
    level: int = logging.INFO, log_file: Optional[str] = "generate.log"
) -> None:
    """Setup logging with color support using colorlog.

    Args:
        level: Logging level (default: INFO)
        log_file: Optional log file path (default: generate.log)
    
    Note:
        Console output uses a minimal formatter since string_utils.py handles
        timestamp/level formatting. File output includes full context.
    """
    # Clear any existing handlers to avoid conflicts
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

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
        file_handler = logging.FileHandler(log_file, mode="w")
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)

    # Configure root logger
    root_logger.setLevel(level)
    for handler in handlers:
        root_logger.addHandler(handler)

    # Suppress noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


class FallbackColoredFormatter(logging.Formatter):
    """Fallback formatter - now deprecated since string_utils handles formatting.
    
    Kept for backwards compatibility but not used by default setup_logging().
    Color formatting is now handled by string_utils.format_padded_message().
    """

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[31;1m",  # Bright Red
    }
    RESET = "\033[0m"

    def format(self, record):
        # Only colorize if outputting to a terminal
        levelname = record.levelname
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            if levelname in self.COLORS:
                record.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"
                record.msg = f"{self.COLORS[levelname]}{record.msg}{self.RESET}"

        result = super().format(record)

        # Reset the record to avoid affecting other handlers
        record.levelname = levelname
        return result


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
