"""Convert a UCC Ireland 5G trace (G-NetTrack CSV) into a link-bandwidth trace.

Input: one CSV from https://github.com/uccmisl/5Gdataset (columns include
Timestamp, DL_bitrate, UL_bitrate, State, NetworkMode, ...; 1 sample/sec;
bitrates in kbit/s).

Output: the simulator's bandwidth-trace format - columns ``t`` (seconds
from 0) and ``bandwidth_bps`` - usable by the ``trace_fluid_link`` network
model.

Method (documented for the thesis's real-vs-derived data separation):
only rows captured during an active download (``State == "D"``) measure
achievable throughput, so idle rows are dropped and the remaining samples
are stitched into a consecutive 1-second timeline. Values are DL_bitrate
(downlink) or UL_bitrate (--uplink) converted kbit/s -> bit/s. Zero-rate
rows within a download (true stalls) are kept.

Usage:
    python tools/prepare_ucc_5g.py INPUT.csv OUTPUT.csv [--uplink]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def convert(src: Path, dst: Path, *, uplink: bool = False) -> int:
    column = "UL_bitrate" if uplink else "DL_bitrate"
    rows_out: list[tuple[float, float]] = []
    with src.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise SystemExit(
                f"error: {src} has no {column!r} column "
                f"(columns: {reader.fieldnames})"
            )
        t = 0.0
        for row in reader:
            if row.get("State") != "D":
                continue  # only active-download samples measure capacity
            raw = row[column]
            if raw in ("", "-"):
                continue
            bandwidth_bps = float(raw) * 1000.0  # kbit/s -> bit/s
            rows_out.append((t, bandwidth_bps))
            t += 1.0

    if not rows_out:
        raise SystemExit(f"error: {src} has no active-download samples")

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["t", "bandwidth_bps"])
        for t, bw in rows_out:
            writer.writerow([repr(t), repr(bw)])
    return len(rows_out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--uplink", action="store_true",
                        help="use UL_bitrate instead of DL_bitrate")
    args = parser.parse_args(argv)
    n = convert(args.input, args.output, uplink=args.uplink)
    mean = None
    print(f"wrote {args.output}: {n} seconds of bandwidth samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
