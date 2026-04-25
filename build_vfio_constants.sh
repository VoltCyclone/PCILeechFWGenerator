#!/bin/bash
# build_vfio_constants.sh - Build and patch VFIO constants for both host and container

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if we're in a container
is_container() {
    [ -f /.dockerenv ] || [ -f /run/.containerenv ] || grep -q 'container=podman' /proc/1/environ 2>/dev/null
}

# Check if we're running as privileged (needed for VFIO access)
is_privileged() {
    [ -c /dev/vfio/vfio ] && [ -r /dev/vfio/vfio ] && [ -w /dev/vfio/vfio ]
}

# Install kernel headers based on the environment
install_kernel_headers() {
    local kernel_version=$(uname -r)
    log_info "Installing kernel headers for kernel version: $kernel_version"
    
    if is_container; then
        log_info "Running in container - installing headers via package manager"
        
        # Detect package manager and install headers
        if command -v dnf >/dev/null 2>&1; then
            # Fedora/RHEL/CentOS
            log_info "Using dnf to install kernel headers"
            dnf install -y "kernel-headers-${kernel_version}" || \
            dnf install -y kernel-headers || {
                log_error "Failed to install kernel headers via dnf"
                return 1
            }
        elif command -v yum >/dev/null 2>&1; then
            # Older RHEL/CentOS
            log_info "Using yum to install kernel headers"
            yum install -y "kernel-headers-${kernel_version}" || \
            yum install -y kernel-headers || {
                log_error "Failed to install kernel headers via yum"
                return 1
            }
        elif command -v apt-get >/dev/null 2>&1; then
            # Debian/Ubuntu
            log_info "Using apt-get to install kernel headers"
            apt-get update
            apt-get install -y "linux-headers-${kernel_version}" || \
            apt-get install -y linux-headers-generic || {
                log_error "Failed to install kernel headers via apt-get"
                return 1
            }
        elif command -v pacman >/dev/null 2>&1; then
            # Arch Linux
            log_info "Using pacman to install kernel headers"
            pacman -Sy --noconfirm "linux-headers" || {
                log_error "Failed to install kernel headers via pacman"
                return 1
            }
        elif command -v zypper >/dev/null 2>&1; then
            # openSUSE
            log_info "Using zypper to install kernel headers"
            zypper install -y "kernel-devel" || {
                log_error "Failed to install kernel headers via zypper"
                return 1
            }
        else
            log_error "No supported package manager found (dnf, yum, apt-get, pacman, zypper)"
            return 1
        fi
    else
        log_info "Running on host - checking for existing headers"
        
        # On host, headers should already be installed
        local header_paths=(
            "/usr/src/kernels/${kernel_version}"
            "/lib/modules/${kernel_version}/build"
            "/usr/src/linux-headers-${kernel_version}"
        )
        
        local found_headers=false
        for path in "${header_paths[@]}"; do
            if [ -d "$path" ]; then
                log_success "Found kernel headers at: $path"
                found_headers=true
                break
            fi
        done
        
        if [ "$found_headers" = false ]; then
            log_error "Kernel headers not found. Please install them:"
            log_error "  Fedora/RHEL: sudo dnf install kernel-headers-\$(uname -r)"
            log_error "  Ubuntu/Debian: sudo apt-get install linux-headers-\$(uname -r)"
            log_error "  Arch Linux: sudo pacman -S linux-headers"
            log_error "  openSUSE: sudo zypper install kernel-devel"
            return 1
        fi
    fi
    
    log_success "Kernel headers are available"
}

# Verify VFIO availability
check_vfio() {
    log_info "Checking VFIO availability..."
    
    if [ ! -c /dev/vfio/vfio ]; then
        log_error "/dev/vfio/vfio device not found"
        log_error "Make sure VFIO is loaded and container has --device=/dev/vfio/vfio"
        return 1
    fi
    
    if ! is_privileged; then
        log_warning "Cannot access /dev/vfio/vfio - may need --privileged flag"
        log_warning "Continuing anyway - constants can still be extracted"
    else
        log_success "VFIO device accessible"
    fi
}

# Build the helper and patch constants
build_and_patch() {
    log_info "Building VFIO helper and patching constants..."
    
    # Ensure we're in the right directory
    if [ ! -f "src/cli/vfio_constants.py" ]; then
        log_error "Must run from project root (src/cli/vfio_constants.py not found)"
        return 1
    fi
    
    # Check required files exist
    for file in "vfio_helper.c" "patch_vfio_constants.py"; do
        if [ ! -f "$file" ]; then
            log_error "Required file not found: $file"
            return 1
        fi
    done
    
    # Check if gcc is available
    if ! command -v gcc >/dev/null 2>&1; then
        log_error "gcc compiler not found - please install build tools"
        if command -v dnf >/dev/null 2>&1; then
            log_error "  Fedora/RHEL: sudo dnf install gcc"
        elif command -v apt-get >/dev/null 2>&1; then
            log_error "  Ubuntu/Debian: sudo apt-get install build-essential"
        fi
        return 1
    fi
    
    # Check if Python 3 is available
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "python3 not found - please install Python 3.8 or later"
        return 1
    fi
    
    # Make the patcher executable
    chmod +x patch_vfio_constants.py
    
    # Run the patcher (it handles compilation internally)
    log_info "Running Python patcher..."
    if ! python3 patch_vfio_constants.py; then
        log_error "VFIO constants patching failed"
        return 1
    fi
    
    log_success "VFIO constants patched successfully!"
}

# Main function
main() {
    log_info "VFIO Constants Builder"
    log_info "====================="
    
    # Show environment info
    log_info "Kernel version: $(uname -r)"
    log_info "Architecture: $(uname -m)"
    if is_container; then
        log_info "Environment: Container"
    else
        log_info "Environment: Host"
    fi
    
    # Install kernel headers if needed
    if ! install_kernel_headers; then
        log_error "Failed to install/verify kernel headers"
        exit 1
    fi
    
    # Check VFIO (non-fatal)
    check_vfio || true
    
    # Build and patch
    if ! build_and_patch; then
        log_error "Failed to build and patch VFIO constants"
        exit 1
    fi
    
    log_success "All done! Your vfio_constants.py now has kernel-correct ioctl numbers."
}

# Show usage if requested
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat << EOF
VFIO Constants Builder

This script builds the VFIO helper program and patches vfio_constants.py
with the correct ioctl numbers for your running kernel.

Usage:
  $0                    # Auto-detect environment and build
  $0 --help            # Show this help

Environment Support:
  - Host system with kernel headers installed
  - Privileged container with kernel headers
  - Container with bind-mounted headers

Requirements:
  - gcc compiler
  - Python 3.8+
  - Kernel headers matching running kernel
  - Access to /dev/vfio/vfio (for verification)

Examples:
  # Host build (headers pre-installed)
  sudo dnf install kernel-headers-\$(uname -r)
  ./build_vfio_constants.sh

  # Container build (install headers)
  podman run --privileged --device=/dev/vfio/vfio \\
    -v \$(pwd):/workspace -w /workspace \\
    pcileechfwgenerator ./build_vfio_constants.sh

EOF
    exit 0
fi

# Run main function
main "$@"