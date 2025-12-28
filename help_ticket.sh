#!/bin/bash
# PCILeech Firmware Generator - Help Ticket Information Collector
# This script collects diagnostic information for bug reports and support tickets

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Output file
OUTPUT_FILE="pcileech_ticket_$(date +%Y%m%d_%H%M%S).txt"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  PCILeech Firmware Generator - Help Ticket Collector          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Start collecting information
{
    echo "PCILeech Firmware Generator - Help Ticket Information"
    echo "Generated: $(date)"
    echo "========================================================================"
    echo ""
    
    # System Information
    echo "## SYSTEM INFORMATION"
    echo "========================================================================"
    echo "Hostname: $(hostname)"
    echo "OS: $(uname -s)"
    echo "Kernel: $(uname -r)"
    echo "Architecture: $(uname -m)"
    if command -v lsb_release &> /dev/null; then
        echo "Distribution: $(lsb_release -ds 2>/dev/null || echo 'N/A')"
    fi
    echo ""
    
    # Python Information
    echo "## PYTHON ENVIRONMENT"
    echo "========================================================================"
    echo "Python Version: $(python3 --version 2>&1 || echo 'Not found')"
    echo "Python Path: $(which python3 2>&1 || echo 'Not found')"
    if [ -d "$HOME/.pcileech-venv" ]; then
        echo "Virtual Environment: ~/.pcileech-venv (exists)"
        if [ -f "$HOME/.pcileech-venv/bin/python3" ]; then
            echo "Venv Python: $($HOME/.pcileech-venv/bin/python3 --version 2>&1)"
        fi
    else
        echo "Virtual Environment: Not found"
    fi
    echo ""
    
    # Git Information
    echo "## REPOSITORY INFORMATION"
    echo "========================================================================"
    if [ -d ".git" ]; then
        echo "Current Branch: $(git branch --show-current 2>&1 || echo 'Unknown')"
        echo "Latest Commit: $(git log -1 --oneline 2>&1 || echo 'Unknown')"
        echo "Submodule Status:"
        git submodule status 2>&1 || echo "Failed to get submodule status"
    else
        echo "Not a git repository"
    fi
    echo ""
    
    # Container Runtime
    echo "## CONTAINER RUNTIME"
    echo "========================================================================"
    if command -v podman &> /dev/null; then
        echo "Podman: $(podman --version 2>&1)"
        echo "Podman Images:"
        podman images | grep -E "pcileech|REPOSITORY" || echo "No pcileech images found"
    else
        echo "Podman: Not installed"
    fi
    if command -v docker &> /dev/null; then
        echo "Docker: $(docker --version 2>&1)"
    else
        echo "Docker: Not installed"
    fi
    echo ""
    
    # VFIO/IOMMU Information
    echo "## VFIO/IOMMU STATUS"
    echo "========================================================================"
    if [ -e /dev/vfio/vfio ]; then
        echo "VFIO Device: Present"
        ls -l /dev/vfio/ 2>&1 || echo "Cannot list /dev/vfio"
    else
        echo "VFIO Device: Not found"
    fi
    echo ""
    echo "IOMMU Groups:"
    if [ -d /sys/kernel/iommu_groups ]; then
        ls /sys/kernel/iommu_groups/ 2>&1 || echo "Cannot list IOMMU groups"
    else
        echo "IOMMU groups not found"
    fi
    echo ""
    echo "Loaded VFIO Modules:"
    lsmod | grep vfio 2>&1 || echo "No VFIO modules loaded"
    echo ""
    
    # PCI Devices
    echo "## PCI DEVICES"
    echo "========================================================================"
    if command -v lspci &> /dev/null; then
        echo "All PCI Devices:"
        lspci -nn 2>&1 || echo "Failed to list PCI devices"
    else
        echo "lspci command not found"
    fi
    echo ""
    
    # Datastore Information
    echo "## DATASTORE STATUS"
    echo "========================================================================"
    if [ -d "pcileech_datastore" ]; then
        echo "Datastore Directory: Exists"
        echo "Permissions:"
        ls -la pcileech_datastore/ 2>&1 || echo "Cannot list datastore"
        echo ""
        if [ -d "pcileech_datastore/output" ]; then
            echo "Output Directory: Exists"
            echo "Output Permissions:"
            ls -la pcileech_datastore/output/ 2>&1 || echo "Cannot list output"
            echo ""
            echo "Output Contents:"
            find pcileech_datastore/output -type f 2>&1 | head -20 || echo "Cannot list contents"
        else
            echo "Output Directory: Not found"
        fi
        echo ""
        if [ -f "pcileech_datastore/device_context.json" ]; then
            echo "Device Context: Present ($(wc -c < pcileech_datastore/device_context.json) bytes)"
        else
            echo "Device Context: Not found"
        fi
        if [ -f "pcileech_datastore/msix_data.json" ]; then
            echo "MSI-X Data: Present ($(wc -c < pcileech_datastore/msix_data.json) bytes)"
        else
            echo "MSI-X Data: Not found"
        fi
    else
        echo "Datastore Directory: Not found"
    fi
    echo ""
    
    # Recent Logs (if available)
    echo "## RECENT BUILD LOGS"
    echo "========================================================================"
    if [ -d "logs" ]; then
        echo "Recent log files:"
        ls -lth logs/*.log 2>&1 | head -5 || echo "No log files found"
        echo ""
        latest_log=$(ls -t logs/*.log 2>/dev/null | head -1)
        if [ -n "$latest_log" ]; then
            echo "Last 50 lines from $latest_log:"
            tail -50 "$latest_log" 2>&1 || echo "Cannot read log file"
        fi
    else
        echo "No logs directory found"
    fi
    echo ""
    
    # Installed Python Packages
    echo "## INSTALLED PYTHON PACKAGES"
    echo "========================================================================"
    if [ -f "$HOME/.pcileech-venv/bin/pip" ]; then
        echo "Packages in virtual environment:"
        $HOME/.pcileech-venv/bin/pip list 2>&1 || echo "Cannot list packages"
    else
        echo "Virtual environment pip not found"
    fi
    echo ""
    
    # Submodule Status
    echo "## SUBMODULE DETAILED STATUS"
    echo "========================================================================"
    if [ -d "lib/voltcyclone-fpga" ]; then
        echo "voltcyclone-fpga submodule: Present"
        echo "Path: lib/voltcyclone-fpga"
        cd lib/voltcyclone-fpga
        echo "Branch: $(git branch --show-current 2>&1 || echo 'Unknown')"
        echo "Commit: $(git log -1 --oneline 2>&1 || echo 'Unknown')"
        echo "Boards found:"
        ls -d */ 2>&1 | head -15 || echo "Cannot list directories"
        cd - > /dev/null
    else
        echo "voltcyclone-fpga submodule: Not found"
    fi
    echo ""
    
    # Disk Space
    echo "## DISK SPACE"
    echo "========================================================================"
    df -h . 2>&1 || echo "Cannot get disk space"
    echo ""
    
    # End of report
    echo "========================================================================"
    echo "End of Help Ticket Information"
    echo "========================================================================"
    
} > "$OUTPUT_FILE" 2>&1

echo -e "${GREEN}✓ Information collected successfully${NC}"
echo -e "${BLUE}Output saved to: ${YELLOW}$OUTPUT_FILE${NC}"
echo ""
echo -e "${BLUE}To submit this information:${NC}"
echo "  1. Review the file to ensure no sensitive information is included"
echo "  2. Attach the file to your GitHub issue or support ticket"
echo "  3. Include your specific error message and reproduction steps"
echo ""
echo -e "${YELLOW}Note: Review $OUTPUT_FILE before sharing - it may contain system information${NC}"
