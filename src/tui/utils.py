"""TUI utility functions."""

import re
from typing import Optional, List, Tuple, Dict, Any
import logging
from pathlib import Path

from src.device_clone.hex_formatter import HexFormatter
from src.utils.validators import get_bdf_validator

# Configure logger
logger = logging.getLogger(__name__)


class TUIUtils:
    """Utility functions for TUI operations."""

    @staticmethod
    def format_device_info(device: Dict[str, Any]) -> str:
        """
        Format device information for display.

        Args:
            device: Device information dictionary

        Returns:
            Formatted device string
        """
        vendor = device.get("vendor_name", "Unknown")
        device_name = device.get("device_name", "Unknown")
        vendor_id = device.get("vendor_id", "0000")
        device_id = device.get("device_id", "0000")
        bdf = device.get("bdf", "0000:00:00.0")
        
        return f"{bdf} - {vendor} {device_name} [{vendor_id}:{device_id}]"

    @staticmethod
    def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
        """
        Truncate text to specified length with suffix.

        Args:
            text: Text to truncate
            max_length: Maximum length including suffix
            suffix: Suffix to append when truncating

        Returns:
            Truncated text
        """
        if len(text) <= max_length:
            return text
        
        if max_length <= len(suffix):
            return suffix[:max_length]
        
        return text[:max_length - len(suffix)] + suffix

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """
        Format byte size in human-readable format.

        Args:
            size_bytes: Size in bytes

        Returns:
            Formatted size string
        """
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"

    @staticmethod
    def parse_hex_value(value: str, default: int = 0) -> int:
        """
        Parse hexadecimal value from string.

        Args:
            value: Hex string (with or without 0x prefix)
            default: Default value if parsing fails

        Returns:
            Parsed integer value
        """
        try:
            # Remove 0x prefix if present
            if value.lower().startswith("0x"):
                value = value[2:]
            return int(value, 16)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def format_hex_value(value: int, width: int = 4) -> str:
        """
        Format integer as hex string.

        Args:
            value: Integer value
            width: Width of hex string (padding with zeros)

        Returns:
            Formatted hex string (without 0x prefix)
        """
        return f"{value:0{width}x}"

    @staticmethod
    def split_vendor_device_id(combined_id: str) -> Tuple[str, str]:
        """
        Split combined vendor:device ID string.

        Args:
            combined_id: Combined ID in format "XXXX:XXXX"

        Returns:
            Tuple of (vendor_id, device_id)
        """
        parts = combined_id.split(":")
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return "0000", "0000"

    @staticmethod
    def combine_vendor_device_id(vendor_id: str, device_id: str) -> str:
        """
        Combine vendor and device IDs.

        Args:
            vendor_id: Vendor ID hex string
            device_id: Device ID hex string

        Returns:
            Combined ID string "vendor_id:device_id"
        """
        return f"{vendor_id}:{device_id}"

    @staticmethod
    def parse_bdf(bdf: str) -> Optional[Tuple[int, int, int, int]]:
        """
        Parse BDF string into components.

        Args:
            bdf: BDF string (e.g., "0000:01:00.0")

        Returns:
            Tuple of (domain, bus, device, function) or None if invalid
        """
        # Use the new validator first
        validator = get_bdf_validator()
        result = validator.validate(bdf)
        if not result.valid:
            return None
        
        # Parse the validated BDF
        try:
            # Handle both full and short formats
            parts = bdf.split(":")
            if len(parts) == 2:
                # Short format: XX:XX.X
                domain = 0
                bus = int(parts[0], 16)
                dev_func = parts[1].split(".")
            else:
                # Full format: XXXX:XX:XX.X or XX:XX:XX.X
                domain = int(parts[0], 16)
                bus = int(parts[1], 16)
                dev_func = parts[2].split(".")
            
            device = int(dev_func[0], 16)
            function = int(dev_func[1], 16)
            
            return (domain, bus, device, function)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def format_bdf(domain: int, bus: int, device: int, function: int) -> str:
        """
        Format BDF components into string.

        Args:
            domain: Domain number
            bus: Bus number
            device: Device number
            function: Function number

        Returns:
            Formatted BDF string
        """
        return f"{domain:04x}:{bus:02x}:{device:02x}.{function:x}"

    @staticmethod
    def is_root() -> bool:
        """
        Check if running with root privileges.

        Returns:
            True if running as root
        """
        import os
        return os.geteuid() == 0

    @staticmethod
    def get_config_dir() -> Path:
        """
        Get configuration directory path.

        Returns:
            Path to config directory
        """
        from pathlib import Path
        import os
        
        # Check XDG_CONFIG_HOME first
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            config_dir = Path(xdg_config) / "pcileech"
        else:
            config_dir = Path.home() / ".config" / "pcileech"
        
        # Create directory if it doesn't exist
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    @staticmethod
    def get_cache_dir() -> Path:
        """
        Get cache directory path.

        Returns:
            Path to cache directory
        """
        from pathlib import Path
        import os
        
        # Check XDG_CACHE_HOME first
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            cache_dir = Path(xdg_cache) / "pcileech"
        else:
            cache_dir = Path.home() / ".cache" / "pcileech"
        
        # Create directory if it doesn't exist
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    @staticmethod
    def validate_bdf(bdf: str) -> bool:
        """
        Validate PCI Bus:Device.Function format

        Args:
            bdf: BDF string to validate

        Returns:
            True if valid BDF format, False otherwise
        """
        validator = get_bdf_validator()
        result = validator.validate(bdf)
        return result.valid

    @staticmethod
    def validate_score(score_str: str) -> Optional[float]:
        """
        Validate and parse a suitability score

        Args:
            score_str: Score string to validate

        Returns:
            Parsed score as float if valid, None otherwise
        """
        try:
            score = float(score_str)
            if 0.0 <= score <= 1.0:
                return score
        except ValueError:
            pass
        return None

    @staticmethod
    def format_progress(current: int, total: int, width: int = 20) -> str:
        """
        Format progress bar string.

        Args:
            current: Current progress value
            total: Total value
            width: Width of progress bar

        Returns:
            Formatted progress bar string
        """
        if total == 0:
            percentage = 0
        else:
            percentage = min(100, int(current * 100 / total))
        
        filled = int(width * percentage / 100)
        bar = "█" * filled + "░" * (width - filled)
        
        return f"[{bar}] {percentage}%"

    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        Format duration in human-readable format.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted duration string
        """
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            secs = seconds % 60
            return f"{minutes}m {secs:.0f}s"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m"

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Sanitize filename for filesystem.

        Args:
            filename: Original filename

        Returns:
            Sanitized filename
        """
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, "_")
        
        # Remove leading/trailing dots and spaces
        filename = filename.strip(". ")
        
        # Ensure filename is not empty
        if not filename:
            filename = "unnamed"
        
        return filename

    @staticmethod
    def parse_key_value_pairs(text: str) -> Dict[str, str]:
        """
        Parse key=value pairs from text.

        Args:
            text: Text containing key=value pairs

        Returns:
            Dictionary of parsed pairs
        """
        pairs = {}
        for line in text.strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                pairs[key.strip()] = value.strip()
        return pairs

    @staticmethod
    def format_key_value_table(data: Dict[str, Any], key_width: int = 20) -> List[str]:
        """
        Format dictionary as key-value table.

        Args:
            data: Dictionary to format
            key_width: Width of key column

        Returns:
            List of formatted lines
        """
        lines = []
        for key, value in data.items():
            formatted_key = f"{key}:".ljust(key_width)
            lines.append(f"{formatted_key} {value}")
        return lines

    @staticmethod
    def wrap_text(text: str, width: int, indent: int = 0) -> List[str]:
        """
        Wrap text to specified width.

        Args:
            text: Text to wrap
            width: Maximum line width
            indent: Indentation for wrapped lines

        Returns:
            List of wrapped lines
        """
        import textwrap
        wrapper = textwrap.TextWrapper(
            width=width,
            subsequent_indent=" " * indent,
            break_long_words=False,
            break_on_hyphens=False
        )
        return wrapper.wrap(text)
