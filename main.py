#!/usr/bin/env python3
"""
Command-line entry point for the Virtual Oscilloscope Data Plotter.

This module wires together the core plotting logic from `oscilloscope_plotter`
with a modern argparse-based CLI.

Supported features (parity with the GUI):
  - .dat / .txt  (auto-delimiter)
  - .csv         (configurable delimiter, header, skiprows, decimal)
  - .json        (configurable orient)
  - Time-domain plots, merged or individual subplots
  - Phase / Lissajous plot
  - FFT / spectrum plot
  - Per-channel scale and offset  (y = scale * raw + offset)
  - Log Y-axis
  - Output saved to plots/ subfolder next to the input file
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import numpy as np

from oscilloscope_plotter import OscilloscopePlotter


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize oscilloscope data from .dat / .csv / .json files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (prompts for columns and labels)
  python main.py data/c1.dat

  # Plot columns 1-4, save PNGs to data/plots/
  python main.py -n -c "1-4" -l "CH1,CH2,CH3,CH4" data/c1.dat

  # Phase (Lissajous) plot
  python main.py -n --phase "1,2" -l "X,Y" -o phase.png data/c1.dat

  # CSV with semicolon delimiter and European decimal
  python main.py -n --fmt csv --sep ";" --decimal "," -c "1,2" data/export.csv

  # FFT of channel 3
  python main.py -n --fft 3 data/c1.dat

  # Merged time plot with log Y axis
  python main.py -n -c "1-4" --merge --logy data/c1.dat
        """,
    )

    # ── Positional ──────────────────────────────────────────────────────────
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to the data file (.dat, .txt, .csv, .json)",
    )

    # ── Mode ────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--non-interactive", "-n",
        action="store_true",
        help="Run without interactive prompts",
    )

    # ── Channel selection ────────────────────────────────────────────────────
    parser.add_argument(
        "--columns", "-c",
        help='Columns to plot, e.g. "1,3,5" or "1-4"',
    )
    parser.add_argument(
        "--labels", "-l",
        help='Comma-separated labels for selected columns, e.g. "Voltage,Current"',
    )
    parser.add_argument(
        "--scale",
        help='Comma-separated scale factors per selected column (default: all 1.0), e.g. "1.0,0.001"',
    )
    parser.add_argument(
        "--offset",
        help='Comma-separated offsets per selected column (default: all 0.0), e.g. "0,100"',
    )

    # ── Plot types ───────────────────────────────────────────────────────────
    parser.add_argument(
        "--phase", "-p",
        help='Phase / Lissajous plot using two columns, e.g. "1,2" (X,Y)',
    )
    parser.add_argument(
        "--fft",
        type=int,
        metavar="CHANNEL",
        help="Generate FFT spectrum plot for the given channel number",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge all selected channels into one time-domain plot",
    )
    parser.add_argument(
        "--logy",
        action="store_true",
        help="Use logarithmic Y axis for time-domain plots",
    )

    # ── Output ───────────────────────────────────────────────────────────────
    parser.add_argument(
        "--output", "-o",
        help=(
            "Output file path for saving the plot. "
            "If omitted, files are saved automatically to a plots/ subfolder "
            "next to the input file."
        ),
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save any PNG files (display only)",
    )

    # ── File format ──────────────────────────────────────────────────────────
    fmt_group = parser.add_argument_group(
        "File format options",
        "These options are only relevant for .csv and .json files.",
    )
    fmt_group.add_argument(
        "--fmt",
        choices=["dat", "csv", "json"],
        default=None,
        help=(
            "Force a specific file format. "
            "If omitted, the format is inferred from the file extension."
        ),
    )
    fmt_group.add_argument(
        "--sep",
        default=",",
        help="CSV column delimiter (default: ',')",
    )
    fmt_group.add_argument(
        "--header",
        type=int,
        default=0,
        help="CSV header row index, 0-based. Use -1 for no header (default: 0)",
    )
    fmt_group.add_argument(
        "--skiprows",
        type=int,
        default=0,
        help="Number of rows to skip at the top of a CSV file (default: 0)",
    )
    fmt_group.add_argument(
        "--decimal",
        default=".",
        help="Decimal separator for CSV files (default: '.'). Use ',' for European locales.",
    )
    fmt_group.add_argument(
        "--orient",
        default="columns",
        choices=["columns", "records", "index", "split", "values"],
        help="pandas read_json orient for JSON files (default: 'columns')",
    )

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_fmt(path: Path) -> str:
    return {"csv": "csv", "json": "json"}.get(path.suffix.lower().lstrip("."), "dat")


