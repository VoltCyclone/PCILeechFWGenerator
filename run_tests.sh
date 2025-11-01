#!/bin/bash
# Test runner script for PCILeech TUI integration tests

set -e

echo "🧪 PCILeech TUI Test Runner"
echo "=========================="

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
echo "📦 Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements-dev.txt

# Run TUI tests specifically
echo "🎯 Running TUI integration tests..."
pytest tests/test_tui_integration.py -v --tb=short -m tui

# Run all tests except hardware
echo "🔬 Running all tests (excluding hardware)..."
pytest tests/ -v -m "not hardware" --cov=src --cov-report=term-missing

echo "✅ All tests completed!"
