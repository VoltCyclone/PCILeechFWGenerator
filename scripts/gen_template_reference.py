#!/usr/bin/env python3
"""
Generate a Template Variables Reference page from template_variables_report.json.

Output: site/docs/template-reference.md

This script reads the top-level template_variables_report.json and writes a
compact Markdown document using collapsible <details> sections per template.

Usage:
  python3 scripts/gen_template_reference.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

# Prefer in-repo utilities for logging/formatting
try:
    from src.string_utils import (log_error_safe, log_info_safe,
                                  log_warning_safe, safe_format)
except Exception:
    # Lightweight shims matching the project signatures

    def safe_format(template: str, prefix: str | None = None, **kwargs: Any) -> str:
        try:
            msg = template.format(**kwargs)
            return f"[{prefix}] {msg}" if prefix else msg
        except Exception:
            return template

    def _emit(level: str, template: str, prefix: str | None, **kwargs: Any) -> None:
        msg = safe_format(template, prefix=prefix, **kwargs)
        stream = sys.stderr if level in {"ERROR", "WARNING"} else sys.stdout
        print(msg, file=stream)

    def log_info_safe(logger: logging.Logger, template: str, prefix: str | None = None, **kwargs: Any) -> None:  # type: ignore[override]
        _emit("INFO", template, prefix, **kwargs)

    def log_warning_safe(logger: logging.Logger, template: str, prefix: str | None = None, **kwargs: Any) -> None:  # type: ignore[override]
        _emit("WARNING", template, prefix, **kwargs)

    def log_error_safe(logger: logging.Logger, template: str, prefix: str | None = None, **kwargs: Any) -> None:  # type: ignore[override]
        _emit("ERROR", template, prefix, **kwargs)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(REPO_ROOT, "template_variables_report.json")
DOCS_OUT = os.path.join(REPO_ROOT, "site", "docs", "template-reference.md")

LOGGER = logging.getLogger("pcileech.docs")


def read_report(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        log_error_safe(LOGGER, "Missing report file: {p}", p=path)
        raise SystemExit(2)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _md_escape(text: str) -> str:
    # Minimal escaping for Markdown special chars in inline code/labels
    return text.replace("<", "&lt;").replace(">", "&gt;")


def build_markdown(report: Dict[str, Any]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    total = report.get("total_variables")
    safe_count = report.get("safely_handled")
    unsafe: List[str] = report.get("unsafe_variables", []) or []
    unsafe_defaults: List[str] = report.get("variables_with_unsafe_defaults", []) or []
    by_template: Dict[str, List[Dict[str, Any]]] = report.get(
        "variables_by_template", {}
    )

    # Header
    lines: List[str] = []
    lines.append("# Template Variables Reference")
    lines.append("")
    lines.append(
        safe_format("Generated from template_variables_report.json — {now}", now=now)
    )
    lines.append("")
    lines.append(
        "This page lists variables used by templates, their safety and fallback status, and origins. Use the search in your browser to jump to a specific variable or template."
    )
    lines.append("")
    # Summary block
    lines.append("## Summary")
    lines.append("")
    lines.append(safe_format("- Total variables: {n}", n=total))
    lines.append(safe_format("- Safely handled: {n}", n=safe_count))
    if unsafe:
        lines.append(
            safe_format("- Unsafe variables: {items}", items=", ".join(sorted(unsafe)))
        )
    else:
        lines.append("- Unsafe variables: None")
    if unsafe_defaults:
        lines.append(
            safe_format(
                "- Variables with potentially unsafe defaults: {items}",
                items=", ".join(sorted(unsafe_defaults)),
            )
        )
    else:
        lines.append("- Variables with potentially unsafe defaults: None")
    lines.append("")

    # Quick index by template
    lines.append("## Templates Index")
    lines.append("")
    for tpl in sorted(by_template.keys()):
        anchor = tpl.lower().replace(" ", "-").replace("/", "-")
        lines.append(safe_format("- [{tpl}](#{anchor})", tpl=tpl, anchor=anchor))
    lines.append("")

    # Details per template
    for tpl in sorted(by_template.keys()):
        anchor = tpl.lower().replace(" ", "-").replace("/", "-")
        variables = by_template.get(tpl, [])
        lines.append(safe_format('\n<a id="{anchor}"></a>', anchor=anchor))
        lines.append(safe_format("## {tpl}", tpl=tpl))
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>Show variables</summary>")
        lines.append("")
        if not variables:
            lines.append("No variables found.")
        else:
            # Compact table header
            lines.append(
                "| Name | Safe | Fallback | Default in Template | Defined In | Unsafe Defaults |"
            )
            lines.append(
                "|------|------|----------|---------------------|------------|------------------|"
            )
            for v in sorted(variables, key=lambda x: (str(x.get("name")))):
                name = _md_escape(str(v.get("name")))
                is_safe = "yes" if v.get("is_safe") else "no"
                has_fallback = "yes" if v.get("has_fallback") else "no"
                has_default = "yes" if v.get("has_default_in_template") else "no"
                defined_in = _md_escape(
                    ", ".join(v.get("defined_in", []))
                    if isinstance(v.get("defined_in"), list)
                    else str(v.get("defined_in", ""))
                )
                udefs_raw = v.get("unsafe_defaults", []) or []
                udefs = _md_escape(", ".join(map(str, udefs_raw))) if udefs_raw else ""
                lines.append(
                    safe_format(
                        "| `{name}` | {safe} | {fallback} | {default} | {defined} | {udefs} |",
                        name=name,
                        safe=is_safe,
                        fallback=has_fallback,
                        default=has_default,
                        defined=defined_in,
                        udefs=udefs,
                    )
                )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append("")
    lines.append("---")
    lines.append(
        "Note: This page is auto-generated. To update, run `python3 scripts/gen_template_reference.py`."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    try:
        report = read_report(REPORT_PATH)
        content = build_markdown(report)
        os.makedirs(os.path.dirname(DOCS_OUT), exist_ok=True)
        with open(DOCS_OUT, "w", encoding="utf-8") as f:
            f.write(content)
        log_info_safe(
            LOGGER,
            "Wrote template reference to {p}",
            p=os.path.relpath(DOCS_OUT, REPO_ROOT),
        )
        return 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        return code
    except Exception as e:
        log_error_safe(LOGGER, "Failed to generate template reference: {e}", e=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
