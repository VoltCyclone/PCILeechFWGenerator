import builtins
import shutil
import subprocess
from pathlib import Path

import pytest


def test_flash_fpga_happy_path(monkeypatch, tmp_path, capsys):
    # Create a fake bin file
    bit = tmp_path / "firmware.bin"
    bit.write_bytes(b"BIN")

    # Pretend usbloader exists
    monkeypatch.setattr(shutil, "which", lambda _: str(tmp_path / "usbloader"))

    # Capture the command passed to subprocess.run
    called = {}

    def fake_run(cmd, shell=False, check=False):
        called["cmd"] = cmd
        called["shell"] = shell
        called["check"] = check

    monkeypatch.setattr(subprocess, "run", fake_run)

    from src.flash_fpga import main

    monkeypatch.setattr(builtins, "print", lambda *a, **k: None)
    monkeypatch.setattr(__import__("sys"), "argv", ["flash_fpga.py", str(bit)])

    main()

    assert called["shell"] is False
    assert called["check"] is True
    # ensure vidpid and file path are present
    joined = (
        " ".join(called["cmd"]) if isinstance(called["cmd"], list) else called["cmd"]
    )
    assert "--vidpid" in joined and "1d50:6130" in joined
    assert str(bit) in joined


def test_flash_fpga_missing_usbloader(monkeypatch, tmp_path):
    from src import flash_fpga

    # usbloader missing triggers sys.exit
    monkeypatch.setattr(shutil, "which", lambda _: None)
    # provide an argv so argparse doesn't exit for missing arg before check
    monkeypatch.setattr(
        __import__("sys"), "argv", ["flash_fpga.py", str(tmp_path / "missing.bin")]
    )

    with pytest.raises(SystemExit):
        flash_fpga.main()


def test_flash_fpga_file_not_found(monkeypatch, tmp_path):
    from src import flash_fpga

    # Pretend usbloader exists
    monkeypatch.setattr(shutil, "which", lambda _: str(tmp_path / "usbloader"))

    # Non-existent bitfile
    monkeypatch.setattr(
        __import__("sys"), "argv", ["flash_fpga.py", str(tmp_path / "missing.bin")]
    )
    with pytest.raises(SystemExit):
        flash_fpga.main()
