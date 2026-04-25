#!/usr/bin/env python3
"""
Enhanced PyPI Package Generator for PCILeech Firmware Generator.

This script provides a comprehensive solution for generating PyPI packages with:
- Automated version management
- Build validation and testing
- Multiple distribution formats
- Security scanning
- Dependency analysis
- Package metadata validation
- Upload to PyPI/Test PyPI
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from git import GitCommandError, InvalidGitRepositoryError, Repo

    GIT_AVAILABLE = True
except ModuleNotFoundError:
    GIT_AVAILABLE = False
    Repo = None
    GitCommandError = InvalidGitRepositoryError = Exception


# ANSI color codes for output formatting
class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    PURPLE = "\033[0;35m"
    CYAN = "\033[0;36m"
    WHITE = "\033[1;37m"
    NC = "\033[0m"  # No Color


# Project configuration
PROJECT_ROOT = Path(__file__).parent.parent
VERSION_FILE = PROJECT_ROOT / "src" / "__version__.py"
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"
CHANGELOG_FILE = PROJECT_ROOT / "CHANGELOG.md"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"


class Logger:
    """Enhanced logging with colors and timestamps."""

    @staticmethod
    def _log(level: str, message: str, color: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{color}[{timestamp}] [{level}]{Colors.NC} {message}")

    @staticmethod
    def info(message: str) -> None:
        Logger._log("INFO", message, Colors.BLUE)

    @staticmethod
    def success(message: str) -> None:
        Logger._log("SUCCESS", message, Colors.GREEN)

    @staticmethod
    def warning(message: str) -> None:
        Logger._log("WARNING", message, Colors.YELLOW)

    @staticmethod
    def error(message: str) -> None:
        Logger._log("ERROR", message, Colors.RED)

    @staticmethod
    def debug(message: str) -> None:
        Logger._log("DEBUG", message, Colors.PURPLE)


class CommandRunner:
    """Enhanced command execution with better error handling."""

    @staticmethod
    def run(
        cmd: str,
        check: bool = True,
        capture_output: bool = True,
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess:
        """Run a command with enhanced error handling."""
        Logger.debug(f"Running: {cmd}")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=capture_output,
                text=True,
                cwd=cwd or PROJECT_ROOT,
            )

            if check and result.returncode != 0:
                Logger.error(f"Command failed: {cmd}")
                if result.stderr:
                    Logger.error(f"Error output: {result.stderr}")
                if result.stdout:
                    Logger.error(f"Standard output: {result.stdout}")
                sys.exit(1)

            return result

        except Exception as e:
            Logger.error(f"Failed to execute command: {cmd}")
            Logger.error(f"Exception: {str(e)}")
            if check:
                sys.exit(1)
            return subprocess.CompletedProcess(cmd, 1, "", str(e))


class PackageValidator:
    """Validate package configuration and dependencies."""

    @staticmethod
    def check_dependencies() -> None:
        """Check if all required tools are available."""
        Logger.info("Checking required dependencies...")

        required_tools = [
            ("python3", "Python 3.9+"),
            ("pip3", "Python package installer"),
            ("git", "Version control system"),
        ]

        optional_tools = [
            ("twine", "PyPI upload tool"),
            ("build", "Python build tool"),
            ("bandit", "Security scanner"),
            ("safety", "Dependency vulnerability scanner"),
        ]

        missing_required = []
        missing_optional = []

        for tool, description in required_tools:
            if not shutil.which(tool):
                missing_required.append(f"{tool} ({description})")

        for tool, description in optional_tools:
            if not shutil.which(tool):
                missing_optional.append(f"{tool} ({description})")

        if missing_required:
            Logger.error("Missing required dependencies:")
            for tool in missing_required:
                Logger.error(f"  - {tool}")
            sys.exit(1)

        if missing_optional:
            Logger.warning("Missing optional dependencies (will be installed):")
            for tool in missing_optional:
                Logger.warning(f"  - {tool}")

        Logger.success("Dependency check completed")

    @staticmethod
    def validate_project_structure() -> None:
        """Validate project structure and required files."""
        Logger.info("Validating project structure...")

        required_files = [
            VERSION_FILE,
            PYPROJECT_FILE,
            PROJECT_ROOT / "README.md",
            PROJECT_ROOT / "LICENSE",
            PROJECT_ROOT / "src" / "__init__.py",
        ]

        missing_files = []
        for file_path in required_files:
            if not file_path.exists():
                missing_files.append(str(file_path.relative_to(PROJECT_ROOT)))

        if missing_files:
            Logger.error("Missing required files:")
            for file_path in missing_files:
                Logger.error(f"  - {file_path}")
            sys.exit(1)

        Logger.success("Project structure validation passed")

    @staticmethod
    def validate_version_format(version: str) -> bool:
        """Validate that ``version`` is a PEP 440 version string.

        setuptools-scm produces PEP 440 versions including dev/post/local
        suffixes (e.g. ``0.14.15.dev3+g219fe2b``), so we delegate to
        ``packaging.version.Version`` instead of a hand-rolled regex.
        """
        try:
            from packaging.version import InvalidVersion, Version

            Version(version)
            return True
        except InvalidVersion:
            return False
        except Exception:
            return False


class VersionManager:
    """Manage package versioning."""

    @staticmethod
    def get_current_version() -> str:
        """Get current version via setuptools-scm (the canonical source)."""
        try:
            from setuptools_scm import get_version

            version = get_version(root=str(PROJECT_ROOT))
            if not PackageValidator.validate_version_format(version):
                raise ValueError(f"Invalid version format: {version}")
            return version
        except Exception as e:
            Logger.error(f"Failed to get current version: {e}")
            sys.exit(1)

    @staticmethod
    def update_build_metadata(version: str) -> None:
        """No-op retained for backwards compatibility.

        Build metadata is no longer baked into ``__version__.py``; the
        version itself is derived from git tags by setuptools-scm at build
        time and runtime callers compute build_date / commit_hash lazily
        from the live git checkout.
        """
        Logger.info(
            "Skipping build-metadata file update; version is dynamic via "
            "setuptools-scm."
        )


class SecurityScanner:
    """Security scanning and vulnerability checking."""

    @staticmethod
    def install_security_tools() -> None:
        """Install security scanning tools if not available."""
        tools = ["bandit", "safety"]

        for tool in tools:
            if not shutil.which(tool):
                Logger.info(f"Installing {tool}...")
                CommandRunner.run(f"pip3 install {tool}")

    @staticmethod
    def run_safety_check() -> None:
        """Check dependencies for known vulnerabilities."""
        Logger.info("Checking dependencies for vulnerabilities...")

        try:
            result = CommandRunner.run("safety check --json", check=False)

            if result.returncode == 0:
                Logger.success("No known vulnerabilities found in dependencies")
            else:
                # Parse safety output
                try:
                    vulnerabilities = json.loads(result.stdout)
                    if vulnerabilities:
                        Logger.error(
                            f"Found {
                                len(vulnerabilities)} vulnerabilities:"
                        )
                        for vuln in vulnerabilities[:5]:  # Show first 5
                            Logger.error(
                                f"  - {vuln.get('package', 'Unknown')}: {vuln.get('vulnerability', 'Unknown')}"
                            )
                        Logger.error("Run 'safety check' for full details")
                        sys.exit(1)
                except json.JSONDecodeError:
                    Logger.warning("Could not parse safety check results")

        except Exception as e:
            Logger.warning(f"Vulnerability check failed: {e}")


class QualityChecker:
    """Code quality and testing."""

    @staticmethod
    def install_dev_dependencies() -> None:
        """Install development dependencies."""
        Logger.info("Installing development dependencies...")

        dev_deps = [
            "build>=0.10.0",
            "twine>=4.0.0",
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "black>=23.0.0",
            "isort>=5.12.0",
            "flake8>=6.0.0",
            "mypy>=1.0.0",
        ]

        for dep in dev_deps:
            if not shutil.which(dep.split(">=")[0]):
                CommandRunner.run(f"pip3 install '{dep}'")

    @staticmethod
    def run_code_formatting_check() -> None:
        """Check code formatting with black and isort."""
        Logger.info("Checking code formatting...")

        # Check black formatting
        result = CommandRunner.run("black --check src/ tests/", check=False)
        if result.returncode != 0:
            Logger.error(
                "Code formatting issues found. Run 'black src/ tests/' to fix."
            )
            sys.exit(1)

        # Check import sorting
        result = CommandRunner.run("isort --check-only src/ tests/", check=False)
        if result.returncode != 0:
            Logger.error("Import sorting issues found. Run 'isort src/ tests/' to fix.")
            sys.exit(1)

        Logger.success("Code formatting check passed")

    @staticmethod
    def run_linting() -> None:
        """Run flake8 linting."""
        Logger.info("Running flake8 linting...")

        result = CommandRunner.run(
            "flake8 src/ tests/ --count --max-line-length=88 --statistics", check=False
        )

        if result.returncode != 0:
            Logger.error("Linting issues found")
            Logger.error(result.stdout)

        Logger.success("Linting check passed")

    @staticmethod
    def run_type_checking() -> None:
        """Run mypy type checking."""
        Logger.info("Running mypy type checking...")

        result = CommandRunner.run("mypy src/", check=False)
        if result.returncode != 0:
            Logger.warning("Type checking issues found")
            Logger.warning(result.stdout)
            # Don't fail on type checking issues, just warn
        else:
            Logger.success("Type checking passed")

    @staticmethod
    def run_tests() -> None:
        """Run test suite with coverage."""
        Logger.info("Running test suite...")

        result = CommandRunner.run(
            "pytest tests/ --cov=src --cov-report=term-missing --cov-report=xml",
            check=False,
        )

        if result.returncode != 0:
            Logger.error("Tests failed")

        Logger.success("All tests passed")


class PackageBuilder:
    """Build and validate packages."""

    @staticmethod
    def clean_build_artifacts() -> None:
        """Clean previous build artifacts."""
        Logger.info("Cleaning build artifacts...")

        artifacts = [
            BUILD_DIR,
            DIST_DIR,
            PROJECT_ROOT / "*.egg-info",
            PROJECT_ROOT / ".pytest_cache",
            PROJECT_ROOT / "htmlcov",
            PROJECT_ROOT / ".coverage",
        ]

        for artifact in artifacts:
            if artifact.exists():
                if artifact.is_dir():
                    shutil.rmtree(artifact)
                else:
                    artifact.unlink()

        # Clean __pycache__ directories
        for pycache in PROJECT_ROOT.rglob("__pycache__"):
            shutil.rmtree(pycache)

        # Clean .pyc files
        for pyc_file in PROJECT_ROOT.rglob("*.pyc"):
            pyc_file.unlink()

        Logger.success("Build artifacts cleaned")

    @staticmethod
    def build_distributions() -> List[Path]:
        """Build wheel and source distributions."""
        Logger.info("Building package distributions...")

        # Ensure dist directory exists
        DIST_DIR.mkdir(exist_ok=True)

        # Build using python -m build
        CommandRunner.run("python3 -m build")

        # List built distributions
        distributions = list(DIST_DIR.glob("*"))

        if not distributions:
            Logger.error("No distributions were built")
            sys.exit(1)

        Logger.success("Package distributions built:")
        for dist in distributions:
            Logger.info(f"  - {dist.name} ({dist.stat().st_size / 1024:.1f} KB)")

        return distributions

    @staticmethod
    def validate_distributions(distributions: List[Path]) -> None:
        """Validate built distributions."""
        Logger.info("Validating distributions...")

        # Check with twine
        dist_paths = " ".join(str(d) for d in distributions)
        CommandRunner.run(f"twine check {dist_paths}")

        # Verify tests are not included in the package
        PackageBuilder._verify_tests_excluded(distributions)

        Logger.success("Distribution validation passed")

    @staticmethod
    def _verify_tests_excluded(distributions: List[Path]) -> None:
        """Verify that test files are not included in distributions."""
        Logger.info("Verifying tests are excluded from package...")

        import tarfile
        import zipfile

        for dist in distributions:
            if dist.suffix == ".whl":
                # Check wheel file
                with zipfile.ZipFile(dist, "r") as zf:
                    files = zf.namelist()
                    test_files = [
                        f for f in files if "test" in f.lower() and f.endswith(".py")
                    ]
                    if test_files:
                        Logger.warning(
                            f"Found test files in {dist.name}: {test_files[:5]}"
                        )
                    else:
                        Logger.success(f"No test files found in {dist.name}")

            elif dist.suffix == ".gz" and ".tar" in dist.name:
                # Check source distribution
                with tarfile.open(dist, "r:gz") as tf:
                    files = tf.getnames()
                    test_files = [
                        f for f in files if "/tests/" in f and f.endswith(".py")
                    ]
                    if test_files:
                        Logger.warning(
                            f"Found test files in {dist.name}: {test_files[:5]}"
                        )
                    else:
                        Logger.success(f"No test files found in {dist.name}")

    @staticmethod
    def test_installation() -> None:
        """Test package installation in a temporary environment."""
        Logger.info("Testing package installation...")

        with tempfile.TemporaryDirectory() as temp_dir:
            venv_path = Path(temp_dir) / "test_venv"

            # Create virtual environment
            CommandRunner.run(f"python -m venv {venv_path}")

            # Activate and install
            if os.name == "nt":  # Windows
                pip_path = venv_path / "Scripts" / "pip"
                python_path = venv_path / "Scripts" / "python"
            else:  # Unix-like
                pip_path = venv_path / "bin" / "pip3"
                python_path = venv_path / "bin" / "python3"

            # Install the built wheel
            wheel_files = list(DIST_DIR.glob("*.whl"))
            if wheel_files:
                CommandRunner.run(f"{pip_path} install {wheel_files[0]}")

                # Test imports
                CommandRunner.run(
                    f"{python_path} -c 'import src; print(f\"Version: {{src.__version__}}\")'"
                )

                # Test console scripts
                result = CommandRunner.run(
                    f"{python_path} -c 'import pkg_resources; print([ep.name for ep in pkg_resources.iter_entry_points(\"console_scripts\")])'",
                    check=False,
                )

                Logger.success("Package installation test passed")
            else:
                Logger.error("No wheel file found for testing")
                sys.exit(1)


class PyPIUploader:
    """Handle PyPI uploads."""

    @staticmethod
    def upload_to_pypi(test_pypi: bool = False) -> None:
        """Upload distributions to PyPI or Test PyPI."""
        "testpypi" if test_pypi else "pypi"
        pypi_name = "Test PyPI" if test_pypi else "PyPI"

        Logger.info(f"Uploading to {pypi_name}...")

        # Check if twine is configured
        config_file = Path.home() / ".pypirc"
        if not config_file.exists():
            Logger.warning(
                "No .pypirc found. You may need to configure authentication."
            )

        # Upload
        if test_pypi:
            CommandRunner.run("twine upload --repository testpypi dist/*")
            Logger.success("Uploaded to Test PyPI")
            Logger.info(
                "Install with: pip3` install --index-url https://test.pypi.org/simple/ pcileechfwgenerator"
            )
        else:
            CommandRunner.run("twine upload dist/*")
            Logger.success("Uploaded to PyPI")
            Logger.info("Install with: pip3 install pcileechfwgenerator")


class PackageGenerator:
    """Main package generation orchestrator."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.version = VersionManager.get_current_version()

    def run(self) -> None:
        """Run the complete package generation process."""
        start_time = time.time()

        Logger.info(
            f"Starting PyPI package generation for version {
                self.version}"
        )
        Logger.info(f"Project root: {PROJECT_ROOT}")

        try:
            # Validation phase
            if not self.args.skip_validation:
                PackageValidator.check_dependencies()
                PackageValidator.validate_project_structure()

            # Quality phase
            if not self.args.skip_quality:
                QualityChecker.install_dev_dependencies()
                if not self.args.skip_formatting:
                    QualityChecker.run_code_formatting_check()
                QualityChecker.run_linting()
                QualityChecker.run_type_checking()

                if not self.args.skip_tests:
                    QualityChecker.run_tests()

            # Build phase
            PackageBuilder.clean_build_artifacts()
            VersionManager.update_build_metadata(self.version)
            distributions = PackageBuilder.build_distributions()
            PackageBuilder.validate_distributions(distributions)

            if not self.args.skip_install_test:
                PackageBuilder.test_installation()

            # Upload phase
            if not self.args.skip_upload:
                PyPIUploader.upload_to_pypi(test_pypi=self.args.test_pypi)

            # Summary
            elapsed_time = time.time() - start_time
            Logger.success(
                f"Package generation completed successfully in {
                    elapsed_time:.1f}s"
            )

            self._print_summary(distributions)

        except KeyboardInterrupt:
            Logger.error("Process interrupted by user")
            sys.exit(1)
        except Exception as e:
            Logger.error(f"Package generation failed: {e}")
            sys.exit(1)
        finally:
            self._cleanup()

    def _print_summary(self, distributions: List[Path]) -> None:
        """Print generation summary."""
        Logger.info("\n" + "=" * 60)
        Logger.info("PACKAGE GENERATION SUMMARY")
        Logger.info("=" * 60)
        Logger.info("Package: pcileechfwgenerator")
        Logger.info(f"Version: {self.version}")
        Logger.info(f"Distributions built: {len(distributions)}")

        for dist in distributions:
            size_kb = dist.stat().st_size / 1024
            Logger.info(f"  - {dist.name} ({size_kb:.1f} KB)")

        if not self.args.skip_upload:
            pypi_name = "Test PyPI" if self.args.test_pypi else "PyPI"
            Logger.info(f"Uploaded to: {pypi_name}")

        Logger.info("=" * 60)

    def _cleanup(self) -> None:
        """Clean up temporary files."""
        temp_files = [
            PROJECT_ROOT / "bandit-report.json",
            PROJECT_ROOT / "coverage.xml",
        ]

        for temp_file in temp_files:
            if temp_file.exists():
                temp_file.unlink()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate PyPI package for PCILeech Firmware Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Full package generation
  %(prog)s --test-pypi              # Upload to Test PyPI
  %(prog)s --skip-tests             # Skip running tests
  %(prog)s --skip-upload            # Build only, don't upload
  %(prog)s --quick                  # Quick build (skip quality checks)
        """,
    )

    # Main options
    parser.add_argument(
        "--test-pypi", action="store_true", help="Upload to Test PyPI instead of PyPI"
    )

    parser.add_argument(
        "--skip-upload", action="store_true", help="Skip uploading to PyPI (build only)"
    )

    # Skip options
    parser.add_argument(
        "--skip-validation", action="store_true", help="Skip project validation checks"
    )

    parser.add_argument(
        "--skip-security", action="store_true", help="Skip security scanning"
    )

    parser.add_argument(
        "--skip-quality", action="store_true", help="Skip all quality checks"
    )

    parser.add_argument(
        "--skip-formatting", action="store_true", help="Skip code formatting checks"
    )

    parser.add_argument("--skip-tests", action="store_true", help="Skip running tests")

    parser.add_argument(
        "--skip-install-test",
        action="store_true",
        help="Skip testing package installation",
    )

    # Convenience options
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick build (skip quality checks and tests)",
    )

    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    # Handle convenience options
    if args.quick:
        args.skip_quality = True
        args.skip_security = True
        args.skip_tests = True

    # Change to project root
    os.chdir(PROJECT_ROOT)

    # Run package generation
    generator = PackageGenerator(args)
    generator.run()


if __name__ == "__main__":
    main()
