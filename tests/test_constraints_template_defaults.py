import pytest

from src.string_utils import generate_tcl_header_comment
from src.templating.template_renderer import TemplateRenderer
from src.utils.unified_context import TemplateObject


class TestConstraintsTemplateDefaults:
    """Ensure optional defaults (like constraint_files) are injected globally."""

    def test_constraints_renders_without_constraint_files(self):
        renderer = TemplateRenderer()

        # Minimal, safe context: provide required objects used by the template
        context = {
            "device": TemplateObject(
                {
                    "vendor_id": "1912",
                    "device_id": "0014",
                    "revision_id": "03",
                    "class_code": "0c0330",
                }
            ),
            "board": TemplateObject(
                {"name": "pcileech_100t484_x1", "fpga_part": "xc7a100tfgg484-1"}
            ),
            # Header is required by TCL templates
            "header": generate_tcl_header_comment("TCL Constraints"),
            # PCIe clock parameters (added for 7-series support)
            "pcie_refclk_freq": 0,  # 0=100MHz
            "pcie_userclk1_freq": 2,  # 2=62.5MHz
            "pcie_userclk2_freq": 2,  # 2=62.5MHz
            # Intentionally omit 'constraint_files' to verify default injection
            # Other optional keys like sys_clk_freq_mhz have in-template defaults
        }

        # Should not raise due to StrictUndefined; validator must inject defaults
        output = renderer.render_template("tcl/constraints.j2", context)

        assert isinstance(output, str) and len(output) > 0
        # Sanity checks that key sections rendered
        assert "Adding constraint files" in output
        assert "Generated device constraints file" in output
