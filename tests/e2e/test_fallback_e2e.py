import subprocess
import sys
from pathlib import Path


def test_cli_fallback_returns_success():
    # Run a short inline Python process that imports the module and checks
    # fallback behaviour via get_device_config returning None
    cmd = [
        sys.executable,
        '-c',
        (
            'import src.device_clone.device_config as dc, json;'
            "m=dc.get_device_config('this_profile_should_not_exist');"
            "print(json.dumps({'result': m is None}))"
        ),
    ]

    # Ensure the subprocess runs from repo root so `import src...` succeeds
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root))
    assert proc.returncode == 0

    # Some modules log warnings to stdout which can prefix the JSON output.
    # Find the first JSON object in stdout and validate its contents.
    out = proc.stdout.strip()
    import json

    idx = out.find('{')
    assert idx != -1, f'no JSON object found in output: {out!r}'
    payload = json.loads(out[idx:])
    assert payload == {'result': True}