def _plots_dir(input_path: Path) -> Path:
    """Return (and create) a plots/ subfolder next to the input file."""
    d = input_path.parent / "plots"
    d.mkdir(exist_ok=True)
    return d


def _parse_floats(raw: str | None, n: int, default: float) -> list[float]:
    """Parse a comma-separated float string into a list of length n."""
    if not raw:
        return [default] * n
    parts = [s.strip() for s in raw.split(",")]
    result = []
    for p in parts:
        try:
            result.append(float(p))
        except ValueError:
            result.append(default)
    # Pad or truncate to exactly n entries
    if len(result) < n:
        result.extend([default] * (n - len(result)))
    return result[:n]


# ---------------------------------------------------------------------------
# Plotting helpers (mirror what the GUI does internally)
# ---------------------------------------------------------------------------

def _plot_time(
    plotter: OscilloscopePlotter,
    scales: list[float],
    offsets: list[float],
    merge: bool,
    logy: bool,
    save_path: str,
) -> None:
    import matplotlib.pyplot as plt

    cols = plotter.selected_columns
    n = len(cols)

    if merge:
        fig, ax = plt.subplots(figsize=(12, 5))
        axes = [ax] * n
    else:
        fig, axes_arr = plt.subplots(n, 1, figsize=(12, 3 * n))
        axes = [axes_arr] if n == 1 else list(axes_arr)

    x = np.arange(len(plotter.data))

    for i, col_idx in enumerate(cols):
        scale  = scales[i]
        offset = offsets[i]
        y = scale * plotter.data[:, col_idx - 1] + offset
        label = plotter.column_labels.get(col_idx, f"Channel {col_idx}")
        axes[i].plot(x, y, linewidth=1.2, label=label)
        if not merge:
            axes[i].set_title(label)
            axes[i].set_ylabel("Amplitude")
            axes[i].grid(True, alpha=0.3)
            axes[i].legend()
            if logy:
                axes[i].set_yscale("log")

    if merge:
        axes[0].set_title("Merged channels")
        axes[0].set_ylabel("Amplitude")
        axes[0].set_xlabel("Sample Number / Time")
        axes[0].grid(True, alpha=0.3)
        axes[0].legend()
        if logy:
            axes[0].set_yscale("log")
    else:
        axes[-1].set_xlabel("Sample Number / Time")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"Time plot saved to: {save_path}")
    plt.show()


