#!/bin/bash
set -e

# Display minimal usage information
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    VERSION=$(python3 /app/get_version.py)
    echo "$VERSION"
    echo "Usage: podman run --rm -it --cap-add=SYS_RAWIO --cap-add=SYS_ADMIN \\"
    echo "         --device=/dev/vfio/GROUP --device=/dev/vfio/vfio \\"
    echo "         -v ./output:/app/output dma-fw \\"
    echo "         sudo python3 /app/pcileech.py [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands: build | tui | flash | check | version"
    echo ""
    echo "Examples:"
    echo "  sudo python3 /app/pcileech.py build --bdf 0000:03:00.0 --board pcileech_35t325_x1"
    echo "  sudo python3 /app/pcileech.py tui"
    echo "  sudo python3 /app/pcileech.py check --device 0000:03:00.0"
    echo ""
    echo "For detailed documentation, see: /app/site/"
    exit 0
fi

# Decide whether to perform any VFIO/module operations inside the container
SHOULD_SKIP_VFIO=0

# Respect explicit env toggles from host orchestrator
case "${PCILEECH_HOST_CONTEXT_ONLY:-0}" in
    1|true|TRUE|yes|on) SHOULD_SKIP_VFIO=1 ;;
esac
case "${PCILEECH_DISABLE_VFIO:-0}" in
    1|true|TRUE|yes|on) SHOULD_SKIP_VFIO=1 ;;
esac

# Auto-detect conditions where VFIO is not viable inside the container
# - /dev/vfio/vfio missing or not writable
# - /sys is read-only (common in rootless/containerized environments)
if [ ! -w /dev/vfio/vfio ] || mount | grep -qE "on /sys .*\(ro[),]"; then
    SHOULD_SKIP_VFIO=1
fi

if [ "$SHOULD_SKIP_VFIO" -eq 0 ]; then
    # Validate and rebuild VFIO constants if needed
    if [ "${REBUILD_VFIO_CONSTANTS:-false}" = "true" ]; then
        echo "Rebuilding VFIO constants for runtime kernel..."
        if [ -f /app/build_vfio_constants.sh ]; then
            cd /app && ./build_vfio_constants.sh || {
                echo "Error: VFIO constants rebuild failed" >&2
                exit 1
            }
        else
            echo "Error: VFIO constants build script not found" >&2
            exit 1
        fi
    fi

    # Validate and load required VFIO modules
    if ! modprobe -q vfio_iommu_type1; then
        echo "Warning: Failed to load vfio_iommu_type1 module" >&2
    fi

    # Validate VFIO module availability before configuring
    if [ -d /sys/module/vfio_iommu_type1 ]; then
        # Enable unsafe-interrupts in this mount namespace
        if echo 1 > /sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts 2>/dev/null; then
            # Print current value for verification
            printf "vfio_iommu_type1.allow_unsafe_interrupts = %s\n" \
                   "$(cat /sys/module/vfio_iommu_type1/parameters/allow_unsafe_interrupts 2>/dev/null || echo 'unavailable')"
        else
            echo "Warning: Could not configure vfio_iommu_type1 unsafe interrupts" >&2
        fi
    else
        echo "Warning: vfio_iommu_type1 module not available" >&2
    fi
else
    echo "Info: Skipping VFIO/module operations (host-context-only or unsupported container environment)" >&2
fi

# Execute the command
exec "$@"