#!/usr/bin/env python3
"""
VFIO Container Manager - PCI device management for containerized builds.

This script provides complete VFIO device management including:
- Device binding/unbinding to vfio-pci driver
- IOMMU group management
- Container runtime configuration
- Device state persistence and restoration
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from typing import Dict, List, Optional, Tuple

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.string_utils import (
    log_debug_safe,
    log_error_safe,
    log_info_safe,
    log_warning_safe,
    safe_format,
)
from src.cli.vfio_handler import (
    VFIOBinder,
    DeviceInfo,
    VFIOBindError,
    VFIOPermissionError,
)
from src.cli.vfio_helpers import check_vfio_prerequisites

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _resolve_state_dir() -> Path:
    """Resolve a writable state directory with fallbacks."""
    override = os.environ.get("PCILEECH_VFIO_STATE_DIR")
    candidates: List[Path] = []

    if override:
        candidates.append(Path(override))

    candidates.append(Path("/var/lib/vfio-container-manager"))

    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        candidates.append(Path(xdg_state) / "pcileech" / "vfio-container-manager")

    candidates.append(
        Path.home() / ".local" / "state" / "pcileech" / "vfio-container-manager"
    )
    candidates.append(Path(tempfile.gettempdir()) / "pcileech-vfio-state")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except PermissionError as exc:
            log_warning_safe(
                logger,
                safe_format(
                    "Cannot access state dir {path}: {error}",
                    path=str(candidate),
                    error=exc,
                ),
                prefix="STATE",
            )
        except OSError as exc:
            log_warning_safe(
                logger,
                safe_format(
                    "Failed to initialize state dir {path}: {error}",
                    path=str(candidate),
                    error=exc,
                ),
                prefix="STATE",
            )

    # Final fallback into current working directory
    fallback = Path.cwd() / ".pcileech-vfio-state"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


STATE_DIR = _resolve_state_dir()

CONTAINER_STATE_FILE = STATE_DIR / "container_state.json"
DEVICE_STATE_FILE = STATE_DIR / "device_state.json"

# Container configuration
DEFAULT_CONTAINER_IMAGE = "pcileechfwgenerator"
DEFAULT_CONTAINER_TAG = "latest"
REQUIRED_CONTAINER_CAPS = [
    "SYS_RAWIO",
    "SYS_ADMIN"
]


class VFIOContainerManager:
    """Manages VFIO devices for containerized builds."""
    
    def __init__(self):
        """Initialize the VFIO container manager."""
        self.state = self._load_state()
        
    def _load_state(self) -> Dict:
        """Load persisted state."""
        if CONTAINER_STATE_FILE.exists():
            try:
                return json.loads(CONTAINER_STATE_FILE.read_text())
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format("Failed to load state: {error}", error=e),
                    prefix="STATE"
                )
        return {"devices": {}, "containers": {}}
    
    def _save_state(self) -> None:
        """Save current state."""
        try:
            CONTAINER_STATE_FILE.write_text(json.dumps(self.state, indent=2))
        except Exception as e:
            log_warning_safe(
                logger,
                safe_format("Failed to save state: {error}", error=e),
                prefix="STATE"
            )
    
    def check_prerequisites(self) -> bool:
        """Check if all VFIO prerequisites are met."""
        try:
            check_vfio_prerequisites()
            
            # Check if running as root
            if os.geteuid() != 0:
                log_error_safe(
                    logger,
                    "VFIO operations require root privileges",
                    prefix="PERM"
                )
                return False
            
            # Check required kernel modules
            modules_path = Path("/proc/modules")
            if modules_path.exists():
                modules_content = modules_path.read_text()
                required_modules = ["vfio", "vfio_iommu_type1", "vfio-pci"]
                missing_modules = []
                
                for module in required_modules:
                    if f"{module} " not in modules_content:
                        missing_modules.append(module)
                
                if missing_modules:
                    log_warning_safe(
                        logger,
                        safe_format(
                            "Missing kernel modules: {modules}",
                            modules=missing_modules
                        ),
                        prefix="VFIO"
                    )
                    log_info_safe(
                        logger,
                        "Loading missing modules...",
                        prefix="VFIO"
                    )
                    
                    for module in missing_modules:
                        result = os.system(f"modprobe {module}")
                        if result != 0:
                            log_error_safe(
                                logger,
                                safe_format(
                                    "Failed to load module {module}", module=module
                                ),
                                prefix="VFIO"
                            )
                            return False
            
            log_info_safe(logger, "VFIO prerequisites check passed", prefix="VFIO")
            return True
            
        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Prerequisites check failed: {error}", error=e),
                prefix="VFIO"
            )
            return False
    
    def bind_device(self, bdf: str, attach: bool = True) -> Optional[str]:
        """Bind a PCI device to vfio-pci driver."""
        try:
            log_info_safe(
                logger,
                safe_format("Binding device {bdf} to vfio-pci", bdf=bdf),
                prefix="BIND"
            )
            
            # Create VFIO binder
            binder = VFIOBinder(bdf, attach=attach)
            
            # Get device info before binding
            device_info = binder.get_device_info()
            
            # Bind the device
            binder.bind()
            
            # Save state for restoration
            self.state["devices"][bdf] = {
                "original_driver": binder.original_driver,
                "iommu_group": device_info.iommu_group,
                "vendor_id": device_info.vendor_id,
                "device_id": device_info.device_id,
                "bound_at": time.time(),
                "attached": attach
            }
            self._save_state()
            
            log_info_safe(
                logger,
                safe_format(
                    "Successfully bound {bdf} (IOMMU group {group})",
                    bdf=bdf,
                    group=device_info.iommu_group
                ),
                prefix="BIND"
            )
            
            return device_info.iommu_group
            
        except (VFIOBindError, VFIOPermissionError) as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to bind device {bdf}: {error}", bdf=bdf, error=e
                ),
                prefix="BIND"
            )
            return None
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Unexpected error binding {bdf}: {error}", bdf=bdf, error=e
                ),
                prefix="BIND"
            )
            return None
    
    def unbind_device(self, bdf: str) -> bool:
        """Unbind a device from vfio-pci and restore original driver."""
        try:
            log_info_safe(
                logger,
                safe_format("Unbinding device {bdf} from vfio-pci", bdf=bdf),
                prefix="UNBIND"
            )
            
            # Create VFIO binder
            binder = VFIOBinder(bdf, attach=False)
            
            # Unbind the device
            binder.unbind()
            
            # Remove from state
            if bdf in self.state["devices"]:
                del self.state["devices"][bdf]
                self._save_state()
            
            log_info_safe(
                logger,
                safe_format("Successfully unbound {bdf}", bdf=bdf),
                prefix="UNBIND"
            )
            
            return True
            
        except Exception as e:
            log_error_safe(
                logger,
                safe_format(
                    "Failed to unbind device {bdf}: {error}", bdf=bdf, error=e
                ),
                prefix="UNBIND"
            )
            return False
    
    def get_container_command(self, bdf: str, board: str, **kwargs) -> List[str]:
        """Generate Podman command for container with VFIO device."""
        device_info = self.state["devices"].get(bdf)
        if not device_info:
            raise ValueError(f"Device {bdf} not bound. Run bind operation first.")
        
        iommu_group = device_info["iommu_group"]
        if not iommu_group:
            raise ValueError(f"No IOMMU group found for device {bdf}")
        
        # Container configuration
        image = kwargs.get("image", DEFAULT_CONTAINER_IMAGE)
        tag = kwargs.get("tag", DEFAULT_CONTAINER_TAG)
        container_name = kwargs.get("name", f"vfio-build-{iommu_group}")
        
        # Build command
        cmd = [
            "podman", "run", "--rm", "-it",
            "--name", container_name,
            "--privileged",  # Required for VFIO operations
            f"--device", "/dev/vfio/vfio",
            f"--device", f"/dev/vfio/{iommu_group}",
        ]
        
        # Add required capabilities
        for cap in REQUIRED_CONTAINER_CAPS:
            cmd.extend(["--cap-add", cap])
        
        # Security options for SELinux
        cmd.extend(["--security-opt", "label=disable"])
        
        # Mount output directory
        output_dir = kwargs.get("output_dir", "./output")
        cmd.extend(["-v", f"{output_dir}:/app/output"])
        
        # Environment variables
        cmd.extend(["-e", f"DEVICE_BDF={bdf}"])
        cmd.extend(["-e", f"IOMMU_GROUP={iommu_group}"])
        
        # Add kernel headers if available
        uname_release = os.uname().release if hasattr(os, "uname") else ""
        if uname_release:
            kernel_headers = f"/lib/modules/{uname_release}/build"
            if Path(kernel_headers).exists():
                cmd.extend(["-v", f"{kernel_headers}:/kernel-headers:ro"])
        
        # Add debugfs if available
        debugfs_path = "/sys/kernel/debug"
        if Path(debugfs_path).exists():
            cmd.extend(["-v", f"{debugfs_path}:{debugfs_path}:rw"])
        
        # Image and command
        cmd.append(f"{image}:{tag}")
        
        # Build arguments
        build_args = kwargs.get("build_args", [])
        if build_args:
            cmd.extend(build_args)
        else:
            # Default build command
            cmd.extend(["sudo", "python3", "/app/pcileech.py", "build"])
            cmd.extend(["--bdf", bdf])
            cmd.extend(["--board", board])
        
        return cmd
    
    def run_container(self, bdf: str, board: str, **kwargs) -> bool:
        """Run container with VFIO device."""
        try:
            # Generate container command
            cmd = self.get_container_command(bdf, board, **kwargs)
            
            log_info_safe(
                logger,
                safe_format(
                    "Running container with command: {cmd}", cmd=" ".join(cmd)
                ),
                prefix="CONTAINER"
            )
            
            # Execute container
            result = os.system(" ".join(cmd))
            
            if result == 0:
                log_info_safe(
                    logger,
                    "Container completed successfully",
                    prefix="CONTAINER"
                )
                return True
            else:
                log_error_safe(
                    logger,
                    safe_format(
                        "Container failed with exit code {code}", code=result
                    ),
                    prefix="CONTAINER"
                )
                return False
                
        except Exception as e:
            log_error_safe(
                logger,
                safe_format("Failed to run container: {error}", error=e),
                prefix="CONTAINER"
            )
            return False
    
    def cleanup_all(self) -> None:
        """Cleanup all bound devices."""
        log_info_safe(logger, "Cleaning up all bound devices", prefix="CLEANUP")
        
        devices_to_cleanup = list(self.state["devices"].keys())
        
        for bdf in devices_to_cleanup:
            try:
                self.unbind_device(bdf)
            except Exception as e:
                log_warning_safe(
                    logger,
                    safe_format(
                        "Failed to cleanup {bdf}: {error}", bdf=bdf, error=e
                    ),
                    prefix="CLEANUP"
                )
        
        log_info_safe(logger, "Cleanup completed", prefix="CLEANUP")
    
    def show_status(self) -> None:
        """Show current VFIO status."""
        print("\n=== VFIO Container Manager Status ===")
        
        if not self.state["devices"]:
            print("No devices currently bound.")
            return
        
        print(f"Bound devices: {len(self.state['devices'])}")
        for bdf, info in self.state["devices"].items():
            print(f"\nDevice: {bdf}")
            print(f"  IOMMU Group: {info['iommu_group']}")
            print(f"  Vendor:Device: {info['vendor_id']}:{info['device_id']}")
            print(f"  Original Driver: {info['original_driver']}")
            print(f"  Bound At: {time.ctime(info['bound_at'])}")
            print(f"  Attached: {info['attached']}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "VFIO Container Manager - Manage PCI devices for containerized builds"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Bind device to vfio-pci
  sudo python3 vfio_container_manager.py bind --bdf 0000:03:00.0
  
  # Run container with bound device
  sudo python3 vfio_container_manager.py run --bdf 0000:03:00.0 \\
    --board pcileech_35t325_x1
  
  # Unbind device and restore original driver
  sudo python3 vfio_container_manager.py unbind --bdf 0000:03:00.0
  
  # Show current status
  sudo python3 vfio_container_manager.py status
  
  # Cleanup all devices
  sudo python3 vfio_container_manager.py cleanup
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Bind command
    bind_parser = subparsers.add_parser("bind", help="Bind device to vfio-pci")
    bind_parser.add_argument(
        "--bdf", required=True, help="PCI device BDF (e.g., 0000:03:00.0)"
    )
    bind_parser.add_argument(
        "--no-attach", action="store_true", help="Don't attach IOMMU group"
    )
    
    # Unbind command
    unbind_parser = subparsers.add_parser(
        "unbind", help="Unbind device from vfio-pci"
    )
    unbind_parser.add_argument("--bdf", required=True, help="PCI device BDF")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run container with VFIO device")
    run_parser.add_argument("--bdf", required=True, help="PCI device BDF")
    run_parser.add_argument("--board", required=True, help="Board configuration")
    run_parser.add_argument(
        "--image", default=DEFAULT_CONTAINER_IMAGE, help="Container image"
    )
    run_parser.add_argument(
        "--tag", default=DEFAULT_CONTAINER_TAG, help="Container tag"
    )
    run_parser.add_argument(
        "--output-dir", default="./output", help="Output directory"
    )
    run_parser.add_argument("--name", help="Container name")
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Show current status")
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Cleanup all bound devices"
    )
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Initialize manager
    manager = VFIOContainerManager()
    
    try:
        if args.command == "bind":
            if not manager.check_prerequisites():
                return 1
            
            group = manager.bind_device(args.bdf, attach=not args.no_attach)
            if group:
                print(f"Device {args.bdf} bound to IOMMU group {group}")
                return 0
            else:
                print(f"Failed to bind device {args.bdf}")
                return 1
        
        elif args.command == "unbind":
            if manager.unbind_device(args.bdf):
                print(f"Device {args.bdf} unbound successfully")
                return 0
            else:
                print(f"Failed to unbind device {args.bdf}")
                return 1
        
        elif args.command == "run":
            success = manager.run_container(
                args.bdf,
                args.board,
                image=args.image,
                tag=args.tag,
                output_dir=args.output_dir,
                name=args.name
            )
            return 0 if success else 1
        
        elif args.command == "status":
            manager.show_status()
            return 0
        
        elif args.command == "cleanup":
            manager.cleanup_all()
            return 0
        
        else:
            parser.print_help()
            return 1
            
    except KeyboardInterrupt:
        print("\nOperation interrupted by user")
        return 130
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
