"""Host exports execute the validated constructor output after relocation."""

import subprocess
from pathlib import Path

import pytest
from pcileechfwgenerator.vivado_handling import (
    pcileech_build_integration as integration,
)
from pcileechfwgenerator.vivado_handling.vivado_runner import (
    VivadoIntegrationError,
    VivadoRunner,
)


@pytest.mark.parametrize("rerun_failure", ["preflight", "vivado"])
def test_host_export_uses_integrated_script_and_relocates(
    tmp_path, monkeypatch, rerun_failure
):
    out = tmp_path / "container output"
    script = out / "board" / "build_all.tcl"
    script.parent.mkdir(parents=True)
    script.write_text("# authoritative generated script")
    from pcileechfwgenerator.vivado_handling.build_evidence import write_build_manifest

    write_build_manifest(script.parent, board="board", part="part", inputs=[script])
    calls = []

    def prepare(board, output, **kwargs):
        calls.append((board, output))
        return script

    monkeypatch.setattr(integration, "integrate_pcileech_build", prepare)
    install = tmp_path / "Vivado space"
    (install / "bin").mkdir(parents=True)
    executable = install / "bin" / "vivado"
    executable.write_text(
        "#!/bin/sh\nmkdir -p board/output board/reports\nprintf image > board/output/image.bit\nprintf test-tool > board/reports/tool_version.txt\n"
        'pwd > invocation.txt\nprintf "%s\\n" "$@" >> invocation.txt\n'
    )
    executable.chmod(0o700)
    monkeypatch.setenv("HOST_VIVADO_PATH", str(install))
    runner = VivadoRunner("board", out, str(install))
    with pytest.raises(VivadoIntegrationError, match="exported script"):
        runner._run_vivado_on_host()
    assert calls == [("board", out)]
    relocated = tmp_path / "host output"
    out.rename(relocated)
    subprocess.run([str(relocated / "run_vivado_on_host.sh")], cwd=tmp_path, check=True)
    lines = (relocated / "invocation.txt").read_text().splitlines()
    assert Path(lines[0]).resolve() == relocated.resolve()
    assert lines[1:] == ["-mode", "batch", "-source", "board/build_all.tcl"]
    published = relocated / "board" / "output"
    previous_image = (published / "image.bit").read_bytes()
    previous_result = (published / "build_result.json").read_bytes()

    (relocated / "invocation.txt").unlink()
    if rerun_failure == "preflight":
        (relocated / "board" / "build_all.tcl").write_text("# changed after export")
    else:
        executable.write_text("#!/bin/sh\nexit 3\n")
    failure = subprocess.run(
        [str(relocated / "run_vivado_on_host.sh")], cwd=tmp_path, capture_output=True
    )
    assert failure.returncode != 0
    if rerun_failure == "preflight":
        assert b"digest mismatch" in failure.stderr
    else:
        assert failure.returncode == 3
    assert not (relocated / "invocation.txt").exists()
    assert not published.exists()
    archives = list((relocated / "board").glob("previous-output-*"))
    assert len(archives) == 1
    assert (archives[0] / "image.bit").read_bytes() == previous_image
    assert (archives[0] / "build_result.json").read_bytes() == previous_result
