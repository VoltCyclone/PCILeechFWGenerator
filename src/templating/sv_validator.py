"""Validation module for SystemVerilog generation.

Enforces strict donor-identity usage when requested and validates presence of
subsystem IDs, class code, revision, and donor artifacts (VPD/Option ROM).
"""

import hashlib

import logging

from typing import Any, Dict, List, Optional, Union

from src.string_utils import (
    log_error_safe,
    log_info_safe,
    safe_format,
)

from src.utils.context_error_messages import (
    OPTION_ROM_MISSING_SIZE,
    ROM_SIZE_MISMATCH,
    VPD_REQUIRED_MISSING,
)

from src.utils.validation_constants import (
    DEVICE_ID_FIELD_WIDTHS,
    DEVICE_IDENTIFICATION_FIELDS,
)

from .sv_constants import SV_CONSTANTS, SV_VALIDATION

from .template_renderer import TemplateRenderError


class SVValidator:
    """Handles validation for SystemVerilog generation."""

    def __init__(self, logger: logging.Logger):
        """Initialize the validator with a logger."""
        self.logger = logger
        self.constants = SV_CONSTANTS
        self.messages = SV_VALIDATION.ERROR_MESSAGES

    def validate_device_config(self, device_config: Any) -> None:
        """
        Validate device configuration for safe firmware generation.

        Args:
            device_config: Device configuration object

        Raises:
            ValueError: If device configuration is invalid
        """
        if not device_config:
            raise ValueError(self.messages["missing_device_config"])

        # Validate device type and class
        self._validate_device_enums(device_config)

        # Validate numeric parameters
        self._validate_numeric_params(device_config)

    def validate_template_context(self, context: Any) -> None:
        """
        Validate template context before processing.

        Args:
            context: Template context to validate

        Raises:
            ValueError: If context is invalid
            TemplateRenderError: If critical fields are missing
        """
        if not context:
            raise ValueError(
                self.messages["no_template_context"].format(
                    operation="template rendering"
                )
            )

        if not isinstance(context, dict):
            raise ValueError(
                self.messages["context_not_dict"].format(
                    type_name=type(context).__name__
                )
            )

        # Validate critical fields
        self._validate_critical_fields(context)

        # Always validate device identification fields
        try:
            device_config = (
                context.get("device_config")
                if isinstance(context, dict)
                else context.device_config
            )
        except Exception:
            device_config = None
        if device_config is not None:
            self.validate_device_identification(
                device_config
                if isinstance(device_config, dict)
                else device_config.to_dict()  # type: ignore[attr-defined]
            )

        # Validate donor artifacts (VPD/Option ROM) when signaled
        self._validate_donor_artifacts(context)

    def validate_device_identification(self, device_config: Dict[str, Any]) -> None:
        """
        Validate critical device identification fields.

        Args:
            device_config: Device configuration dictionary

        Raises:
            TemplateRenderError: If validation fails
        """
        # Require full set of identifiers (avoid silent fallbacks)
        required_fields = DEVICE_IDENTIFICATION_FIELDS
        missing_fields: List[str] = []
        invalid_fields: List[str] = []

        for field in required_fields:
            value = device_config.get(field)
            if not value:
                missing_fields.append(field)
            else:
                # Validate hex-widths using centralized constants
                expected_width = DEVICE_ID_FIELD_WIDTHS.get(field)
                if expected_width is not None:
                    if not isinstance(value, str) or len(value) != expected_width:
                        invalid_fields.append(
                            safe_format(
                                "{field}='{value}' (must be {expected_width}-character hex string)",
                                field=field,
                                value=value,
                                expected_width=expected_width,
                            )
                        )

        # CRITICAL: Check for incorrect class_code defaults
        class_code = device_config.get("class_code", "")
        if class_code == "020000":
            # Warn if using Ethernet class code - might be incorrect fallback
            log_error_safe(
                self.logger,
                "Device has class_code=020000 (Ethernet Controller). "
                "If the source device is NOT an Ethernet controller, this indicates "
                "a fallback value was used instead of reading from hardware. "
                "This will cause incorrect device enumeration in Windows/Linux.",
                prefix="VALID",
            )

        if missing_fields or invalid_fields:
            error_details: List[str] = []
            if missing_fields:
                error_details.append(f"Missing fields: {', '.join(missing_fields)}")
            if invalid_fields:
                error_details.append(f"Invalid fields: {', '.join(invalid_fields)}")

            details = "; ".join(error_details)
            error_msg = safe_format(
                "Critical device identification validation failed: {details}. Cannot generate safe firmware without proper device identification.",
                details=details,
            )
            log_error_safe(self.logger, error_msg, prefix="VALID")
            raise TemplateRenderError(error_msg)

    def _validate_donor_artifacts(self, context: Any) -> None:
        """
        Validate VPD and Option ROM donor artifacts when required.

        Rules:
        - If requires_vpd is True, require non-empty vpd_data bytes/hex.
        - If has_option_rom is True, require ROM_SIZE > 0. If rom_data is provided,
          ensure its length matches ROM_SIZE and compute checksum when possible.
        """

        # Normalize access to context attributes/keys

        def _get(name: str, default: Any = None) -> Any:
            if isinstance(context, dict):
                return context.get(name, default)
            try:
                return getattr(context, name)
            except Exception:
                return default

        # VPD checks
        requires_vpd = bool(_get("requires_vpd", False))
        vpd_data = _get("vpd_data")
        if requires_vpd:
            if vpd_data is None or (
                hasattr(vpd_data, "__len__") and len(vpd_data) == 0
            ):
                error_msg = VPD_REQUIRED_MISSING
                log_error_safe(self.logger, error_msg, prefix="VALID")
                raise TemplateRenderError(error_msg)

        # Option ROM checks
        has_option_rom = False
        device_cfg = _get("device_config")
        if device_cfg is not None:
            try:
                # Support dict or TemplateObject
                has_option_rom = bool(
                    device_cfg.get("has_option_rom")
                    if isinstance(device_cfg, dict)
                    else getattr(device_cfg, "has_option_rom", False)
                )
            except Exception:
                has_option_rom = False
        # Allow top-level alias override
        has_option_rom = bool(_get("has_option_rom", has_option_rom))

        if has_option_rom:
            rom_size = _get("ROM_SIZE", None)
            if not isinstance(rom_size, int) or rom_size <= 0:
                error_msg = OPTION_ROM_MISSING_SIZE
                log_error_safe(self.logger, error_msg)
                raise TemplateRenderError(error_msg)

            rom_data = _get("rom_data", None)
            if rom_data is not None:
                try:
                    data_len = len(rom_data)
                except Exception:
                    data_len = None
                if data_len is None or data_len != rom_size:
                    error_msg = safe_format(
                        ROM_SIZE_MISMATCH,
                        size=rom_size,
                        dlen=(data_len if data_len is not None else -1),
                    )
                    log_error_safe(self.logger, error_msg, prefix="VALID")
                    raise TemplateRenderError(error_msg)

                # Compute checksum if not present and attach for downstream consumers
                rom_checksum = _get("rom_checksum", None)
                if rom_checksum is not None:
                    return

                try:
                    digest = self._compute_rom_digest(rom_data)
                    if digest:
                        log_info_safe(
                            self.logger,
                            safe_format(
                                "Computed ROM checksum: {csum}",
                                csum=digest[:16],
                            ),
                            prefix="VALID",
                        )
                except Exception:
                    log_error_safe(
                        self.logger,
                        "Failed to compute ROM checksum",
                        prefix="VALID",
                    )

    def _compute_rom_digest(
        self, rom_data: Union[bytes, bytearray, str, Any]
    ) -> Optional[str]:
        """
        Compute SHA256 digest of ROM data.

        Args:
            rom_data: ROM data as bytes, bytearray, or hex string

        Returns:
            SHA256 hex digest or None if type unsupported
        """
        if isinstance(rom_data, (bytes, bytearray)):
            return hashlib.sha256(rom_data).hexdigest()
        if isinstance(rom_data, str):
            # If provided as hex string
            return hashlib.sha256(bytes.fromhex(rom_data)).hexdigest()
        return None

    def _validate_numeric_range(
        self,
        param_name: str,
        value: Union[int, float],
        min_value: Union[int, float],
        max_value: Union[int, float],
    ) -> Optional[str]:
        """
        Validate a numeric parameter against a range (internal helper).

        Args:
            param_name: Name of the parameter
            value: Value to validate
            min_value: Minimum allowed value
            max_value: Maximum allowed value

        Returns:
            Error message if validation fails, None otherwise
        """
        if not isinstance(value, (int, float)):
            return f"{param_name} must be a number, got {type(value).__name__}"

        if value < min_value or value > max_value:
            return self.messages["invalid_numeric_param"].format(
                param=param_name, value=value, min=min_value, max=max_value
            )

        return None

    def _validate_device_enums(self, device_config: Any) -> None:
        """Validate device type and class enums."""
        if not hasattr(device_config.device_type, "value"):
            raise ValueError(
                self.messages["invalid_device_type"].format(
                    value=device_config.device_type
                )
            )

        if not hasattr(device_config.device_class, "value"):
            raise ValueError(
                self.messages["invalid_device_class"].format(
                    value=device_config.device_class
                )
            )

    def _validate_numeric_params(self, device_config: Any) -> None:
        """Validate numeric parameters of device configuration."""
        validations = [
            (
                "max_payload_size",
                device_config.max_payload_size,
                self.constants.MIN_PAYLOAD_SIZE,
                self.constants.MAX_PAYLOAD_SIZE,
            ),
            (
                "max_read_request_size",
                device_config.max_read_request_size,
                self.constants.MIN_READ_REQUEST_SIZE,
                self.constants.MAX_READ_REQUEST_SIZE,
            ),
            (
                "tx_queue_depth",
                device_config.tx_queue_depth,
                self.constants.MIN_QUEUE_DEPTH,
                self.constants.MAX_QUEUE_DEPTH,
            ),
            (
                "rx_queue_depth",
                device_config.rx_queue_depth,
                self.constants.MIN_QUEUE_DEPTH,
                self.constants.MAX_QUEUE_DEPTH,
            ),
        ]

        for param_name, value, min_val, max_val in validations:
            error = self._validate_numeric_range(param_name, value, min_val, max_val)
            if error:
                raise ValueError(error)

    def _validate_critical_fields(self, context: Dict[str, Any]) -> None:
        """Validate critical fields in template context."""
        # Check for device_config
        if "device_config" not in context:
            raise TemplateRenderError(self.messages["missing_critical_field"])

        device_config = context["device_config"]
        # Accept both dict and TemplateObject types for device_config
        from src.utils.unified_context import TemplateObject

        if not isinstance(device_config, (dict, TemplateObject)):
            raise TemplateRenderError(
                self.messages["device_config_not_dict"].format(
                    type_name=type(device_config).__name__
                )
            )

        # Check for device_signature
        if "device_signature" not in context:
            raise TemplateRenderError(self.messages["missing_device_signature"])

        if not context["device_signature"]:
            raise TemplateRenderError(self.messages["empty_device_signature"])
