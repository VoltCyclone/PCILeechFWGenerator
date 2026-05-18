"""T10: ThreadPoolExecutor must be build-scoped.

Previously a single ThreadPoolExecutor was instantiated in __init__ and
shutdown() ran in the start_build finally. A late _update_resource_usage
tick after shutdown raised RuntimeError; subsequent builds couldn't reuse
the orchestrator. Verify the new lifecycle:
  - executor is None at rest
  - _update_resource_usage is a no-op when executor is None
  - two sequential builds on one orchestrator don't crash
"""


import pytest

from pcileechfwgenerator.tui.core.build_orchestrator import BuildOrchestrator
from pcileechfwgenerator.tui.models.config import BuildConfiguration
from pcileechfwgenerator.tui.models.device import PCIDevice
from pcileechfwgenerator.tui.models.progress import (
    BuildProgress,
    BuildStage,
)


def test_executor_starts_unset():
    orch = BuildOrchestrator()
    assert orch._executor is None


@pytest.mark.asyncio
async def test_update_resource_usage_noop_without_executor():
    orch = BuildOrchestrator()
    orch._current_progress = BuildProgress(
        stage=BuildStage.ENVIRONMENT_VALIDATION,
        completion_percent=0.0,
        current_operation="x",
    )
    # No executor — must not raise.
    await orch._update_resource_usage()


@pytest.mark.asyncio
async def test_two_sequential_builds_share_orchestrator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    orch = BuildOrchestrator()

    # Replace the heavy work with a no-op stage list.
    orch._create_build_stages = lambda dev, cfg: []  # type: ignore[assignment]

    device = PCIDevice(
        bdf="0000:01:00.0",
        vendor_id="abcd",
        device_id="1234",
        vendor_name="x",
        device_name="y",
        device_class="0x000000",
    )
    config = BuildConfiguration(local_build=True)

    assert await orch.start_build(device, config) is True
    assert orch._executor is None  # released

    assert await orch.start_build(device, config) is True
    assert orch._executor is None
