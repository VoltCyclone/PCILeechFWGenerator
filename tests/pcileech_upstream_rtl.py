"""Load upstream PCILeech stream modules for Icarus, flattened to plain ports.

Icarus cannot take SystemVerilog interface ports, so the module text read from
the voltcyclone-fpga submodule is rewritten mechanically: each interface port
becomes one plain port per interface signal, with the direction its modport
gives, and every ``port.signal`` reference becomes ``port_signal``. The logic
itself is not edited, so simulations run the boards' real consumers.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ROOT / "lib/voltcyclone-fpga"
HEADER = BOARDS / "CaptainDMA/100t484-1/src/pcileech_header.svh"


def module_text(path: Path, name: str) -> str:
    text = path.read_text()
    start = text.index(f"module {name}")
    return text[start : text.index("endmodule", start) + len("endmodule")]


def _interfaces(header: str) -> dict:
    """{interface: {"signals": {name: width}, "modports": {modport: {name: dir}}}}"""
    result = {}
    for name, body in re.findall(r"interface (\w+);(.*?)endinterface", header, re.S):
        signals = {}
        for width, names in re.findall(
            r"^\s*(?:wire|logic|bit)\s*(\[[^\]]+\])?\s*([\w\s,]+);", body, re.M
        ):
            for sig in re.findall(r"\w+", names):
                signals[sig] = width or ""
        modports = {}
        for mp, ports in re.findall(r"modport (\w+)\s*\((.*?)\);", body, re.S):
            dirs, direction = {}, None
            for token in re.findall(r"\w+", ports):
                if token in ("input", "output"):
                    direction = token
                else:
                    dirs[token] = direction
            modports[mp] = dirs
        result[name] = {"signals": signals, "modports": modports}
    return result


def flatten(module: str, header: Path = HEADER) -> str:
    interfaces = _interfaces(header.read_text())
    ports = re.findall(r"^\s*(If\w+)\.(\w+)\s+(\w+)\s*,?\s*$", module, re.M)
    for iface, modport, port in ports:
        spec = interfaces[iface]
        decls = [
            f"{direction} wire {spec['signals'][sig]} {port}_{sig}"
            for sig, direction in spec["modports"][modport].items()
        ]
        module = re.sub(
            rf"^(\s*){iface}\.{modport}\s+{port}(\s*,?)\s*$",
            lambda m: m.group(1) + ", ".join(decls) + m.group(2),
            module,
            count=1,
            flags=re.M,
        )
        module = re.sub(rf"\b{port}\.(\w+)", rf"{port}_\1", module)
    return module
