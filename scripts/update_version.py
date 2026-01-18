#!/usr/bin/env python3
"""
Automated version update script for PCILeech Firmware Generator.
Updates version information and build metadata automatically in CI/CD.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple


def run_command(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {cmd}")
        print(f"Error: {result.stderr}")
        sys.exit(1)
    return result


def get_git_info() -> Tuple[str, str]:
    """Get current git commit hash and branch."""
    try:
        commit_hash = run_command("git rev-parse --short HEAD").stdout.strip()
        branch = run_command("git rev-parse --abbrev-ref HEAD").stdout.strip()
        return commit_hash, branch
    except Exception:
        return "unknown", "unknown"


def get_current_version(version_file: Path) -> str:
    """Get the current version from __version__.py."""
    with open(version_file, "r") as f:
        content = f.read()

    match = re.search(r'__version__ = ["\']([^"\']+)["\']', content)
    if not match:
        raise ValueError("Could not find version in __version__.py")

    return match.group(1)


def parse_version(version_str: str) -> Tuple[int, int, int]:
    """Parse version string into tuple of integers."""
    try:
        parts = version_str.split(".")
        if len(parts) != 3:
            raise ValueError(f"Version must have exactly 3 parts: {version_str}")
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid version format: {version_str}")


def bump_version(current_version: str, bump_type: str) -> str:
    """Bump version according to semantic versioning."""
    major, minor, patch = parse_version(current_version)

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "patch":
        patch += 1
    else:
        raise ValueError(f"Invalid bump type: {bump_type}")

    return f"{major}.{minor}.{patch}"


def auto_determine_bump_type(commit_messages: list) -> str:
    """Automatically determine version bump type from commit messages."""
    has_breaking = False
    has_feature = False
    has_fix = False

    for msg in commit_messages:
        msg_lower = msg.lower()

        # Check for breaking changes
        if "breaking change" in msg_lower or "!" in msg.split(":")[0]:
            has_breaking = True

        # Check for features
        elif msg_lower.startswith("feat"):
            has_feature = True

        # Check for fixes
        elif msg_lower.startswith("fix"):
            has_fix = True

    if has_breaking:
        return "major"
    elif has_feature:
        return "minor"
    elif has_fix:
        return "patch"
    else:
        return "patch"  # Default to patch for other changes


def get_recent_commits(since_tag: Optional[str] = None) -> list:
    """Get recent commit messages since last tag or last 10 commits."""
    if since_tag:
        cmd = f"git log {since_tag}..HEAD --oneline --pretty=format:'%s'"
    else:
        cmd = "git log -10 --oneline --pretty=format:'%s'"

    try:
        result = run_command(cmd)
        return [line.strip() for line in result.stdout.split("\n") if line.strip()]
    except Exception:
        return []


def get_latest_tag() -> Optional[str]:
    """Get the latest git tag."""
    try:
        result = run_command("git describe --tags --abbrev=0", check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def update_version_file(version_file: Path, new_version: str, commit_hash: str) -> None:
    """Update the version file with new version and build metadata."""
    with open(version_file, "r") as f:
        content = f.read()

    # Update version
    content = re.sub(
        r'__version__ = ["\'][^"\']+["\']', f'__version__ = "{new_version}"', content
    )

    # Update version_info tuple
    major, minor, patch = parse_version(new_version)
    content = re.sub(
        r"__version_info__ = \([^)]+\)",
        f"__version_info__ = ({major}, {minor}, {patch})",
        content,
    )

    # Update build metadata
    build_date = datetime.utcnow().isoformat()
    content = re.sub(
        r'__build_date__ = ["\'][^"\']+["\']',
        f'__build_date__ = "{build_date}"',
        content,
    )

    content = re.sub(
        r'__commit_hash__ = ["\'][^"\']+["\']',
        f'__commit_hash__ = "{commit_hash}"',
        content,
    )

    with open(version_file, "w") as f:
        f.write(content)

    print(f"Updated version to {new_version} (commit: {commit_hash})")


def check_if_version_update_needed() -> bool:
    """Check if version update is needed based on git status."""
    try:
        # Check if we're on main branch
        branch = run_command("git rev-parse --abbrev-ref HEAD").stdout.strip()
        if branch != "main":
            print(f"Not on main branch (current: {branch}), skipping version update")
            return False

        # Check if there are new commits since last tag
        latest_tag = get_latest_tag()
        if latest_tag:
            result = run_command(
                f"git rev-list {latest_tag}..HEAD --count", check=False
            )
            if result.returncode == 0:
                commit_count = int(result.stdout.strip())
                if commit_count == 0:
                    print("No new commits since last tag, skipping version update")
                    return False

        return True
    except Exception as e:
        print(f"Error checking if version update needed: {e}")
        return False


def main():
    """Main version update script."""
    parser = argparse.ArgumentParser(description="Update version automatically")
    parser.add_argument(
        "--bump-type",
        choices=["major", "minor", "patch", "auto"],
        default="auto",
        help="Type of version bump (default: auto-detect from commits)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Force version update even if not needed"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes",
    )

    args = parser.parse_args()

    # Project paths
    project_root = Path(__file__).parent.parent
    version_file = project_root / "src" / "__version__.py"

    if not version_file.exists():
        print(f"Version file not found: {version_file}")
        sys.exit(1)

    # Check if update is needed
    if not args.force and not check_if_version_update_needed():
        sys.exit(0)

    # Get current version and git info
    current_version = get_current_version(version_file)
    commit_hash, branch = get_git_info()

    print(f"Current version: {current_version}")
    print(f"Current commit: {commit_hash}")
    print(f"Current branch: {branch}")

    # Determine bump type
    if args.bump_type == "auto":
        latest_tag = get_latest_tag()
        recent_commits = get_recent_commits(latest_tag)
        bump_type = auto_determine_bump_type(recent_commits)
        print(f"Auto-detected bump type: {bump_type}")
        print(f"Based on commits: {recent_commits[:3]}...")  # Show first 3 commits
    else:
        bump_type = args.bump_type

    # Calculate new version
    new_version = bump_version(current_version, bump_type)
    print(f"New version: {new_version}")

    if args.dry_run:
        print("DRY RUN: Would update version file but not making changes")
        sys.exit(0)

    # Update version file
    update_version_file(version_file, new_version, commit_hash)

    # Update changelog
    try:
        changelog_script = project_root / "scripts" / "update_changelog.py"
        if changelog_script.exists():
            print(f"Updating changelog for version {new_version}...")
            changelog_cmd = f"python3 {changelog_script} --version {new_version}"
            run_command(
                changelog_cmd, check=False
            )  # Don't fail build if changelog update fails
            print("Changelog updated successfully")
        else:
            print("Changelog update script not found, skipping changelog update")
    except Exception as e:
        print(f"Warning: Failed to update changelog: {e}")

    # Output for CI/CD
    print(f"::set-output name=version::{new_version}")
    print(f"::set-output name=previous_version::{current_version}")
    print(f"::set-output name=bump_type::{bump_type}")


if __name__ == "__main__":
    main()
