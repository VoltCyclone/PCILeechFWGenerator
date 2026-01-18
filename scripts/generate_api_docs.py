#!/usr/bin/env python3
"""
Generate API documentation for PCILeech Firmware Generator.

This script generates comprehensive API documentation using Sphinx autodoc
and converts it to Markdown format for integration with MkDocs.
"""

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class APIDocGenerator:
    """Generate API documentation for the project."""

    def __init__(
        self,
        source_dir: Path,
        output_dir: Path,
        project_name: str = "PCILeech Firmware Generator",
        author: str = "Ramsey McGrath",
        version: str = "latest",
    ):
        """
        Initialize the API documentation generator.

        Args:
            source_dir: Path to the source code directory
            output_dir: Path to the output directory for documentation
            project_name: Name of the project
            author: Author name
            version: Project version
        """
        self.source_dir = Path(source_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.project_name = project_name
        self.author = author
        self.version = version

        # Sphinx directories
        self.sphinx_dir = self.output_dir / "_sphinx_build"
        self.sphinx_source = self.sphinx_dir / "source"
        self.sphinx_build = self.sphinx_dir / "build"

        # Final output directory for MkDocs
        self.mkdocs_api_dir = self.output_dir / "api"

    def create_sphinx_config(self) -> None:
        """Create Sphinx configuration file."""
        conf_content = f"""
# Configuration file for the Sphinx documentation builder.
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path("{self.source_dir.parent}").resolve()
sys.path.insert(0, str(project_root))

# Project information
project = "{self.project_name}"
copyright = "2025, {self.author}"
author = "{self.author}"
version = "{self.version}"
release = version

# Extensions
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx.ext.todo',
    'sphinx.ext.coverage',
    'sphinx.ext.githubpages',
    'sphinx_autodoc_typehints',
    'myst_parser',
]

# Add support for Markdown
source_suffix = {{
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}}

# Master document
master_doc = 'index'

# Autodoc settings
autodoc_default_options = {{
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__',
    'show-inheritance': True,
    'inherited-members': False,
}}

autodoc_typehints = 'description'
autodoc_typehints_format = 'short'
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True

# Intersphinx mapping
intersphinx_mapping = {{
    'python': ('https://docs.python.org/3', None),
    'jinja2': ('https://jinja.palletsprojects.com/en/3.1.x/', None),
    'pyyaml': ('https://pyyaml.org/wiki/PyYAMLDocumentation', None),
}}

# HTML output options (for intermediate processing)
html_theme = 'sphinx_rtd_theme'
html_static_path = []
html_show_sourcelink = False

# Exclude patterns
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# Mock imports for modules that might not be available in CI
autodoc_mock_imports = [
    'textual',
    'rich',
    'click',
    'psutil',
    'setuptools_scm',
]
"""

        self.sphinx_source.mkdir(parents=True, exist_ok=True)
        conf_file = self.sphinx_source / "conf.py"
        conf_file.write_text(conf_content)
        logger.info(f"Created Sphinx configuration at {conf_file}")

    def create_index_rst(self) -> None:
        """Create the main index.rst file for Sphinx."""
        modules = self._discover_modules()

        index_content = f"""
{self.project_name} API Documentation
{"=" * (len(self.project_name) + 18)}

.. toctree::
   :maxdepth: 2
   :caption: API Reference

"""

        # Add module references
        for module in sorted(modules):
            index_content += f"   modules/{module}\n"

        index_content += """

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
"""

        index_file = self.sphinx_source / "index.rst"
        index_file.write_text(index_content)
        logger.info(f"Created index file at {index_file}")

    def _discover_modules(self) -> List[str]:
        """Discover Python modules in the source directory."""
        modules = []

        for item in self.source_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_"):
                if (item / "__init__.py").exists():
                    modules.append(item.name)
            elif (
                item.is_file()
                and item.suffix == ".py"
                and not item.name.startswith("_")
            ):
                modules.append(item.stem)

        return modules

    def create_module_docs(self) -> None:
        """Create documentation files for each module."""
        modules_dir = self.sphinx_source / "modules"
        modules_dir.mkdir(exist_ok=True)

        modules = self._discover_modules()

        for module in modules:
            module_content = f"""
{module}
{"=" * len(module)}

.. automodule:: src.{module}
   :members:
   :undoc-members:
   :show-inheritance:
   :private-members:
   :special-members: __init__

"""

            # Check if it's a package with submodules
            module_path = self.source_dir / module
            if module_path.is_dir():
                submodules = self._discover_submodules(module_path)
                if submodules:
                    module_content += f"""
Submodules
----------

"""
                    for submodule in submodules:
                        module_content += f"""
{submodule}
{"~" * len(submodule)}

.. automodule:: src.{module}.{submodule}
   :members:
   :undoc-members:
   :show-inheritance:
   :private-members:
   :special-members: __init__

"""

            module_file = modules_dir / f"{module}.rst"
            module_file.write_text(module_content)
            logger.info(f"Created module documentation for {module}")

    def _discover_submodules(self, package_path: Path) -> List[str]:
        """Discover submodules in a package."""
        submodules = []

        for item in package_path.iterdir():
            if (
                item.is_file()
                and item.suffix == ".py"
                and not item.name.startswith("_")
            ):
                if item.name != "__init__.py":
                    submodules.append(item.stem)

        return sorted(submodules)

    def build_sphinx_docs(self) -> None:
        """Build the Sphinx documentation."""
        logger.info("Building Sphinx documentation...")

        # Run sphinx-build
        cmd = [
            sys.executable,
            "-m",
            "sphinx",
            "-b",
            "html",
            str(self.sphinx_source),
            str(self.sphinx_build),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("Sphinx build completed successfully")
            if result.stdout:
                logger.debug(result.stdout)
        except subprocess.CalledProcessError as e:
            logger.error(f"Sphinx build failed: {e}")
            if e.stderr:
                logger.error(e.stderr)
            raise

    def convert_to_markdown(self) -> None:
        """Convert Sphinx HTML output to Markdown for MkDocs."""
        logger.info("Converting documentation to Markdown format...")

        # Ensure output directory exists
        self.mkdocs_api_dir.mkdir(parents=True, exist_ok=True)

        # Create main API index
        api_index = self.mkdocs_api_dir / "index.md"
        api_index_content = f"""# API Reference

This section contains the automatically generated API documentation for {self.project_name}.

## Modules

"""

        modules = self._discover_modules()
        for module in sorted(modules):
            api_index_content += f"- [{module}]({module}.md)\n"

            # Create module markdown file
            module_md = self.mkdocs_api_dir / f"{module}.md"
            module_content = self._generate_module_markdown(module)
            module_md.write_text(module_content)
            logger.info(f"Created Markdown documentation for {module}")

        api_index.write_text(api_index_content)
        logger.info(f"Created API index at {api_index}")

    def _generate_module_markdown(self, module_name: str) -> str:
        """Generate Markdown content for a module."""
        content = f"""# {module_name}

::: src.{module_name}
    options:
      show_source: true
      show_bases: true
      show_root_heading: true
      members: true
      inherited_members: false
      filters:
        - "!^_"
"""

        # Check for submodules
        module_path = self.source_dir / module_name
        if module_path.is_dir():
            submodules = self._discover_submodules(module_path)
            if submodules:
                content += "\n## Submodules\n\n"
                for submodule in submodules:
                    content += f"""### {submodule}

::: src.{module_name}.{submodule}
    options:
      show_source: true
      show_bases: true
      show_root_heading: false
      members: true
      inherited_members: false
      filters:
        - "!^_"

"""

        return content

    def cleanup(self) -> None:
        """Clean up temporary Sphinx build directories."""
        if self.sphinx_dir.exists():
            shutil.rmtree(self.sphinx_dir)
            logger.info(f"Cleaned up temporary directory: {self.sphinx_dir}")

    def generate(self, use_mkdocstrings: bool = True) -> None:
        """
        Generate the API documentation.

        Args:
            use_mkdocstrings: If True, generate Markdown for mkdocstrings plugin.
                            If False, use Sphinx to generate HTML then convert.
        """
        if use_mkdocstrings:
            # Generate Markdown files for mkdocstrings
            logger.info("Generating API documentation using mkdocstrings format...")
            self.mkdocs_api_dir.mkdir(parents=True, exist_ok=True)
            self.convert_to_markdown()
        else:
            # Use traditional Sphinx approach
            logger.info("Generating API documentation using Sphinx...")
            self.create_sphinx_config()
            self.create_index_rst()
            self.create_module_docs()
            self.build_sphinx_docs()
            self.convert_to_markdown()
            self.cleanup()

        logger.info(
            f"API documentation generated successfully at {self.mkdocs_api_dir}"
        )


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Generate API documentation")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).parent.parent / "src",
        help="Source code directory (default: ../src)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent / "site" / "docs",
        help="Output directory for documentation (default: ../site/docs)",
    )
    parser.add_argument(
        "--version", default="latest", help="Project version (default: latest)"
    )
    parser.add_argument(
        "--use-mkdocstrings",
        action="store_true",
        default=True,
        help="Use mkdocstrings format (default: True)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean existing API documentation before generating",
    )

    args = parser.parse_args()

    # Clean existing API docs if requested
    if args.clean:
        api_dir = args.output / "api"
        if api_dir.exists():
            shutil.rmtree(api_dir)
            logger.info(f"Cleaned existing API documentation at {api_dir}")

    # Generate documentation
    generator = APIDocGenerator(
        source_dir=args.source, output_dir=args.output, version=args.version
    )

    try:
        generator.generate(use_mkdocstrings=args.use_mkdocstrings)
        logger.info("✅ API documentation generation completed successfully!")
    except Exception as e:
        logger.error(f"❌ Failed to generate API documentation: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
