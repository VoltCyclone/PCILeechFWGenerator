#!/usr/bin/env python3
"""
Wrapper script to properly invoke the build.py module with correct Python path setup.
This ensures that relative imports work correctly in the container environment.
"""

import os
import sys
from pathlib import Path

# Determine if we're in container or local environment
if Path("/app").exists():
    # Container environment
    app_dir = Path("/app")
    src_dir = app_dir / "src"
else:
    # Local environment - we're in src/cli,
    # so go up two levels to get to project root
    app_dir = Path(__file__).parent.parent.parent.absolute()
    src_dir = app_dir / "src"

# Ensure both directories are in Python path
if str(app_dir) not in sys.path:
    sys.path.insert(0, str(app_dir))
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Change to the src directory so relative imports work
if src_dir.exists():
    os.chdir(str(src_dir))
else:
    print(f"Error: Source directory {src_dir} does not exist")
    sys.exit(1)

# Now import and run the build module
if __name__ == "__main__":
    try:
        # Import the build module
        from src import build

        # Run the main function with the original arguments
        sys.argv[0] = "build.py"  # Fix the script name for argument parsing
        build.main()
    except (ImportError, AttributeError) as e:
        # Try alternative import path
        try:
            import build

            sys.argv[0] = "build.py"  # Fix the script name for argument parsing
            build.main()
        except (ImportError, AttributeError):
            print(f"Error: Could not import build module or main function: {e}")
            sys.exit(1)
