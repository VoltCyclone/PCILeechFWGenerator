"""Word-order conversion between logical PCI DWORDs and shadow-BRAM COE words.

Generator code (templates, writemask policy, tests) works in *logical* PCI
DWORD values: config byte +0 in bits [7:0], exactly what ``ConfigSpace`` and
Linux read. The PCILeech shadow config-space BRAM stores the opposite order:
config byte +0 lives in BRAM bits [31:24]. Evidence in the upstream RTL
(lib/voltcyclone-fpga/*/src):

* ``pcileech_tlps128_cfgspace_shadow.sv`` takes the CfgWr payload DWORD
  unswapped and reverses the byte enables
  (``pcie_rx_be = {tdata[32], tdata[33], tdata[34], tdata[35]}``), so
  First-BE bit 0 (byte +0) drives BRAM lane 3, bits [31:24].
* ``pcileech_tlps128_bar_controller.sv`` byte-swaps every payload DWORD
  before pairing it with an unswapped First-BE, i.e. AXI payload byte 0 is
  bits [31:24].
* ``pcileech_fifo.sv``'s USB shadow path places LeechCore's ``pb[i]``
  (``in_cmd_value[7:0]``) in ``rx_data[31:24]`` with ``be[3]``.

Every COE written for the BRAM/DROM is therefore converted here, once, and
tagged with :data:`BRAM_ORDER_MARKER` so readers can convert back.
"""

from __future__ import annotations

import re
from typing import List

BRAM_ORDER_MARKER = "; word_order=bram_msb_first"

_VECTOR_RE = re.compile(r"memory_initialization_vector\s*=", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[0-9A-Fa-f]+")


def bswap32(value: int) -> int:
    """Reverse the four bytes of a 32-bit value."""
    return int.from_bytes((value & 0xFFFFFFFF).to_bytes(4, "little"), "big")


def is_bram_order(text: str) -> bool:
    """Return True when the COE text carries the BRAM word-order marker."""
    return any(line.strip() == BRAM_ORDER_MARKER for line in text.splitlines())


def _split_vector(text: str):
    match = _VECTOR_RE.search(text)
    if not match:
        raise ValueError("COE text has no memory_initialization_vector")
    return text[: match.end()], text[match.end() :]


def _convert_data_line(line: str) -> str:
    code, sep, comment = line.partition(";")
    pieces = re.split(r"([,\s]+)", code)
    out = []
    for piece in pieces:
        if not piece or re.fullmatch(r"[,\s]+", piece):
            out.append(piece)
            continue
        token = piece[2:] if piece.lower().startswith("0x") else piece
        if not _TOKEN_RE.fullmatch(token) or len(token) > 8:
            raise ValueError(f"Malformed COE word: {piece!r}")
        out.append(f"{bswap32(int(token, 16)):08X}")
    return "".join(out) + sep + comment


def logical_coe_to_bram(text: str) -> str:
    """Convert logical-DWORD COE text to BRAM word order (idempotent).

    Comments and layout are preserved; only data words in the
    ``memory_initialization_vector`` section are byte-reversed. The marker
    line is inserted at the top of the file.
    """
    if is_bram_order(text):
        return text
    head, body = _split_vector(text)
    converted = "\n".join(_convert_data_line(line) for line in body.split("\n"))
    return f"{BRAM_ORDER_MARKER}\n{head}{converted}"


def parse_coe_logical_words(text: str) -> List[int]:
    """Parse COE data words and return logical PCI DWORD values."""
    swap = is_bram_order(text)
    _, body = _split_vector(text)
    words: List[int] = []
    for line in body.split("\n"):
        code = line.split(";", 1)[0]
        for token in re.split(r"[,\s]+", code):
            if not token:
                continue
            if token.lower().startswith("0x"):
                token = token[2:]
            if not _TOKEN_RE.fullmatch(token) or len(token) > 8:
                continue
            value = int(token, 16)
            words.append(bswap32(value) if swap else value)
    return words
