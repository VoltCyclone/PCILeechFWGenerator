import logging
import sys
from pathlib import Path

import pytest

from src import build_helpers as bh


def test_create_fpga_strategy_selector_basic():
    select = bh.create_fpga_strategy_selector()
    artix = select("xc7a35t-foo")
    kintex = select("xc7k325t-foo")
    us = select("xczu3eg")
    assert artix["pcie_ip_type"] == "axi_pcie"
    assert kintex["pcie_ip_type"] == "pcie_7x"
    assert us["pcie_ip_type"] == "pcie_ultrascale"
    # unknown falls back to generic (pcie_7x)
    generic = select("abcd")
    assert generic["pcie_ip_type"] == "pcie_7x"


def test_write_tcl_file_and_batch(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    out = tmp_path / "tcl"
    files: list[str] = []
    bh.write_tcl_file("puts 'hi'", out / "a.tcl", files, "a.tcl")
    assert (out / "a.tcl").exists()
    assert "a.tcl" in files

    to_write = {
        "b.tcl": "puts 'b'",
        "c.tcl": "puts 'c'",
    }
    bh.batch_write_tcl_files(to_write, out, files, logging.getLogger(__name__))
    assert (out / "b.tcl").exists()
    assert (out / "c.tcl").exists()
    # total files tracked should be 3
    assert set(files) >= {"a.tcl", str(out / "b.tcl"), str(out / "c.tcl")}


@pytest.mark.parametrize(
    "part,ok", [("xc7a35t", True), ("xczu9eg", True), ("nope", False)]
)
def test_validate_fpga_part(part, ok, caplog):
    caplog.set_level(logging.INFO)
    assert bh.validate_fpga_part(part) is ok
