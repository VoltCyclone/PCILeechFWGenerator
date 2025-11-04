import types
import sys
from types import SimpleNamespace


def test_run_local_templating_monkeypatched(tmp_path, monkeypatch):
    # Build a fake src.build module
    fake_build = types.ModuleType('src.build')

    class FakeConfigurationManager:
        def create_from_args(self, cfg_args):
            # Ensure output is under datastore/output
            return SimpleNamespace(
                bdf=cfg_args.bdf,
                board=cfg_args.board,
                output_dir=cfg_args.output,  # already a path string
                enable_profiling=False,
                preload_msix=True,
                profile_duration=0,
                disable_vfio=True,
                parallel_writes=False,
                max_workers=1,
                output_template=None,
            )

    class FakeFirmwareBuilder:
        def __init__(self, cfg):
            self.cfg = cfg
        def build(self):
            return ['ok.sv']

    fake_build.ConfigurationManager = FakeConfigurationManager
    fake_build.FirmwareBuilder = FakeFirmwareBuilder

    # Inject fake module so pcileech.run_local_templating imports it
    sys.modules['src.build'] = fake_build

    # Import function under test
    import pcileech

    args = SimpleNamespace(
        bdf='0000:03:00.0',
        board='pcileech_35t325_x1',
        datastore=str(tmp_path),
        generate_donor_template=None,
        donor_template=None,
        vivado_path=None,
        vivado_jobs=2,
        vivado_timeout=60,
        enable_error_injection=False,
    )

    rc = pcileech.run_local_templating(args)
    assert rc == 0

    # Env vars should be set for host-context-only flow
    import os
    assert os.environ.get('DEVICE_CONTEXT_PATH') == str(tmp_path / 'device_context.json')
    assert os.environ.get('MSIX_DATA_PATH') == str(tmp_path / 'msix_data.json')
    assert os.environ.get('PCILEECH_HOST_CONTEXT_ONLY') == '1'
