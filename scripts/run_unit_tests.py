#!/usr/bin/env python3
"""
Unit test runner for PCILeech FW Generator.

This module provides functionality to discover and run unit tests for the
PCILeech FW Generator project. It supports running all tests or specific
test modules, with proper error handling and reporting.
"""

import argparse
import importlib.util
import logging
import sys
import time
import unittest
from pathlib import Path
from typing import List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TestRunner:
    """Manages test discovery and execution."""

    def __init__(self, test_dir: Optional[Path] = None):
        """
        Initialize the test runner.

        Args:
            test_dir: Directory containing test files. Defaults to the repo's
                ``tests/`` directory (this runner lives in ``scripts/``).
        """
        # This script lives in scripts/; tests live in ../tests.
        self.project_root = Path(__file__).parent.parent.resolve()
        self.test_dir = test_dir or (self.project_root / "tests")
        self._setup_python_path()

    def _setup_python_path(self) -> None:
        """Add project root to Python path if not already present."""
        project_root_str = str(self.project_root)
        if project_root_str not in sys.path:
            sys.path.insert(0, project_root_str)
            logger.debug(f"Added {project_root_str} to Python path")

    def discover_test_modules(self) -> List[Path]:
        """
        Discover all test modules in the test directory.

        Returns:
            List of paths to test modules.
        """
        test_files = sorted(self.test_dir.glob("test_*.py"))
        # Filter out this script itself
        test_files = [f for f in test_files if f.name != "run_unit_tests.py"]
        return test_files

    def list_available_tests(self) -> None:
        """List all available test modules."""
        test_files = self.discover_test_modules()

        if not test_files:
            print("No test modules found.")
            return

        print("Available test modules:")
        for test_file in test_files:
            module_name = test_file.stem.removeprefix("test_")
            print(f"  - {module_name}")

        print(f"\nTotal: {len(test_files)} test module(s)")

    def run_all_tests(self, verbosity: int = 2) -> int:
        """
        Run all discovered tests.

        Args:
            verbosity: Test output verbosity level (0-2).

        Returns:
            Exit code (0 for success, 1 for failure).
        """
        print("Running all unit tests...")
        print("=" * 70)

        start_time = time.time()

        try:
            # Discover tests
            loader = unittest.TestLoader()
            suite = loader.discover(
                start_dir=str(self.test_dir),
                pattern="test_*.py",
                top_level_dir=str(self.project_root),
            )

            # Count tests
            test_count = suite.countTestCases()
            if test_count == 0:
                logger.warning("No tests found to run")
                return 1

            print(f"Discovered {test_count} test(s)\n")

            # Run tests
            runner = unittest.TextTestRunner(
                verbosity=verbosity, stream=sys.stdout, failfast=False
            )
            result = runner.run(suite)

            # Print summary
            elapsed_time = time.time() - start_time
            self._print_test_summary(result, elapsed_time)

            return 0 if result.wasSuccessful() else 1

        except Exception as e:
            logger.error(f"Error running tests: {e}", exc_info=True)
            return 1

    def run_specific_test(self, test_module: str, verbosity: int = 2) -> int:
        """
        Run a specific test module.

        Args:
            test_module: Name of the test module (without 'test_' prefix or '.py' suffix).
            verbosity: Test output verbosity level (0-2).

        Returns:
            Exit code (0 for success, 1 for failure).
        """
        # Construct test file path
        test_file = self.test_dir / f"test_{test_module}.py"

        if not test_file.exists():
            logger.error(f"Test file '{test_file}' does not exist")
            print("\nAvailable test modules:")
            self.list_available_tests()
            return 1

        print(f"Running test module: {test_module}")
        print("=" * 70)

        start_time = time.time()

        try:
            # Load the test module
            spec = importlib.util.spec_from_file_location(
                f"test_{test_module}", str(test_file)
            )

            if spec is None or spec.loader is None:
                logger.error(f"Could not load spec for test module '{test_file}'")
                return 1

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Discover and run tests from the module
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromModule(module)

            # Count tests
            test_count = suite.countTestCases()
            if test_count == 0:
                logger.warning(f"No tests found in module '{test_module}'")
                return 1

            print(f"Discovered {test_count} test(s) in {test_module}\n")

            # Run tests
            runner = unittest.TextTestRunner(
                verbosity=verbosity, stream=sys.stdout, failfast=False
            )
            result = runner.run(suite)

            # Print summary
            elapsed_time = time.time() - start_time
            self._print_test_summary(result, elapsed_time)

            return 0 if result.wasSuccessful() else 1

        except Exception as e:
            logger.error(
                f"Error running test module '{test_module}': {e}", exc_info=True
            )
            return 1

    def _print_test_summary(
        self, result: unittest.TestResult, elapsed_time: float
    ) -> None:
        """
        Print a summary of test results.

        Args:
            result: The test result object.
            elapsed_time: Time taken to run tests in seconds.
        """
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)

        total_tests = result.testsRun
        failures = len(result.failures)
        errors = len(result.errors)
        skipped = len(result.skipped) if hasattr(result, "skipped") else 0

        print(f"Total tests run: {total_tests}")
        print(f"Passed: {total_tests - failures - errors - skipped}")
        print(f"Failed: {failures}")
        print(f"Errors: {errors}")
        print(f"Skipped: {skipped}")
        print(f"Time elapsed: {elapsed_time:.2f}s")

        if result.wasSuccessful():
            print("\n✅ All tests passed!")
        else:
            print("\n❌ Some tests failed!")

            # Print failure details
            if result.failures:
                print("\nFailures:")
                for test, traceback in result.failures:
                    print(f"  - {test}: {traceback.splitlines()[-1]}")

            if result.errors:
                print("\nErrors:")
                for test, traceback in result.errors:
                    print(f"  - {test}: {traceback.splitlines()[-1]}")


def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Run unit tests for PCILeech FW Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Run all tests
  %(prog)s --list            # List available test modules
  %(prog)s config_space      # Run specific test module
  %(prog)s -v 1 device_config # Run with reduced verbosity
  %(prog)s --quiet           # Run with minimal output
""",
    )

    parser.add_argument(
        "test_module",
        nargs="?",
        help="Specific test module to run (without 'test_' prefix)",
    )

    parser.add_argument(
        "--list", "-l", action="store_true", help="List available test modules"
    )

    parser.add_argument(
        "--verbosity",
        "-v",
        type=int,
        choices=[0, 1, 2],
        default=2,
        help="Test output verbosity (0=quiet, 1=normal, 2=verbose)",
    )

    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Run tests with minimal output (same as -v 0)",
    )

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    return parser.parse_args()


def main() -> int:
    """
    Main entry point for the test runner.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    args = parse_arguments()

    # Configure logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle quiet mode
    if args.quiet:
        args.verbosity = 0

    # Create test runner
    runner = TestRunner()

    # Handle list command
    if args.list:
        runner.list_available_tests()
        return 0

    # Run tests
    if args.test_module:
        return runner.run_specific_test(args.test_module, args.verbosity)
    else:
        return runner.run_all_tests(args.verbosity)


if __name__ == "__main__":
    sys.exit(main())
