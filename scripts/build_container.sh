#!/bin/bash
# Container build and test script for PCILeech Firmware Generator
# Usage: ./scripts/build_container.sh [--test] [--push] [--tag TAG] [--container-engine ENGINE]

set -e

# Default values
CONTAINER_TAG="pcileechfwgenerator:latest"
RUN_TESTS=false
PUSH_IMAGE=false
CONTAINER_ENGINE="podman"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if container engine is available
check_container_engine() {
    # If container engine was explicitly specified, check if it's available
    if [ -n "$CONTAINER_ENGINE" ]; then
        if command -v $CONTAINER_ENGINE &> /dev/null; then
            print_status "Using $CONTAINER_ENGINE as container engine (user specified)"
        else
            print_error "Specified container engine '$CONTAINER_ENGINE' not found. Please install it or choose another engine."
            exit 1
        fi
        return
    fi

    # Auto-detect container engine if not specified
    if command -v podman &> /dev/null; then
        CONTAINER_ENGINE="podman"
        print_status "Using Podman as container engine (auto-detected)"
    elif command -v docker &> /dev/null; then
        CONTAINER_ENGINE="docker"
        print_status "Using Docker as container engine (auto-detected)"
    else
        print_error "Neither Podman nor Docker found. Please install one of them."
        exit 1
    fi
}

# Function to build the container
build_container() {
    print_status "Building container image: $CONTAINER_TAG"
    
    # Build the container with --no-cache to always rebuild
    $CONTAINER_ENGINE build \
        --no-cache \
        -t "$CONTAINER_TAG" \
        -f Containerfile \
        .
    
    if [ $? -eq 0 ]; then
        print_success "Container built successfully"
    else
        print_error "Container build failed"
        exit 1
    fi
}

# Function to run basic tests
run_tests() {
    print_status "Running container tests..."
    
    # Test 1: Health check
    print_status "Testing health check..."
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" python3 -c "import psutil, pydantic; print('Dependencies OK')"
    
    # Test 2: Help command
    print_status "Testing help command..."
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" --help > /dev/null
    
    # Test 3: File structure
    print_status "Testing file structure..."
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" test -f /app/src/build.py
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" test -f /app/pcileech.py
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" test -d /app/output
    
    # Test 4: Python imports
    print_status "Testing Python imports..."
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" python3 -c "
import sys
sys.path.append('/app/src')
try:
    from advanced_sv_main import AdvancedSVGenerator
    from manufacturing_variance import ManufacturingVarianceSimulator
    from behavior_profiler import BehaviorProfiler
    from device_config import get_device_config
    print('All imports successful')
except ImportError as e:
    print(f'Import error: {e}')
    sys.exit(1)
"
    
    # Test 4.2: VFIO constants
    print_status "Testing VFIO constants..."
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" python3 -c "
import sys
sys.path.append('/app/src')
try:
    from cli.vfio_constants import VFIO_GET_API_VERSION, VFIO_DEVICE_GET_INFO
    print(f'VFIO constants loaded: API_VERSION={VFIO_GET_API_VERSION}, DEVICE_GET_INFO={VFIO_DEVICE_GET_INFO}')
    # Verify they are numeric values, not computed functions
    assert isinstance(VFIO_GET_API_VERSION, int), 'VFIO_GET_API_VERSION should be int'
    assert isinstance(VFIO_DEVICE_GET_INFO, int), 'VFIO_DEVICE_GET_INFO should be int'
    print('VFIO constants validation passed')
except Exception as e:
    print(f'VFIO constants error: {e}')
    sys.exit(1)
"
    
    # Test 4.5: Device config with YAML support
    print_status "Testing device config with YAML support..."
    $CONTAINER_ENGINE run --rm "$CONTAINER_TAG" python3 -c "
import sys
sys.path.append('/app/src')
try:
    from device_config import DeviceConfigManager
    manager = DeviceConfigManager()
    config = manager.get_profile('audio_controller')
    print(f'Successfully loaded device config: {config.name}')
except Exception as e:
    print(f'Device config error: {e}')
    sys.exit(1)
"
    
    # Test 5: User permissions
    print_status "Testing user permissions..."
    USER_ID=$($CONTAINER_ENGINE run --rm "$CONTAINER_TAG" id -u)
    if [ "$USER_ID" = "0" ]; then
        print_warning "Container is running as root user"
    else
        print_success "Container running as non-root user (ID: $USER_ID)"
    fi
    
    # Test 6: Volume mount test
    print_status "Testing volume mounts..."
    mkdir -p ./test-output
    chmod 777 ./test-output
    $CONTAINER_ENGINE run --rm \
        -v ./test-output:/app/output \
        "$CONTAINER_TAG" \
        touch /app/output/test-file.txt
    
    if [ -f ./test-output/test-file.txt ]; then
        print_success "Volume mount test passed"
        rm -f ./test-output/test-file.txt
        rmdir ./test-output
    else
        print_error "Volume mount test failed"
        exit 1
    fi
    
    print_success "All tests passed!"
}

