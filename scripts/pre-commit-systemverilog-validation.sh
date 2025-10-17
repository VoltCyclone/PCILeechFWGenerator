#!/bin/bash
#
# Pre-commit hook to validate SystemVerilog templates
# This prevents commits that would introduce SystemVerilog syntax issues
#
# To install this hook, copy it to .git/hooks/pre-commit and make it executable:
#   cp scripts/pre-commit-systemverilog-validation.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#

echo " Validating SystemVerilog templates..."

# Check if we're in the right directory
if [ ! -f "scripts/validate_systemverilog_templates.py" ]; then
    echo "SystemVerilog validation script not found. Are you in the project root?"
    exit 1
fi

# Run SystemVerilog template validation
python3 scripts/validate_systemverilog_templates.py
template_result=$?

# Run project SystemVerilog configuration validation
python3 scripts/validate_project_systemverilog.py
project_result=$?

if [ $template_result -ne 0 ] || [ $project_result -ne 0 ]; then
    echo ""
    echo " SystemVerilog validation failed!"
    echo "   Please fix the issues above before committing."
    echo "   This helps prevent synthesis errors in generated firmware."
    exit 1
fi

echo " SystemVerilog validation passed!"
echo ""
