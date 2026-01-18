#!/usr/bin/env python3
"""
Automated changelog update script for PCILeech Firmware Generator.
Updates CHANGELOG.rst with new version information automatically.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional


def run_command(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {cmd}")
        print(f"Error: {result.stderr}")
        sys.exit(1)
    return result


def get_recent_commits(since_tag: Optional[str] = None, limit: int = 20) -> List[str]:
    """Get recent commit messages since last tag or last N commits."""
    if since_tag:
        cmd = f"git log {since_tag}..HEAD --oneline --pretty=format:'%s'"
    else:
        cmd = f"git log -{limit} --oneline --pretty=format:'%s'"

    try:
        result = run_command(cmd)
        commits = [line.strip() for line in result.stdout.split("\n") if line.strip()]
        return commits
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


def categorize_commits(commits: List[str]) -> dict:
    """Categorize commits into different types based on conventional commits."""
    categories = {
        "Features": [],
        "Bug Fixes": [],
        "Performance": [],
        "Documentation": [],
        "Build System": [],
        "Tests": [],
        "Refactoring": [],
        "Breaking Changes": [],
        "Other": [],
    }

    for commit in commits:
        commit_lower = commit.lower()

        # Check for breaking changes first
        if "breaking change" in commit_lower or "!" in commit.split(":")[0]:
            categories["Breaking Changes"].append(commit)
        # Features
        elif commit_lower.startswith("feat"):
            categories["Features"].append(commit)
        # Bug fixes
        elif commit_lower.startswith("fix"):
            categories["Bug Fixes"].append(commit)
        # Performance improvements
        elif commit_lower.startswith("perf"):
            categories["Performance"].append(commit)
        # Documentation
        elif commit_lower.startswith("docs"):
            categories["Documentation"].append(commit)
        # Build system
        elif commit_lower.startswith(("build", "ci", "chore")):
            categories["Build System"].append(commit)
        # Tests
        elif commit_lower.startswith("test"):
            categories["Tests"].append(commit)
        # Refactoring
        elif commit_lower.startswith("refactor"):
            categories["Refactoring"].append(commit)
        else:
            categories["Other"].append(commit)

    # Remove empty categories
    return {k: v for k, v in categories.items() if v}


def format_changelog_entry(
    version: str, categories: dict, date: Optional[str] = None
) -> str:
    """Format a new changelog entry."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    entry = f"## Version {version} ({date})\n\n"

    if not categories:
        entry += "- Minor updates and improvements.\n\n"
        return entry

    for category, commits in categories.items():
        if commits:
            entry += f"### {category}\n\n"
            for commit in commits:
                # Clean up commit message - remove conventional commit prefixes
                clean_commit = re.sub(
                    r"^(feat|fix|docs|style|refactor|perf|test|chore|build|ci)(\([^)]+\))?:\s*",
                    "",
                    commit,
                )
                # Capitalize first letter
                clean_commit = (
                    clean_commit[0].upper() + clean_commit[1:]
                    if clean_commit
                    else commit
                )
                # Ensure it ends with a period
                if not clean_commit.endswith("."):
                    clean_commit += "."
                entry += f"- {clean_commit}\n"
            entry += "\n"

    return entry


def update_table_of_contents(
    content: str, new_version: str, date: Optional[str] = None
) -> str:
    """Update the table of contents section with the new version."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # Find the table of contents section
    toc_pattern = r"(## 📑 Table of Contents\s*\n)(.*?)(\n---)"

    def update_toc(match):
        header = match.group(1)
        existing_toc = match.group(2)
        footer = match.group(3)

        # Create new TOC entry
        new_entry = f"- [Version {new_version} ({date})](#version-{new_version.replace('.', '')}-{date})\n"

        # Add new entry at the top of existing TOC
        updated_toc = new_entry + existing_toc

        return header + updated_toc + footer

    updated_content = re.sub(toc_pattern, update_toc, content, flags=re.DOTALL)
    return updated_content


def update_changelog(
    changelog_path: Path,
    version: str,
    commits: Optional[List[str]] = None,
    dry_run: bool = False,
    custom_message: Optional[str] = None,
) -> None:
    """Update the changelog file with new version information."""
    if not changelog_path.exists():
        print(f"Changelog file not found: {changelog_path}")
        sys.exit(1)

    # Read current changelog
    with open(changelog_path, "r", encoding="utf-8") as f:
        content = f.read()

    date = datetime.now().strftime("%Y-%m-%d")

    # If custom message provided, use it directly
    if custom_message:
        new_entry = f"## Version {version} ({date})\n\n{custom_message}\n\n---\n"
    else:
        # Get commits if not provided
        if commits is None:
            latest_tag = get_latest_tag()
            commits = get_recent_commits(latest_tag)

        # Categorize commits
        categories = categorize_commits(commits)

        # Generate changelog entry
        new_entry = format_changelog_entry(version, categories, date)
        new_entry += "---\n"

    # Find where to insert the new entry (after the TOC section)
    # Look for the second occurrence of "---" (after TOC)
    toc_pattern = r"(## 📑 Table of Contents.*?---\s*\n)"
    match = re.search(toc_pattern, content, re.DOTALL)

    if match:
        # Insert after the TOC section
        insert_pos = match.end()
        new_content = content[:insert_pos] + new_entry + content[insert_pos:]
    else:
        # Fallback: look for any "---" and insert after it
        toc_end_pattern = r"(---\s*\n)"
        match = re.search(toc_end_pattern, content)
        if match:
            insert_pos = match.end()
            new_content = content[:insert_pos] + new_entry + content[insert_pos:]
        else:
            # Final fallback: append at the end
            new_content = content + "\n" + new_entry

    # Update table of contents
    new_content = update_table_of_contents(new_content, version, date)

    if dry_run:
        print("DRY RUN - proposed changelog entry:")
        print("=" * 50)
        print(new_entry)
        print("=" * 50)
        return

    # Write updated changelog
    with open(changelog_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"Updated {changelog_path} with version {version}")
    if commits:
        print(f"Processed {len(commits)} commit messages")


def main():
    """Main changelog update script."""
    parser = argparse.ArgumentParser(description="Update changelog automatically")
    parser.add_argument(
        "--version", required=True, help="Version to add to changelog (e.g., 1.2.3)"
    )
    parser.add_argument(
        "--message",
        help="Custom changelog message (overrides automatic commit analysis)",
    )
    parser.add_argument(
        "--since-tag", help="Analyze commits since this tag (default: latest tag)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without making changes",
    )
    parser.add_argument(
        "--changelog-path",
        default="CHANGELOG.rst",
        help="Path to changelog file (default: CHANGELOG.rst)",
    )

    args = parser.parse_args()

    # Project paths
    project_root = Path(__file__).parent.parent
    changelog_path = project_root / args.changelog_path

    # Get commits for analysis
    commits = None
    if not args.message:
        since_tag = args.since_tag or get_latest_tag()
        commits = get_recent_commits(since_tag)

        if not commits:
            print("Warning: No commits found for analysis. Using generic message.")
            args.message = "Minor updates and improvements."

    # Update changelog
    update_changelog(
        changelog_path=changelog_path,
        version=args.version,
        commits=commits,
        dry_run=args.dry_run,
        custom_message=args.message,
    )


if __name__ == "__main__":
    main()