def _plot_fft(
    plotter: OscilloscopePlotter,
    channel: int,
    scale: float,
    offset: float,
    save_path: str,
) -> None:
    import matplotlib.pyplot as plt

    y_raw = plotter.data[:, channel - 1]
    y = scale * y_raw + offset
    y = y - np.mean(y)
    n = len(y)
    freqs    = np.fft.rfftfreq(n, d=1.0)
    spectrum = np.abs(np.fft.rfft(y))

    label = plotter.column_labels.get(channel, f"Channel {channel}")
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(freqs, spectrum, linewidth=1.0)
    ax.set_xlabel("Frequency (1 / sample)")
    ax.set_ylabel("Amplitude")
    ax.set_title(f"FFT: {label}")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"FFT plot saved to: {save_path}")
    plt.show()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plotter = OscilloscopePlotter()

    # ── No file given → interactive mode (dat only) ─────────────────────────
    if not args.file:
        plotter.run_interactive()
        return 0

    file_path = Path(args.file)

    # ── Determine format ─────────────────────────────────────────────────────
    fmt = args.fmt or _infer_fmt(file_path)

    load_kwargs: dict = {"fmt": fmt}
    if fmt == "csv":
        load_kwargs.update(
            sep      = args.sep,
            header   = None if args.header < 0 else args.header,
            skiprows = args.skiprows,
            decimal  = args.decimal,
        )
    elif fmt == "json":
        load_kwargs.update(orient=args.orient)

    if not plotter.load_data(file_path, **load_kwargs):
        return 1

    num_columns = plotter.data.shape[1]

    # ── Interactive mode (non-dat files still supported) ─────────────────────
    if not args.non_interactive:
        if plotter.get_user_input():
            save_plot = bool(args.output) and not args.no_save
            plotter.plot_data(save_plot=save_plot, output_path=args.output or "")
        return 0

    # ── Non-interactive: resolve output directory ─────────────────────────────
    auto_dir = _plots_dir(file_path) if not args.no_save else None
    stem     = file_path.stem

    def _out(suffix: str) -> str:
        if args.no_save:
            return ""
        if args.output:
            return args.output
        return str(auto_dir / f"{stem}_{suffix}.png")  # type: ignore[operator]

    # ── Phase plot ────────────────────────────────────────────────────────────
    if args.phase:
        try:
            x_str, y_str = [s.strip() for s in args.phase.split(",")]
            x_col, y_col = int(x_str), int(y_str)
        except Exception:
            print('Error: --phase expects two comma-separated integers, e.g. "1,2"')
            return 1

        if args.labels:
            parts = [s.strip() for s in args.labels.split(",")]
            if len(parts) >= 1:
                plotter.column_labels[x_col] = parts[0]
            if len(parts) >= 2:
                plotter.column_labels[y_col] = parts[1]

        plotter.plot_phase(
            x_col      = x_col,
            y_col      = y_col,
            save_plot  = not args.no_save,
            output_path= _out("phase"),
        )
        return 0

    # ── FFT plot ──────────────────────────────────────────────────────────────
    if args.fft:
        ch = args.fft
        if not (1 <= ch <= num_columns):
            print(f"Error: --fft channel {ch} out of range (1–{num_columns})")
            return 1

        if args.labels:
            parts = [s.strip() for s in args.labels.split(",")]
            if parts:
                plotter.column_labels[ch] = parts[0]

        scales  = _parse_floats(args.scale,  1, 1.0)
        offsets = _parse_floats(args.offset, 1, 0.0)

        _plot_fft(
            plotter   = plotter,
            channel   = ch,
            scale     = scales[0],
            offset    = offsets[0],
            save_path = _out(f"fft_ch{ch}"),
        )
        return 0

    # ── Time-domain plots ─────────────────────────────────────────────────────
    if args.columns:
        selected = plotter.parse_column_input(args.columns, num_columns)
        if not selected:
            print("Error: Invalid column specification")
            return 1
    else:
        selected = list(range(1, min(5, num_columns + 1)))

    plotter.selected_columns = selected
    n = len(selected)

    # Labels
    if args.labels:
        parts = [s.strip() for s in args.labels.split(",")]
        for i, col in enumerate(selected):
            plotter.column_labels[col] = parts[i] if i < len(parts) else f"Channel {col}"
    else:
        for col in selected:
            plotter.column_labels[col] = f"Channel {col}"

    scales  = _parse_floats(args.scale,  n, 1.0)
    offsets = _parse_floats(args.offset, n, 0.0)

    _plot_time(
        plotter   = plotter,
        scales    = scales,
        offsets   = offsets,
        merge     = args.merge,
        logy      = args.logy,
        save_path = _out("time"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))