# Function to push image (if using a registry)
push_image() {
    print_status "Pushing image: $CONTAINER_TAG"
    $CONTAINER_ENGINE push "$CONTAINER_TAG"
    
    if [ $? -eq 0 ]; then
        print_success "Image pushed successfully"
    else
        print_error "Image push failed"
        exit 1
    fi
}

# Function to show usage examples
show_usage_examples() {
    print_status "Container build completed. Usage examples:"
    echo
    echo "Basic usage:"
    echo "  $CONTAINER_ENGINE run --rm -it $CONTAINER_TAG --help"
    echo
    echo "With specific capabilities (recommended):"
    echo "  $CONTAINER_ENGINE run --rm -it \\"
    echo "    --cap-add=SYS_RAWIO --cap-add=SYS_ADMIN \\"
    echo "    --device=/dev/vfio/GROUP --device=/dev/vfio/vfio \\"
    echo "    -v ./output:/app/output \\"
    echo "    $CONTAINER_TAG \\"
    echo "    sudo python3 /app/src/build.py --bdf 0000:03:00.0 --board pcileech_35t325_x1"
    echo
    echo "With privileged access (less secure):"
    echo "  $CONTAINER_ENGINE run --rm -it --privileged \\"
    echo "    --device=/dev/vfio/GROUP --device=/dev/vfio/vfio \\"
    echo "    -v ./output:/app/output \\"
    echo "    $CONTAINER_TAG \\"
    echo "    sudo python3 /app/src/build.py --bdf 0000:03:00.0 --board pcileech_35t325_x1"
    echo
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --test)
            RUN_TESTS=true
            shift
            ;;
        --push)
            PUSH_IMAGE=true
            shift
            ;;
        --tag)
            CONTAINER_TAG="$2"
            shift 2
            ;;
        --container-engine)
            CONTAINER_ENGINE="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo
            echo "Options:"
            echo "  --test                  Run tests after building"
            echo "  --push                  Push image to registry after building"
            echo "  --tag TAG               Use custom tag (default: pcileechfwgenerator:latest)"
            echo "  --container-engine ENG  Specify container engine to use (podman or docker)"
            echo "  --help, -h              Show this help message"
            echo
            echo "Examples:"
            echo "  $0                           # Build container"
            echo "  $0 --test                    # Build and test container"
            echo "  $0 --tag myregistry/pcileech:v0.7.4 --push  # Build, tag, and push"
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Main execution
print_status "PCILeech Firmware Generator Container Build Script"
print_status "=================================================="

# Check prerequisites
check_container_engine

# Build the container
build_container

# Run tests if requested
if [ "$RUN_TESTS" = true ]; then
    run_tests
fi

# Push image if requested
if [ "$PUSH_IMAGE" = true ]; then
    push_image
fi

# Show usage examples
show_usage_examples

print_success "Container build process completed successfully!"