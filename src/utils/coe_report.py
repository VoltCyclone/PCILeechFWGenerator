#!/usr/bin/env python3
"""
COE File Visualization Report Generator.
Automatically generates device ID injection reports for .coe files.
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from ..string_utils import log_debug_safe, log_info_safe, log_warning_safe


def find_coe_files(output_dir: Path) -> List[tuple]:
    """
    Find pairs of template and generated .coe files.
    
    Args:
        output_dir: Output directory to search
        
    Returns:
        List of (template_path, generated_path) tuples
    """
    coe_pairs = []
    
    # Look for generated .coe files
    for generated_file in output_dir.glob("**/*.coe"):
        # Skip if this looks like a template itself
        if "template" in generated_file.name.lower():
            continue
            
        # Try to find corresponding template
        # Common patterns: pcie_7x_0_config_rom.coe -> pcie_7x_0_config_rom_template.coe
        template_name = generated_file.stem + "_template" + generated_file.suffix
        
        # Search in common locations
        possible_template_paths = [
            generated_file.parent / template_name,
            generated_file.parent.parent / template_name,
            Path("pcileech_datastore") / "35T" / template_name,
            Path("pcileech_datastore") / "100T" / template_name,
        ]
        
        for template_path in possible_template_paths:
            if template_path.exists():
                coe_pairs.append((template_path, generated_file))
                break
    
    return coe_pairs


def generate_coe_report(
    output_dir: Path,
    logger: Optional[logging.Logger] = None
) -> bool:
    """
    Generate COE file visualization report.
    
    Args:
        output_dir: Output directory containing .coe files
        logger: Optional logger instance
        
    Returns:
        True if report was generated successfully, False otherwise
    """
    if logger is None:
        logger = logging.getLogger(__name__)
    
    # Find visualize_coe.py script
    script_paths = [
        Path(__file__).parent.parent.parent / "scripts" / "visualize_coe.py",
        Path("scripts") / "visualize_coe.py",
        Path("visualize_coe.py"),
    ]
    
    visualize_script = None
    for script_path in script_paths:
        if script_path.exists():
            visualize_script = script_path
            break
    
    if not visualize_script:
        log_debug_safe(
            logger,
            "COE visualization script not found, skipping report",
            prefix="REPORT"
        )
        return False
    
    # Find COE file pairs
    coe_pairs = find_coe_files(output_dir)
    
    if not coe_pairs:
        log_debug_safe(
            logger,
            "No .coe files found in output directory",
            prefix="REPORT"
        )
        return False
    
    log_info_safe(
        logger,
        "\n" + "═" * 70,
        prefix="REPORT"
    )
    log_info_safe(
        logger,
        "  PCIe Configuration Space Report",
        prefix="REPORT"
    )
    log_info_safe(
        logger,
        "═" * 70,
        prefix="REPORT"
    )
    
    # Generate report for each pair
    for template_path, generated_path in coe_pairs:
        try:
            log_info_safe(
                logger,
                "\nAnalyzing: {file}",
                file=generated_path.name,
                prefix="REPORT"
            )
            
            # Run visualization script
            result = subprocess.run(
                [sys.executable, str(visualize_script), str(template_path), str(generated_path)],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                # Print the visualization output
                print(result.stdout, flush=True)
            else:
                log_warning_safe(
                    logger,
                    "Visualization failed for {file}: {err}",
                    file=generated_path.name,
                    err=result.stderr.strip() if result.stderr else "Unknown error",
                    prefix="REPORT"
                )
        except subprocess.TimeoutExpired:
            log_warning_safe(
                logger,
                "Visualization timed out for {file}",
                file=generated_path.name,
                prefix="REPORT"
            )
        except Exception as e:
            log_warning_safe(
                logger,
                "Failed to visualize {file}: {err}",
                file=generated_path.name,
                err=str(e),
                prefix="REPORT"
            )
    
    log_info_safe(
        logger,
        "═" * 70 + "\n",
        prefix="REPORT"
    )
    
    return True


def generate_coe_report_if_enabled(
    output_dir: Path,
    logger: Optional[logging.Logger] = None
) -> None:
    """
    Generate COE report if not disabled via environment variable.
    
    Args:
        output_dir: Output directory containing .coe files
        logger: Optional logger instance
    """
    import os
    
    # Allow disabling via environment variable
    if os.environ.get("PCILEECH_DISABLE_COE_REPORT", "").lower() in ("1", "true", "yes"):
        return
    
    try:
        generate_coe_report(output_dir, logger)
    except Exception as e:
        # Don't fail the build if report generation fails
        if logger:
            log_debug_safe(
                logger,
                "COE report generation failed (non-fatal): {err}",
                err=str(e),
                prefix="REPORT"
            )
