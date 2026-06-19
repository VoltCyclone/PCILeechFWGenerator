#!/bin/bash
#
# Guard against test import anti-patterns.
#
#  1. `from src ...` / `import src...` — tests must import the installed package
#     `pcileechfwgenerator.*`, never the bare `src` path (that creates a duplicate
#     module identity and breaks mock-patch targets).
#  2. Module-level `sys.path.insert(...)` — redundant given pytest.ini's
#     `pythonpath = .`. The only allowed use is in-body inserts that are the
#     subject under test (test_cli_build_wrapper.py).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

status=0

src_hits="$(grep -rnE '^(from|import) src(\.| import)' tests/ --include='*.py' || true)"
if [ -n "$src_hits" ]; then
    echo "ERROR: 'from src' / 'import src' in tests — use pcileechfwgenerator.* instead:"
    echo "$src_hits"
    status=1
fi

# Module-level (column 0) sys.path.insert is redundant under pythonpath = .
syspath_hits="$(grep -rnE '^sys\.path\.insert' tests/ --include='*.py' || true)"
if [ -n "$syspath_hits" ]; then
    echo "ERROR: module-level sys.path.insert in tests (redundant under pytest.ini pythonpath = .):"
    echo "$syspath_hits"
    status=1
fi

if [ "$status" -eq 0 ]; then
    echo "OK: no 'from src' imports or module-level sys.path.insert in tests"
fi
exit "$status"
