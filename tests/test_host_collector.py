
import json


def test_host_collector_writes_datastore(tmp_path, monkeypatch):
    # Lazy import inside function; create a fake config space
    cfg_bytes = bytes(range(256))

    # Import the collector
    from pcileechfwgenerator.host_collect.collector import HostCollector

    # Monkeypatch _read_config_space to avoid touching /sys
    monkeypatch.setattr(HostCollector, "_read_config_space", lambda self: cfg_bytes)

    # Run
    hc = HostCollector(bdf="0000:03:00.0", datastore=tmp_path, logger=None)
    rc = hc.run()
    assert rc == 0

    # Validate files
    ctx_path = tmp_path / "device_context.json"
    msix_path = tmp_path / "msix_data.json"
    assert ctx_path.exists()
    assert msix_path.exists()

    ctx = json.loads(ctx_path.read_text())
    msix = json.loads(msix_path.read_text())

    assert "config_space_hex" in ctx
    assert isinstance(ctx["config_space_hex"], str)
    # Should be 512 hex chars for 256 bytes
    assert len(ctx["config_space_hex"]) == 512

    # Verify device IDs are extracted and saved (Bug #511 fix)
    assert "vendor_id" in ctx, "device_context.json should include vendor_id"
    assert "device_id" in ctx, "device_context.json should include device_id"
    assert "class_code" in ctx, "device_context.json should include class_code"
    assert "revision_id" in ctx, "device_context.json should include revision_id"
    
    # Verify the extracted values match the config space
    # cfg_bytes = bytes(range(256)), so bytes 0-1 are 0x0100 (little endian)
    assert ctx["vendor_id"] == 0x0100, "vendor_id should be extracted from config space"
    assert ctx["device_id"] == 0x0302, "device_id should be extracted from config space"

    assert "config_space_hex" in msix
    assert "msix_info" in msix
    assert isinstance(msix["msix_info"], dict)


def _build_config_space_with_msix() -> bytes:
    """Build a 256-byte config space exposing an MSI-X capability.

    Models the donor in issue #612: MSI-X cap at 0xB0, table size 4,
    table BIR 4 / offset 0, PBA BIR 4 / offset 0x800.
    """
    cfg = bytearray(256)
    cfg[0:2] = (0x10EC).to_bytes(2, "little")  # vendor
    cfg[2:4] = (0x8161).to_bytes(2, "little")  # device
    cfg[0x06:0x08] = (0x0010).to_bytes(2, "little")  # status: capabilities bit
    cfg[0x34] = 0xB0  # capabilities pointer
    cfg[0xB0] = 0x11  # MSI-X capability ID
    cfg[0xB1] = 0x00  # next pointer (end of list)
    cfg[0xB2:0xB4] = (0x0003).to_bytes(2, "little")  # message control: size-1=3
    cfg[0xB4:0xB8] = (0x00000004).to_bytes(4, "little")  # table BIR=4, offset=0
    cfg[0xB8:0xBC] = (0x00000804).to_bytes(4, "little")  # PBA BIR=4, offset=0x800
    return bytes(cfg)


def test_host_collector_extracts_msix_info(tmp_path, monkeypatch):
    """Regression for #612: MSI-X must be parsed, not written as zeros.

    The collector previously passed raw bytes to parse_msix_capability(),
    which expects a hex string, so MSI-X was silently reported as absent.
    """
    cfg_bytes = _build_config_space_with_msix()

    from pcileechfwgenerator.host_collect.collector import HostCollector

    monkeypatch.setattr(HostCollector, "_read_config_space", lambda self: cfg_bytes)

    hc = HostCollector(bdf="0000:06:00.0", datastore=tmp_path, logger=None)
    assert hc.run() == 0

    msix = json.loads((tmp_path / "msix_data.json").read_text())
    info = msix["msix_info"]

    assert info["table_size"] == 4
    assert info["table_bir"] == 4
    assert info["table_offset"] == 0
    assert info["pba_bir"] == 4
    assert info["pba_offset"] == 0x800
