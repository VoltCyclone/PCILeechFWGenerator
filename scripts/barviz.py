#!/usr/bin/env python3
"""
barviz.py — Terminal visualizer for BAR-like byte blobs.

Modes:
  - entropy: rolling Shannon entropy bars (0..8)
  - heatmap: per-byte intensity as blocks (rows of N bytes)
  - hist:    byte-value distribution (bucketed)

Auto-uses Rich if installed; otherwise falls back to ASCII.
"""

import argparse
import math
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional, Tuple

# -------- Optional Rich detection --------
_HAVE_RICH = True
try:
    from rich.console import Console
    from rich.bar import Bar
    from rich.text import Text
    from rich.theme import Theme
except Exception:
    _HAVE_RICH = False
    Console = None
    Bar = None
    Text = None


# ---------- Core stats ----------

def shannon_entropy(buf: bytes) -> float:
    """Calculate Shannon entropy (bits per byte)."""
    if not buf:
        return 0.0
    c = Counter(buf)
    total = len(buf)
    e = 0.0
    for n in c.values():
        p = n / total
        e -= p * math.log2(p)
    return e


def quick_stats(buf: bytes) -> Tuple[float, int, int]:
    """Return (entropy, size, unique_bytes)."""
    if not buf:
        return 0.0, 0, 0
    c = Counter(buf)
    return shannon_entropy(buf), len(buf), len(c)


# ---------- ASCII renderers ----------

