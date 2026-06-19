#!/bin/bash
#
# Guard against raw f-string logging in src/ (outside the TUI subtree).
#
# The project standardizes on the safe logging helpers in src/string_utils.py:
#   log_*_safe(logger, safe_format("msg {x}", x=val), prefix="...")
# Raw f-string logging (logger.info(f"...")) evaluates eagerly and bypasses the
# project's missing-key tolerance and prefix formatting. src/tui/ uses its own
# logging convention and is excluded here (opt-in migration tracked separately).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Match logger.<level>(f"  or  logger.<level>(f'
PATTERN='logger\.(debug|info|warning|error|critical|exception)\(\s*f["'"'"']'

hits="$(grep -rnE "$PATTERN" src/ --include='*.py' --exclude-dir=tui || true)"

if [ -n "$hits" ]; then
    echo "ERROR: raw f-string logging found (use log_*_safe + safe_format):"
    echo "$hits"
    exit 1
fi

echo "OK: no raw f-string logging outside src/tui/"
