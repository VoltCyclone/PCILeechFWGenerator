#!/usr/bin/env python3
"""
Driver Register Scraper with Enhanced Analysis

Usage:
    python3 driver_scrape.py <vendor_id_hex> <device_id_hex> [--verbose] [--src PATH]

Example:
    python3 driver_scrape.py 8086 1533 --verbose

Output JSON schema:
{
  "driver_module": "driver_name",
  "registers": [
    {
      "offset": "0x400",
      "name": "reg_ctrl",
      "value": "0x0",
      "rw": "rw",
      "bit_width": 32,
      "context": {
        "function": "init_device",
        "dependencies": ["reg_status"],
        "timing": "early",
        "access_pattern": "write_then_read"
      }
    }
  ]
}

Prerequisites:
    - modprobe (Linux module utilities)
    - ripgrep (rg) for fast text searching (optional but recommended)
    - Linux kernel source packages in /usr/src/
"""

import argparse
import json
import logging
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Set

try:
    from scripts.kernel_utils import (
        check_linux_requirement,
        ensure_kernel_source,
        find_driver_sources,
        resolve_driver_module,
    )
    from scripts.state_machine_extractor import StateMachineExtractor
except ImportError:
    # Fallback for when running as script directly
    from kernel_utils import (
        check_linux_requirement,
        ensure_kernel_source,
        find_driver_sources,
        resolve_driver_module,
    )
    from state_machine_extractor import StateMachineExtractor

# Module-level regex patterns for register analysis
REG_PATTERN = re.compile(r"#define\s+(REG_[A-Z0-9_]+)\s+0x([0-9A-Fa-f]+)")
WRITE_PATTERN = re.compile(r"write([blwq]?)\s*\(.*?\b(REG_[A-Z0-9_]+)\b")
READ_PATTERN = re.compile(r"read([blwq]?)\s*\(.*?\b(REG_[A-Z0-9_]+)\b")

# Bit width mapping for register access functions
BIT_WIDTH_MAP = {
    "b": 8,  # readb/writeb
    "w": 16,  # readw/writew
    "l": 32,  # readl/writel
    "q": 64,  # readq/writeq
    "": 32,  # default to 32-bit
}

logger = logging.getLogger(__name__)


def validate_hex_id(hex_id: str, id_type: str) -> str:
    """
    Validate that hex ID is a 4-digit hex string.

    Args:
        hex_id: The hex ID to validate
        id_type: Type description for error messages ("Vendor ID" or "Device ID")

    Returns:
        Lowercase validated hex ID

    Raises:
        ValueError: If hex ID is invalid format
    """
    if not re.fullmatch(r"[0-9A-Fa-f]{4}", hex_id):
        raise ValueError(
            f"{id_type} must be a 4-digit hexadecimal string. " f"Got: '{hex_id}'"
        )
    return hex_id.lower()