def ascii_heatmap(
    data: bytes, width: int = 64, max_rows: Optional[int] = None
) -> Iterable[str]:
    """Generate ASCII heatmap rows."""
    shades = " .:-=+*#%@"
    rows = (len(data) + width - 1) // width
    if max_rows is not None:
        rows = min(rows, max_rows)
    for r in range(rows):
        row = data[r * width:(r + 1) * width]
        line = ''.join(shades[b * (len(shades) - 1) // 255] for b in row)
        yield line


def ascii_entropy_plot(
    data: bytes, window: int = 4096, step: int = 1024, width: int = 50
) -> Iterable[str]:
    """Generate ASCII entropy plot lines."""
    mv = memoryview(data)
    i = 0
    while i < len(data):
        chunk = mv[i:i + window].tobytes()
        e = shannon_entropy(chunk)
        bars = int((e / 8.0) * width)
        yield f"{i:08X} | {'█' * bars} {e:0.2f}"
        i += step


def ascii_hist(data: bytes, bucket: int = 16, width: int = 50) -> Iterable[str]:
    """Generate ASCII histogram lines."""
    c = Counter(data)
    # Compress into buckets of size `bucket`
    bins = []
    for start in range(0, 256, bucket):
        count = sum(c[v] for v in range(start, min(256, start + bucket)))
        bins.append((start, min(255, start + bucket - 1), count))
    m = max((b[2] for b in bins), default=1)
    for start, end, cnt in bins:
        fill = int((cnt / m) * width) if m else 0
        yield f"{start:03d}-{end:03d}: {'#' * fill} ({cnt})"


# ---------- Rich renderers ----------

def rich_console() -> Console:
    """Create Rich console with custom theme."""
    theme = Theme({
        "entropy.high": "bold green",
        "entropy.mid": "bold yellow1",
        "entropy.low": "bold red",
        "dim": "grey50",
    })
    return Console(theme=theme, force_terminal=True)


def rich_entropy_row(
    console: Console, offset: int, e: float, width: int, vmax: float = 8.0
):
    """Print a single entropy row with Rich formatting."""
    # Color by threshold
    if e >= 7.5:
        style = "entropy.high"
    elif e >= 6.0:
        style = "entropy.mid"
    else:
        style = "entropy.low"
    bar_len = int((e / vmax) * width)
    console.print(f"{offset:08X} | ", end="")
    console.print("█" * bar_len, style=style, end="")
    console.print(f" {e:0.2f}")


def rich_entropy_plot(
    data: bytes, window: int = 4096, step: int = 1024, width: int = 50
):
    """Generate Rich entropy plot."""
    console = rich_console()
    mv = memoryview(data)
    i = 0
    while i < len(data):
        chunk = mv[i:i + window].tobytes()
        e = shannon_entropy(chunk)
        rich_entropy_row(console, i, e, width)
        i += step


def rich_heatmap(data: bytes, width: int = 64, max_rows: Optional[int] = None):
    """Generate Rich heatmap with grayscale blocks."""
    console = rich_console()
    rows = (len(data) + width - 1) // width
    if max_rows is not None:
        rows = min(rows, max_rows)
    for r in range(rows):
        row = data[r * width:(r + 1) * width]
        t = Text()
        for b in row:
            # map byte value to grayscale
            t.append("█", style=f"rgb({b},{b},{b})")
        console.print(t)


def rich_hist(data: bytes, bucket: int = 16, width: int = 40):
    """Generate Rich histogram with colored bars."""
    console = rich_console()
    c = Counter(data)
    # build buckets
    series = []
    for start in range(0, 256, bucket):
        count = sum(c[v] for v in range(start, min(256, start + bucket)))
        series.append((start, min(255, start + bucket - 1), count))
    m = max((cnt for _, _, cnt in series), default=1)
    for start, end, cnt in series:
        # Use Bar for proportional fill; color by density
        frac = (cnt / m) if m else 0.0
        color = "cyan" if frac >= 0.66 else ("magenta" if frac >= 0.33 else "blue")
        console.print(f"{start:03d}-{end:03d}: ",
                      Bar(size=width, value=cnt, end=m, color=color),
                      f" {cnt}")


# ---------- IO ----------

def read_bytes(path: str) -> bytes:
    """Read bytes from file or stdin."""
    if path == "-":
        import sys
        return sys.stdin.buffer.read()
    return Path(path).read_bytes()


# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(
        description="Terminal visualizer for BAR-like binary blobs."
    )
    p.add_argument(
        "--file",
        "-f",
        action="append",
        required=True,
        help="Path to file (use '-' for stdin). Can be repeated.",
    )
    p.add_argument(
        "--mode",
        "-m",
        choices=["entropy", "heatmap", "hist", "summary"],
        default="entropy",
    )
    p.add_argument(
        "--width",
        type=int,
        default=64,
        help="Heatmap row width (bytes) or bar width (chars).",
    )
    p.add_argument(
        "--window",
        type=int,
        default=4096,
        help="Entropy window size (bytes).",
    )
    p.add_argument(
        "--step", type=int, default=1024, help="Entropy step size (bytes)."
    )
    p.add_argument(
        "--bucket",
        type=int,
        default=16,
        help="Histogram bucket size (1..256).",
    )
    p.add_argument(
        "--max-rows",
        type=int,
        default=512,
        help="Max rows to print in heatmap (avoid floods).",
    )
    p.add_argument(
        "--no-rich",
        action="store_true",
        help="Force ASCII; ignore Rich even if installed.",
    )
    args = p.parse_args()

    use_rich = _HAVE_RICH and (not args.no_rich)

    for fp in args.file:
        data = read_bytes(fp)
        label = fp if fp != "-" else "<stdin>"

        if args.mode == "summary":
            e, size, uniq = quick_stats(data)
            if use_rich:
                console = rich_console()
                console.rule(f"[bold]Summary — {label}")
                console.print(f"Size: {size:,} bytes")
                console.print(
                    f"Entropy: [entropy.high]{e:0.4f}[/] bits/byte (max 8)"
                )
                console.print(f"Unique byte values: {uniq}/256")
                console.rule()
            else:
                print(f"== Summary — {label} ==")
                print(f"Size: {size:,} bytes")
                print(f"Entropy: {e:0.4f} bits/byte (max 8)")
                print(f"Unique byte values: {uniq}/256")
            continue

        if args.mode == "entropy":
            if use_rich:
                if label:
                    rich_console().rule(f"[bold]Entropy — {label}")
                rich_entropy_plot(
                    data, window=args.window, step=args.step, width=args.width
                )
            else:
                print(f"== Entropy — {label} ==")
                for line in ascii_entropy_plot(
                    data, window=args.window, step=args.step, width=args.width
                ):
                    print(line)

        elif args.mode == "heatmap":
            if use_rich:
                if label:
                    rich_console().rule(f"[bold]Heatmap — {label}")
                rich_heatmap(data, width=args.width, max_rows=args.max_rows)
            else:
                print(f"== Heatmap — {label} ==")
                for line in ascii_heatmap(data, width=args.width, max_rows=args.max_rows):
                    print(line)

        elif args.mode == "hist":
            if use_rich:
                if label:
                    rich_console().rule(f"[bold]Histogram — {label}")
                rich_hist(
                    data, bucket=args.bucket, width=max(10, args.width)
                )
            else:
                print(f"== Histogram — {label} ==")
                for line in ascii_hist(
                    data, bucket=args.bucket, width=args.width
                ):
                    print(line)


if __name__ == "__main__":
    main()
