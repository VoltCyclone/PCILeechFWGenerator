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
