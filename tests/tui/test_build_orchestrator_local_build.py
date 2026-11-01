"""T1: `BuildOrchestrator` must honor `BuildConfiguration.local_build`.

Previously, `_validate_environment` walked a non-existent widget tree via
`_get_app()`, which always returned `None`, so every build took the
container path (and failed for non-root local-build users).
"""

from unittest.mock import AsyncMock

import pytest

from pcileechfwgenerator.tui.core.build_orchestrator import BuildOrchestrator
from pcileechfwgenerator.tui.models.config import BuildConfiguration


@pytest.mark.asyncio
async def test_local_build_skips_container_validation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    orch = BuildOrchestrator()
    orch._config = BuildConfiguration(local_build=True)

    container = AsyncMock()
    local = AsyncMock()
    git = AsyncMock()
    orch._validate_container_environment = container
    orch._validate_local_environment = local
    orch._ensure_git_repo = git

    await orch._validate_environment()

    container.assert_not_called()
    local.assert_awaited_once_with(orch._config)


@pytest.mark.asyncio
async def test_container_build_runs_container_validation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    orch = BuildOrchestrator()
    orch._config = BuildConfiguration(local_build=False)

    container = AsyncMock()
    local = AsyncMock()
    git = AsyncMock()
    orch._validate_container_environment = container
    orch._validate_local_environment = local
    orch._ensure_git_repo = git

    await orch._validate_environment()

    container.assert_awaited_once()
    local.assert_not_called()


def test_get_app_is_removed():
    """The widget-walking helper is the root cause; it must not return."""
    assert not hasattr(BuildOrchestrator, "_get_app")


@pytest.mark.asyncio
async def test_progress_does_not_regress_after_vivado_stage():
    from pcileechfwgenerator.tui.core.build_orchestrator import BuildOrchestrator
    from pcileechfwgenerator.tui.models.progress import BuildProgress, BuildStage

    orchestrator = BuildOrchestrator()
    orchestrator._current_progress = BuildProgress(
        BuildStage.ENVIRONMENT_VALIDATION, 0, "Starting"
    )

    async def build():
        await orchestrator._update_progress(BuildStage.VIVADO_SYNTHESIS, 80, "Vivado")

    await orchestrator._run_stage(
        BuildStage.ENVIRONMENT_VALIDATION, build, "Start", "Done"
    )
    progress = orchestrator.get_current_progress()
    assert progress.stage == BuildStage.VIVADO_SYNTHESIS
    assert progress.completion_percent == 100
    assert progress.current_operation == "Done"
