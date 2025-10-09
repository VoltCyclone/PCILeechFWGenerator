#!/bin/bash
# Template validation script for local development
# Usage: ./scripts/check_templates.sh [--strict] [--fix]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default options
STRICT=false
GENERATE_FIXES=false
VERBOSE=false
WARNINGS_AS_ERRORS=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --strict)
            STRICT=true
            shift
            ;;
        --fix|--generate-fixes)
            GENERATE_FIXES=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --warnings-as-errors)
            WARNINGS_AS_ERRORS=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--strict] [--fix] [--verbose] [--warnings-as-errors]"
            echo ""
            echo "Options:"
            echo "  --strict              Exit with error code if critical errors are found"
            echo "  --fix                 Generate suggested fixes"
            echo "  --verbose             Enable verbose output"
            echo "  --warnings-as-errors  Treat warnings as blocking errors"
            echo "  --help                Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}🔍 PCILeech Template Validation${NC}"
echo "=================================="

# Check if we're in the right directory
if [[ ! -f "scripts/validate_template_variables.py" ]]; then
    echo -e "${RED}❌ Error: Must be run from project root directory${NC}"
    exit 1
fi

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Error: python3 not found${NC}"
    exit 1
fi

# Build validation command
VALIDATION_CMD="python3 scripts/validate_template_variables.py --format text"

if [[ "$STRICT" == true ]]; then
    VALIDATION_CMD="$VALIDATION_CMD --strict"
fi

if [[ "$GENERATE_FIXES" == true ]]; then
    VALIDATION_CMD="$VALIDATION_CMD --generate-fixes"
fi

if [[ "$WARNINGS_AS_ERRORS" == true ]]; then
    VALIDATION_CMD="$VALIDATION_CMD --warnings-as-errors"
fi

if [[ "$VERBOSE" == true ]]; then
    VALIDATION_CMD="$VALIDATION_CMD --verbose"
fi

echo -e "${BLUE}📋 Running template variable analysis...${NC}"
echo "Command: $VALIDATION_CMD"
echo ""

# Run the validation (don't exit immediately; aggregate results with SV linter)
VALID_EXIT=0
if ! eval "$VALIDATION_CMD"; then
    VALID_EXIT=$?
fi

# Run the SystemVerilog declaration-order linter
echo ""
echo -e "${BLUE}📐 Running SystemVerilog declaration-order linter...${NC}"
SV_LINT_CMD="python3 scripts/lint_sv_block_decls.py"
if [[ "$STRICT" == true || "$WARNINGS_AS_ERRORS" == true ]]; then
    SV_LINT_CMD="$SV_LINT_CMD --strict"
fi
echo "Command: $SV_LINT_CMD"

SV_LINT_EXIT=0
if ! eval "$SV_LINT_CMD"; then
    SV_LINT_EXIT=$?
fi

echo ""
if [[ $VALID_EXIT -eq 0 && $SV_LINT_EXIT -eq 0 ]]; then
    echo -e "${GREEN}✅ Template validation completed successfully!${NC}"
    exit 0
else
    if [[ "$STRICT" == true || "$WARNINGS_AS_ERRORS" == true || $VALID_EXIT -ne 0 ]]; then
        echo -e "${RED}❌ Template validation failed with issues${NC}"
        # Prefer returning the validation tool's exit code if non-zero; otherwise linter's
        if [[ $VALID_EXIT -ne 0 ]]; then
            exit $VALID_EXIT
        else
            exit $SV_LINT_EXIT
        fi
    else
        echo -e "${YELLOW}⚠️  Template validation found warnings (non-critical)${NC}"
        echo -e "${YELLOW}    Use --strict to treat critical errors as blocking${NC}"
        echo -e "${YELLOW}    Use --warnings-as-errors to treat warnings as blocking${NC}"
        echo -e "${YELLOW}    Use --fix to see suggested fixes${NC}"
        # Non-strict + only linter warnings: keep exit 0
        exit 0
    fi
fi