class DriverAnalyzer:
    """
    Encapsulates driver analysis functionality with shared state.

    This class maintains pre-compiled regex patterns and file content
    to avoid duplication and improve performance.
    """

    def __init__(self, file_contents: Dict[pathlib.Path, str]):
        """
        Initialize analyzer with file contents.

        Args:
            file_contents: Dictionary mapping file paths to their content
        """
        self.file_contents = file_contents
        self.all_content = "\n".join(file_contents.values())

        # Pre-compile regex patterns for better performance
        self._func_pattern_cache: Dict[str, re.Pattern] = {}
        self._access_pattern = re.compile(
            r"(write|read)([blwq]?)\s*\([^)]*\b(REG_[A-Z0-9_]+)\b"
        )
        self._delay_pattern = re.compile(
            r"(udelay|mdelay|msleep|usleep_range)\s*\(\s*(\d+)", re.IGNORECASE
        )

    def _get_function_pattern(self, reg_name: str) -> re.Pattern:
        """Get cached function pattern for register name."""
        if reg_name not in self._func_pattern_cache:
            # Improved pattern to handle nested braces with balance counter
            self._func_pattern_cache[reg_name] = re.compile(
                r"(\w+)\s*\([^)]*\)\s*\{.*?" + re.escape(reg_name) + r".*?\}", re.DOTALL
            )
        return self._func_pattern_cache[reg_name]

    def analyze_function_context(self, reg_name: str) -> Dict[str, Any]:
        """
        Analyze the function context where a register is used.

        Enhanced to recognize macros split across lines and provide
        fallback timing detection.
        """
        context = {
            "function": None,
            "dependencies": [],
            "timing": "unknown",
            "access_pattern": "unknown",
        }

        # Find function containing the register usage with improved pattern
        func_pattern = self._get_function_pattern(reg_name)

        # Search through content with brace balancing for nested blocks
        content = self.all_content
        for match in re.finditer(r"(\w+)\s*\([^)]*\)\s*\{", content):
            func_name = match.group(1)
            start_pos = match.end() - 1  # Position of opening brace

            # Balance braces to find function end
            brace_count = 1
            pos = start_pos + 1
            while pos < len(content) and brace_count > 0:
                if content[pos] == "{":
                    brace_count += 1
                elif content[pos] == "}":
                    brace_count -= 1
                pos += 1

            if brace_count == 0:  # Found complete function
                func_body = content[start_pos:pos]

                # Check if register is used in this function (handle line continuations)
                reg_pattern = re.compile(
                    r"\b" + re.escape(reg_name) + r"\b|"
                    r"\b(REG_\w*|IWL_\w*)\s*\\\s*\n.*?" + re.escape(reg_name),
                    re.MULTILINE | re.DOTALL,
                )

                if reg_pattern.search(func_body):
                    context["function"] = func_name

                    # Analyze dependencies - other registers used in same function
                    dep_pattern = re.compile(r"\b(REG_[A-Z0-9_]+)\b")
                    deps = set(dep_pattern.findall(func_body))
                    deps.discard(reg_name)  # Remove self
                    context["dependencies"] = list(deps)[:5]  # Limit to 5 most relevant

                    # Enhanced timing determination with fallback
                    timing = self._determine_timing(func_name, func_body)
                    context["timing"] = timing

                    # Analyze access patterns
                    context["access_pattern"] = self._analyze_access_pattern(
                        func_body, reg_name
                    )
                    break

        return context

    def _determine_timing(self, func_name: str, func_body: str) -> str:
        """
        Determine timing context with fallback detection.

        Args:
            func_name: Name of the function
            func_body: Content of the function

        Returns:
            Timing classification string
        """
        func_lower = func_name.lower()

        # Primary classification based on function name
        if any(keyword in func_lower for keyword in ["init", "probe", "start"]):
            return "early"
        elif any(keyword in func_lower for keyword in ["exit", "remove", "stop"]):
            return "late"
        elif any(keyword in func_lower for keyword in ["irq", "interrupt", "handler"]):
            return "interrupt"

        # Fallback: detect "probe" vs "resume" patterns in function body
        if re.search(r"\bprobe\b", func_body, re.IGNORECASE):
            return "early"
        elif re.search(r"\bresume\b", func_body, re.IGNORECASE):
            return "runtime"
        elif re.search(r"\bsuspend\b", func_body, re.IGNORECASE):
            return "late"

        return "runtime"

    def _analyze_access_pattern(self, func_body: str, reg_name: str) -> str:
        """Analyze register access patterns within a function."""
        write_pattern = re.compile(
            r"write[blwq]?\s*\([^)]*" + re.escape(reg_name), re.IGNORECASE
        )
        read_pattern = re.compile(
            r"read[blwq]?\s*\([^)]*" + re.escape(reg_name), re.IGNORECASE
        )

        writes = list(write_pattern.finditer(func_body))
        reads = list(read_pattern.finditer(func_body))

        write_count = len(writes)
        read_count = len(reads)

        # Check for specific patterns based on read/write counts
        if write_count > 0 and read_count > 0:
            # Check for write-then-read pattern (exactly one write followed by one read)
            if write_count == 1 and read_count == 1:
                write_pos = writes[0].start()
                read_pos = reads[0].start()
                if write_pos < read_pos:
                    return "write_then_read"
                else:
                    return "balanced"
            # Check if significantly more writes than reads
            elif write_count > read_count * 1.5:
                return "write_heavy"
            # Check if significantly more reads than writes
            elif read_count > write_count * 1.5:
                return "read_heavy"
            # Otherwise it's balanced
            else:
                return "balanced"
        elif write_count > 0:
            return "write_heavy"
        elif read_count > 0:
            return "read_heavy"
        else:
            return "unknown"

    def analyze_access_sequences(
        self, reg_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Analyze register access sequences with improved function parsing.

        Enhanced to handle nested braces properly using balance counter.
        """
        sequences = []
        content = self.all_content

        # Find functions with improved brace balancing
        for match in re.finditer(r"(\w+)\s*\([^)]*\)\s*\{", content):
            func_name = match.group(1)
            start_pos = match.end() - 1  # Position of opening brace

            # Balance braces to find function end
            brace_count = 1
            pos = start_pos + 1
            while pos < len(content) and brace_count > 0:
                if content[pos] == "{":
                    brace_count += 1
                elif content[pos] == "}":
                    brace_count -= 1
                pos += 1

            if brace_count == 0:  # Found complete function
                func_body = content[start_pos:pos]

                # Find all register accesses in order
                accesses = []
                for access_match in self._access_pattern.finditer(func_body):
                    operation = access_match.group(1)
                    bit_suffix = access_match.group(2)
                    register = access_match.group(3)

                    # If specific register requested, filter for it
                    if reg_name and register != reg_name:
                        continue

                    accesses.append(
                        (operation, register, access_match.start(), bit_suffix)
                    )

                # Only process functions with register accesses
                if len(accesses) > 0:
                    for i, (op, reg, pos, bit_suffix) in enumerate(accesses):
                        sequence = {
                            "function": func_name,
                            "position": i,
                            "total_ops": len(accesses),
                            "operation": op,
                            "register": reg,
                            "bit_width": BIT_WIDTH_MAP.get(bit_suffix, 32),
                        }

                        # Add preceding and following operations for context
                        if i > 0:
                            sequence["preceded_by"] = accesses[i - 1][1]
                            sequence["preceded_by_op"] = accesses[i - 1][0]
                        if i < len(accesses) - 1:
                            sequence["followed_by"] = accesses[i + 1][1]
                            sequence["followed_by_op"] = accesses[i + 1][0]

                        sequences.append(sequence)

        return sequences

    def analyze_timing_constraints(
        self, reg_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Analyze timing constraints and delays related to register accesses."""
        constraints = []

        for delay_match in self._delay_pattern.finditer(self.all_content):
            delay_type = delay_match.group(1).lower()
            delay_value = int(delay_match.group(2))

            # Convert to microseconds for consistency
            if delay_type in ["mdelay", "msleep"]:
                delay_us = delay_value * 1000
            elif delay_type == "udelay":
                delay_us = delay_value
            else:  # usleep_range
                delay_us = delay_value

            # Find nearby register accesses
            context_start = max(0, delay_match.start() - 200)
            context_end = min(len(self.all_content), delay_match.end() + 200)
            context = self.all_content[context_start:context_end]

            reg_pattern = re.compile(r"\b(REG_[A-Z0-9_]+)\b")
            nearby_regs = reg_pattern.findall(context)

            # Filter for specific register if provided
            if reg_name and reg_name not in nearby_regs:
                continue

            if nearby_regs:
                constraint = {
                    "delay_us": delay_us,
                    "registers": list(set(nearby_regs)),
                    "context": "register_access",
                }

                # Determine if this is a post-write or pre-read delay
                pre_context = self.all_content[context_start : delay_match.start()]
                post_context = self.all_content[delay_match.end() : context_end]

                if re.search(r"write[blwq]?\s*\([^)]*", pre_context):
                    constraint["type"] = "post_write_delay"
                elif re.search(r"read[blwq]?\s*\([^)]*", post_context):
                    constraint["type"] = "pre_read_delay"
                else:
                    constraint["type"] = "general_delay"

                constraints.append(constraint)

        return constraints


def extract_registers_with_analysis(
    source_files: List[pathlib.Path], driver_name: str
) -> Dict[str, Any]:
    """
    Extract and analyze registers from source files with enhanced performance.

    Args:
        source_files: List of source file paths to analyze
        driver_name: Name of the driver module for metadata

    Returns:
        Dictionary containing registers and analysis metadata
    """
    # Read each source file once and store content
    file_contents: Dict[pathlib.Path, str] = {}
    for path in source_files:
        try:
            file_contents[path] = path.read_text(errors="ignore")
        except Exception as e:
            logger.warning(f"Error reading {path}: {e}")
            continue

    if not file_contents:
        logger.warning("No source files could be read")
        return {
            "driver_module": driver_name,
            "registers": [],
        }

    # Create analyzer with file contents
    analyzer = DriverAnalyzer(file_contents)

    # Extract register definitions efficiently
    regs: Dict[str, int] = {}
    writes: Set[str] = set()
    reads: Set[str] = set()
    bit_widths: Dict[str, int] = {}

    for m in REG_PATTERN.finditer(analyzer.all_content):
        regs[m.group(1)] = int(m.group(2), 16)

    for w in WRITE_PATTERN.finditer(analyzer.all_content):
        bit_suffix = w.group(1)
        reg_name = w.group(2)
        writes.add(reg_name)
        bit_widths[reg_name] = BIT_WIDTH_MAP.get(bit_suffix, 32)
        if len(writes) > 64:
            break

    for r in READ_PATTERN.finditer(analyzer.all_content):
        bit_suffix = r.group(1)
        reg_name = r.group(2)
        reads.add(reg_name)
        if reg_name not in bit_widths:
            bit_widths[reg_name] = BIT_WIDTH_MAP.get(bit_suffix, 32)
        if len(reads) > 64:
            break

    # Extract state machines using the state machine extractor
    try:
        state_machine_extractor = StateMachineExtractor(debug=False)
        extracted_state_machines = state_machine_extractor.extract_state_machines(
            analyzer.all_content, regs
        )
        optimized_state_machines = state_machine_extractor.optimize_state_machines()
    except Exception as e:
        logger.warning(f"State machine extraction failed: {e}")
        extracted_state_machines = []
        optimized_state_machines = []

    # Build register items with enhanced context
    items = []
    for sym, off in regs.items():
        # Determine read/write capability
        rw_capability = "ro"  # default
        if sym in writes and sym in reads:
            rw_capability = "rw"
        elif sym in writes:
            rw_capability = "wo"
        elif sym in reads:
            rw_capability = "ro"

        # Analyze context for this register
        context = analyzer.analyze_function_context(sym)

        # Add timing constraints
        timing_constraints = analyzer.analyze_timing_constraints(sym)
        if timing_constraints:
            context["timing_constraints"] = timing_constraints[:3]

        # Add access sequences
        sequences = analyzer.analyze_access_sequences(sym)
        if sequences:
            context["sequences"] = sequences[:5]

        # Add state machine information
        context["state_machines"] = []
        for sm in optimized_state_machines:
            if (
                sym.lower() in [reg.lower() for reg in sm.registers]
                or f"reg_0x{off:08x}" in sm.registers
            ):
                sm_info = {
                    "name": sm.name,
                    "states_count": len(sm.states),
                    "transitions_count": len(sm.transitions),
                    "complexity_score": sm.complexity_score,
                    "type": sm.context["type"],
                    "initial_state": sm.initial_state,
                    "final_states": list(sm.final_states),
                }
                context["state_machines"].append(sm_info)

        items.append(
            {
                "offset": f"0x{off:x}",  # Emit as hex string
                "name": sym.lower(),
                "value": "0x0",
                "rw": rw_capability,
                "bit_width": bit_widths.get(sym, 32),  # Add bit width
                "context": context,
            }
        )

    return {
        "driver_module": driver_name,  # Add driver module for traceability
        "registers": items,
        "state_machine_analysis": {
            "extracted_state_machines": len(extracted_state_machines),
            "optimized_state_machines": len(optimized_state_machines),
            "state_machines": [sm.to_dict() for sm in optimized_state_machines],
        },
    }


def setup_logging(verbose: bool) -> None:
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Extract and analyze driver registers from kernel source",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 8086 1533                    # Analyze Intel device
  %(prog)s 8086 1533 --verbose          # With debug output
  %(prog)s 8086 1533 --src /custom/src  # Custom source directory
        """,
    )

    parser.add_argument(
        "vendor_id",
        help="4-digit hexadecimal vendor ID (e.g., 8086 for Intel)",
    )

    parser.add_argument(
        "device_id",
        help="4-digit hexadecimal device ID (e.g., 1533)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug output",
    )

    parser.add_argument(
        "--src",
        type=pathlib.Path,
        help="Override kernel source directory (for testing)",
    )

    return parser.parse_args()


def main() -> None:
    """Main function to scrape driver registers."""
    args = parse_arguments()
    setup_logging(args.verbose)

    try:
        # Validate input IDs
        vendor_id = validate_hex_id(args.vendor_id, "Vendor ID")
        device_id = validate_hex_id(args.device_id, "Device ID")

        logger.info(f"Analyzing driver for VID:DID {vendor_id}:{device_id}")

        # Check Linux requirement early
        check_linux_requirement("Driver analysis")

        # Get kernel source directory
        if args.src:
            ksrc = args.src
            if not ksrc.exists():
                raise RuntimeError(f"Custom source directory does not exist: {ksrc}")
        else:
            ksrc = ensure_kernel_source()
            if ksrc is None:
                logger.error("Linux source package not found")
                empty_output = {
                    "driver_module": "unknown",
                    "registers": [],
                    "state_machine_analysis": {
                        "extracted_state_machines": 0,
                        "optimized_state_machines": 0,
                        "state_machines": [],
                        "analysis_report": "Linux source package not found. Unable to perform analysis.",
                    },
                }
                print(json.dumps(empty_output, indent=2))
                return

        # Resolve driver module
        try:
            driver_name = resolve_driver_module(vendor_id, device_id)
            logger.info(f"Resolved driver module: {driver_name}")
        except RuntimeError as e:
            logger.error(f"Driver resolution failed: {e}")
            empty_output = {
                "driver_module": "unknown",
                "registers": [],
                "state_machine_analysis": {
                    "extracted_state_machines": 0,
                    "optimized_state_machines": 0,
                    "state_machines": [],
                    "analysis_report": f"Driver resolution failed: {e}",
                },
            }
            print(json.dumps(empty_output, indent=2))
            return

        # Find source files
        src_files = find_driver_sources(ksrc, driver_name)
        if not src_files:
            logger.warning(f"No source files found for driver: {driver_name}")
            empty_output = {
                "driver_module": driver_name,
                "registers": [],
                "state_machine_analysis": {
                    "extracted_state_machines": 0,
                    "optimized_state_machines": 0,
                    "state_machines": [],
                    "analysis_report": "No driver source files found for the specified device.",
                },
            }
            print(json.dumps(empty_output, indent=2))
            return

        logger.info(f"Found {len(src_files)} source files")

        # Extract and analyze registers
        result = extract_registers_with_analysis(src_files, driver_name)

        logger.info(f"Extracted {len(result['registers'])} registers")
        print(json.dumps(result, indent=2))

    except ValueError as e:
        logger.error(f"Invalid input: {e}")
        sys.exit(1)
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        sys.exit(2)
    except KeyboardInterrupt:
        logger.info("Analysis interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
