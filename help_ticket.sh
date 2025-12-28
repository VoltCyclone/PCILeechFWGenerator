#!/bin/bash
# PCILeech Firmware Generator - Help Ticket Information Collector
# This script collects diagnostic information for bug reports and support tickets

set -e

# Colors and styles
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# Output file
OUTPUT_FILE="pcileech_ticket_$(date +%Y%m%d_%H%M%S).txt"

# ASCII Art Banner
clear
echo -e "${CYAN}${BOLD}"
cat << "EOF"
    ____  __________    __                 __  
   / __ \/ ____/  _/   / /   ___  ___  ___/ /_ 
  / /_/ / /    / /    / /   / _ \/ _ \/ __/ __ \
 / ____/ /____/ /    / /___/  __/  __/ /_/ / / /
/_/    \____/___/   /_____/\___/\___/\__/_/ /_/ 
                                                 
       🔧 HELP TICKET DIAGNOSTICS COLLECTOR 🔧
EOF
echo -e "${NC}"
echo -e "${DIM}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}${BOLD}⚡ Scanning your system for diagnostic information...${NC}"
echo -e "${DIM}═══════════════════════════════════════════════════════════════════${NC}"
echo ""

# Helper function to show progress
show_progress() {
    echo -e "${CYAN}⟳${NC} $1..." >&2
}

# Start collecting information
{
    echo "PCILeech Firmware Generator - Help Ticket Information"
    echo "Generated: $(date)"
    echo "========================================================================"
    echo ""
    
    show_progress "Gathering system information"
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
    
    show_progress "Checking Python environment"
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
    
    show_progress "Analyzing repository status"
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
    
    show_progress "Detecting container runtime"
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
    echo ""
    
    show_progress "Scanning VFIO/IOMMU configuration"
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
    
    show_progress "Enumerating PCI devices"
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
    
    show_progress "Examining datastore contents"
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
    
    show_progress "Reading recent build logs"
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
    
    show_progress "Inventorying Python packages"
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
    
    show_progress "Verifying submodule status"
    # Submodule Status
    echo "## SUBMODULE DETAILED STATUS"
    echo "========================================================================"
    if [ -d "lib/voltcyclone-fpga" ]; then
        echo "voltcyclone-fpga submodule: Present"
        echo "Path: lib/voltcyclone-fpga"
        if cd lib/voltcyclone-fpga 2>/dev/null; then
            echo "Branch: $(git branch --show-current 2>&1 || echo 'Unknown')"
            echo "Commit: $(git log -1 --oneline 2>&1 || echo 'Unknown')"
            echo "Boards found:"
            ls -d */ 2>&1 | head -15 || echo "Cannot list directories"
            cd - > /dev/null || echo "Warning: cannot return to previous directory"
        else
            echo "Cannot enter lib/voltcyclone-fpga directory (permission or other error)"
        fi
    else
        echo "voltcyclone-fpga submodule: Not found"
    fi
    echo ""
    
    show_progress "Checking disk space"
    # Disk Space
    echo "## DISK SPACE"
    echo "========================================================================"
    df -h . 2>&1 || echo "Cannot get disk space"
    echo ""
    
    # End of report
    echo "========================================================================"
    echo "End of Help Ticket Information"
    echo "========================================================================"
    
} > "$OUTPUT_FILE"

# Animated completion
echo -ne "${YELLOW}▓"
for i in {1..50}; do
    echo -ne "▓"
    sleep 0.02
done
echo -e "${NC}"
echo ""

# Success banner
echo -e "${GREEN}${BOLD}"
cat << "EOF"
    DIAGNOSTIC SCAN COMPLETE
EOF
echo -e "${NC}"
echo ""
echo -e "${CYAN}┌─────────────────────────────────────────────────────────────────┐${NC}"
echo -e "${CYAN}│${NC} ${BOLD}Output File:${NC} ${YELLOW}$OUTPUT_FILE${NC}"
echo -e "${CYAN}│${NC} ${BOLD}File Size:${NC}   ${GREEN}$(wc -c < "$OUTPUT_FILE" | awk '{print int($1/1024)"KB"}')${NC}"
echo -e "${CYAN}│${NC} ${BOLD}Timestamp:${NC}   ${BLUE}$(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${CYAN}└─────────────────────────────────────────────────────────────────┘${NC}"
echo ""
echo -e "${MAGENTA}${BOLD}NEXT STEPS:${NC}"
echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${CYAN}1.${NC} Review ${YELLOW}$OUTPUT_FILE${NC} for sensitive information"
echo -e "  ${CYAN}2.${NC} Attach the file to your ${BOLD}GitHub issue${NC} or support ticket"
echo -e "  ${CYAN}3.${NC} Include your ${BOLD}error message${NC} and ${BOLD}reproduction steps${NC}"
echo ""
echo -e "${RED}${BOLD}WARNING:${NC} ${DIM}This file may contain system information - review before sharing${NC}"
echo ""
echo -e "${BLUE}${BOLD}Report issues at:${NC} ${CYAN}https://github.com/voltcyclone/PCILeechFWGenerator/issues${NC}"
echo ""
