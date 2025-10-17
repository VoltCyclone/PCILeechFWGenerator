#!/usr/bin/env python3
"""
Lint SystemVerilog templates for block-scoped declarations appearing after statements.

Heuristic:
- Within begin..end procedural blocks (always_ff/always_comb/case/if/etc.),
  flag lines that declare local signals (logic|reg|wire|bit) after any
  non-declaration statement has already appeared in that same block.

Notes:
- Skips content inside function/task blocks.
- Ignores comments and blank lines when determining the first statement.
- Designed for templates (.sv.j2) but operates on the raw text; it does
  not evaluate Jinja, just checks for likely problematic patterns.

Exit codes:
- 0: No issues found.
- 1: Issues found and --strict is set.
- 0: Issues found but --strict not set (prints warnings).
"""

from __future__ import annotations

import argparse

import re
from pathlib import Path

from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
SV_DIR = ROOT / "src" / "templates" / "sv"


BEGIN_RE = re.compile(r"\bbegin\b")
END_RE = re.compile(r"\bend\b(?!\w)")  # match 'end' but not 'endcase', etc.
END_ANY_RE = re.compile(r"\bend(case|function|task)?\b")
# Match declarations, including those with initializers and parentheses
DECL_RE = re.compile(r"^\s*(logic|reg|wire|bit)\b[^;]*;\s*$")
STMT_HINT_RE = re.compile(
    r"^\s*(if\b|case\b|for\b|while\b|unique\b|priority\b|assign\b|\w+\s*<=|\w+\s*=)"
)
FUNCTION_START_RE = re.compile(r"\bfunction\b")
FUNCTION_END_RE = re.compile(r"\bendfunction\b")
TASK_START_RE = re.compile(r"\btask\b")
TASK_END_RE = re.compile(r"\bendtask\b")


def strip_line_comment(line: str, in_block: bool) -> Tuple[str, bool]:
    """Remove // comments and handle /* */ block comments crudely."""
    # Handle block comments spanning multiple lines
    if in_block:
        end = line.find("*/")
        if end == -1:
            return "", True
        # Remove comment up to */ and continue
        line = line[end + 2 :]
        in_block = False
    # Remove any new block comment start
    out = []
    i = 0
    while i < len(line):
        if line.startswith("/*", i):
            # start of block comment
            end = line.find("*/", i + 2)
            if end == -1:
                in_block = True
                break
            i = end + 2
            continue
        if line.startswith("//", i):
            break
        out.append(line[i])
        i += 1
    return "".join(out), in_block


def lint_file(path: Path) -> List[str]:
    issues: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"{path}: Failed to read file: {e}"]

    in_function = False
    in_task = False
    in_block_comment = False
    # Stack of dicts for nested begin..end blocks
    block_stack: List[dict] = []

    lines = text.splitlines()
    for idx, raw in enumerate(lines, start=1):
        # Strip comments
        line, in_block_comment = strip_line_comment(raw, in_block_comment)
        if not line.strip():
            continue

        # Track function/task scope
        if not in_function and FUNCTION_START_RE.search(line):
            in_function = True
        if not in_task and TASK_START_RE.search(line):
            in_task = True
        if in_function and FUNCTION_END_RE.search(line):
            in_function = False
        if in_task and TASK_END_RE.search(line):
            in_task = False
        if in_function or in_task:
            continue  # skip content within functions/tasks

        # Handle 'begin' pushes (allow multiple per line)
        begins_on_line = list(BEGIN_RE.finditer(line))
        for _ in begins_on_line:
            block_stack.append(
                {
                    "first_decl_seen": False,
                    "statement_seen": False,
                    "start_line": idx,
                }
            )

        # If this line opened a new block (e.g. 'if (...) begin'), skip classification
        # to avoid attributing the 'if' statement as inside the new block.
        if block_stack and begins_on_line:
            pass
        elif block_stack:
            top = block_stack[-1]
            is_decl = bool(DECL_RE.search(line))
            # Statement heuristic: anything that looks like control/assign and is not a decl
            is_stmt = bool(STMT_HINT_RE.search(line)) and not is_decl

            if is_stmt and not top["first_decl_seen"]:
                top["statement_seen"] = True

            if is_decl:
                if top["statement_seen"]:
                    issues.append(
                        f"{path}:{idx}: Declaration after statement in block starting at line {top['start_line']}"
                    )
                top["first_decl_seen"] = True

        # Handle 'end*' pops (pop only plain 'end' matching a begin)
        for m in END_ANY_RE.finditer(line):
            token = m.group(0)
            if token == "end" and block_stack:
                block_stack.pop()
            # endcase/endfunction/endtask don't alter the begin..end stack

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint SV templates for declaration order"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when issues are found",
    )
    args = parser.parse_args()

    if not SV_DIR.exists():
        print(f"Templates directory not found: {SV_DIR}")
        return 0

    sv_templates = sorted(SV_DIR.glob("*.sv.j2"))
    all_issues: List[str] = []
    for path in sv_templates:
        all_issues.extend(lint_file(path))

    if all_issues:
        print("\nSV block declaration order check:")
        for msg in all_issues:
            print(f"  - {msg}")
        if args.strict:
            return 1
    else:
        print("SV block declaration order check: OK")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
