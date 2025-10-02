import pytest

from src.templating.template_renderer import (TemplateRenderer,
                                              TemplateRenderError)
from src.utils.unified_context import UnifiedContextBuilder


def test_render_all_templates_with_baseline_context():
    """Attempt to render all templates with a comprehensive baseline context.

    Uses the UnifiedContextBuilder to generate a complete template context that
    provides conservative defaults for many template variables. Any template
    that still raises a TemplateRenderError will be reported as a failure so
    we can identify missing defaults before pushing upstream.
    """
    renderer = TemplateRenderer()

    # Build a rich baseline context that mirrors what the unified builder provides
    builder = UnifiedContextBuilder()
    baseline_obj = builder.create_complete_template_context(
        vendor_id="10ee", device_id="7024"
    )
    # Convert TemplateObject to plain dict for rendering
    try:
        baseline = baseline_obj.to_dict()
    except Exception:
        # Fallback: if it's already a dict-like
        baseline = dict(baseline_obj)

    failures = []

    # Iterate over .j2 templates discoverable by the renderer to avoid binary files
    for template_name in renderer.list_templates(pattern="*.j2"):
        try:
            renderer.render_template(template_name, baseline)
        except TemplateRenderError as e:
            failures.append((template_name, str(e)))
        except Exception as e:
            failures.append((template_name, f"{type(e).__name__}: {e}"))

    if failures:
        # Build a readable failure message for pytest
        msgs = [f"{name}: {err}" for name, err in failures]
        pytest.fail(
            f"Some templates failed to render with baseline context (count={len(failures)}):\n"
            + "\n".join(msgs)
        )
