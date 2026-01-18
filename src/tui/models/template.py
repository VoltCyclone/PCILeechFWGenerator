"""
Template model classes for PCILeechFWGenerator.
"""

from typing import List, Optional


class TemplateOption:
    """Represents a configurable template option."""

    def __init__(
        self,
        name: str,
        description: str,
        default_value: str = "",
        options: Optional[List[str]] = None,
        option_type: str = "string",
        required: bool = False,
    ) -> None:
        """
        Initialize a template option.

        Args:
            name: The name of the option
            description: A description of what the option does
            default_value: The default value for the option
            options: A list of possible values for select-type options
            option_type: The data type of the option (string, int, bool, select)
            required: Whether this option is required
        """
        self.name = name
        self.description = description
        self.default_value = default_value
        self.options = options or []
        self.option_type = option_type
        self.required = required


class Template:
    """Represents a device firmware template."""

    def __init__(
        self,
        name: str,
        description: str,
        path: str,
        options: Optional[List[TemplateOption]] = None,
        compatibility: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize a template.

        Args:
            name: The template name
            description: A description of the template
            path: The file path to the template
            options: A list of configurable options for this template
            compatibility: A list of compatible device IDs
        """
        self.name = name
        self.description = description
        self.path = path
        self.options = options or []
        self.compatibility = compatibility or []

    def get_option(self, name: str) -> Optional[TemplateOption]:
        """
        Get a template option by name.

        Args:
            name: The name of the option to retrieve

        Returns:
            The template option if found, None otherwise
        """
        for option in self.options:
            if option.name == name:
                return option
        return None

    def is_compatible_with(self, device_id: str) -> bool:
        """
        Check if this template is compatible with the given device ID.

        Args:
            device_id: The device ID to check compatibility with

        Returns:
            True if compatible, False otherwise
        """
        if not self.compatibility:
            # If no compatibility list is specified, assume compatible with all
            return True

        return device_id in self.compatibility
