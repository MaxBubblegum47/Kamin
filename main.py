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
  - CNC tool path (plain, error-colored, deviation arrows, polar deviation)
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
  python main.py -n --phase "1,2" -l "X,Y" data/c1.dat

  # CSV with semicolon delimiter and European decimal
  python main.py -n --fmt csv --sep ";" --decimal "," -c "1,2" data/export.csv

  # FFT of channel 3
  python main.py -n --fft 3 data/c1.dat

  # Merged time plot with log Y axis
  python main.py -n -c "1-4" --merge --logy data/c1.dat

  # CNC tool path — plain XY trajectory
  python main.py --toolpath data/cut.dat --tp-x 1 --tp-y 2

  # CNC tool path — line colored by error channel 3
  python main.py --toolpath data/cut.dat --tp-x 1 --tp-y 2 --tp-err 3 --tp-mode color

  # CNC tool path — polar deviation plot (×200 exaggeration)
  python main.py --toolpath data/cut.dat --tp-x 1 --tp-y 2 --tp-err 3 --tp-mode polar --tp-exag 200

  # Multiple toolpath files overlaid
  python main.py --toolpath data/pass1.dat data/pass2.dat --tp-x 1 --tp-y 2
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
        help='Comma-separated scale factors per selected column (default: all 1.0)',
    )
    parser.add_argument(
        "--offset",
        help='Comma-separated offsets per selected column (default: all 0.0)',
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
        "These options apply to the main file and to all --toolpath files.",
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

    # ── CNC Tool Path ─────────────────────────────────────────────────────────
    tp_group = parser.add_argument_group(
        "CNC Tool Path",
        "Plot the tool trajectory from one or more data files. "
        "Can be used without a main file argument.",
    )
    tp_group.add_argument(
        "--toolpath", "--tp",
        nargs="+",
        metavar="FILE",
        help="One or more data files to plot as a CNC tool path (X vs Y)",
    )
    tp_group.add_argument(
        "--tp-x",
        type=int,
        default=1,
        metavar="CH",
        help="X-axis channel for tool path (default: 1)",
    )
    tp_group.add_argument(
        "--tp-y",
        type=int,
        default=2,
        metavar="CH",
        help="Y-axis channel for tool path (default: 2)",
    )
    tp_group.add_argument(
        "--tp-ds",
        type=int,
        default=1,
        metavar="N",
        help="Plot every Nth sample — downsample factor (default: 1 = all samples)",
    )
    tp_group.add_argument(
        "--tp-err",
        type=int,
        default=0,
        metavar="CH",
        help="Error channel number (0 = none). Used for color, arrows, and polar modes.",
    )
    tp_group.add_argument(
        "--tp-err2",
        type=int,
        default=0,
        metavar="CH",
        help="Second error channel (Y-component) for deviation-arrows mode (0 = none)",
    )
    tp_group.add_argument(
        "--tp-mode",
        choices=["plain", "color", "arrows", "polar"],
        default="plain",
        help=(
            "Tool path display mode (default: plain). "
            "'color' colors the path by error magnitude. "
            "'arrows' draws error vectors at each point. "
            "'polar' shows radial deviation from a circular ideal path."
        ),
    )
    tp_group.add_argument(
        "--tp-exag",
        type=float,
        default=100.0,
        metavar="FACTOR",
        help="Exaggeration multiplier for polar deviation mode (default: 100)",
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
        y = scales[i] * plotter.data[:, col_idx - 1] + offsets[i]
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

    y = scale * plotter.data[:, channel - 1] + offset
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


def _plot_toolpath(
    files: list[Path],
    x_ch: int,
    y_ch: int,
    ds: int,
    err_ch: int,
    err2_ch: int,
    mode: str,
    exag: float,
    load_kwargs: dict,
    save_path: str,
) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    from matplotlib.collections import LineCollection

    _COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b"]

    polar = (mode == "polar")
    if polar:
        fig, ax = plt.subplots(subplot_kw={"projection": "polar"}, figsize=(8, 8))
    else:
        fig, ax = plt.subplots(figsize=(8, 8))
        ax.set_aspect("equal", adjustable="datalim")

    any_plotted = False
    for i, path in enumerate(files):
        p = OscilloscopePlotter()
        fmt = load_kwargs.get("fmt") or _infer_fmt(path)
        kw = {**load_kwargs, "fmt": fmt}
        if not p.load_data(path, **kw):
            print(f"Warning: could not load {path}")
            continue
        ncols = p.data.shape[1]
        if x_ch > ncols or y_ch > ncols:
            print(f"Warning: {path.name} has only {ncols} columns — skipping")
            continue

        x = p.data[::ds, x_ch - 1]
        y = p.data[::ds, y_ch - 1]
        color = _COLORS[i % len(_COLORS)]

        has_err  = 0 < err_ch  <= ncols
        has_err2 = 0 < err2_ch <= ncols
        err  = p.data[::ds, err_ch  - 1] if has_err  else None
        err2 = p.data[::ds, err2_ch - 1] if has_err2 else None

        if mode == "color" and has_err:
            points   = np.column_stack([x, y]).reshape(-1, 1, 2)
            segments = np.concatenate([points[:-1], points[1:]], axis=1)
            e = err[:-1]
            norm = mcolors.Normalize(vmin=e.min(), vmax=e.max())
            lc = LineCollection(segments, cmap="RdYlGn_r", norm=norm, linewidth=1.5)
            lc.set_array(e)
            ax.add_collection(lc)
            fig.colorbar(lc, ax=ax, label=f"Error (ch {err_ch})", shrink=0.8)
            ax.autoscale()
            ax.set_title(f"Tool Path — colored by error ch{err_ch}")

        elif mode == "arrows" and has_err:
            ax.plot(x, y, linewidth=0.5, color=color, alpha=0.4, label=path.name)
            ex = err
            ey = err2 if has_err2 else np.zeros_like(ex)
            ax.quiver(x, y, ex, ey, np.hypot(ex, ey),
                      cmap="RdYlGn_r", scale_units="xy", angles="xy",
                      width=0.003, label=f"Error ch{err_ch}")
            ax.set_title("Tool Path — deviation arrows")

        elif polar:
            cx, cy   = x.mean(), y.mean()
            theta    = np.arctan2(y - cy, x - cx)
            r_actual = np.hypot(x - cx, y - cy)
            r_ideal  = r_actual.mean()
            r_dev    = err if has_err else (r_actual - r_ideal)
            order    = np.argsort(theta)
            th_s     = theta[order]
            rd_s     = r_dev[order]
            r_plot   = r_ideal + rd_s * exag
            ax.plot(th_s, np.full_like(th_s, r_ideal),
                    "--", color="grey", linewidth=0.8, label="Ideal")
            ax.plot(th_s, r_plot, linewidth=1.0, color=color, label=path.name)
            ax.set_title(f"Polar deviation (×{exag:.0f} exaggeration)")

        else:
            ax.plot(x, y, linewidth=0.8, color=color, label=path.name)
            ax.set_title("CNC Tool Path")

        any_plotted = True

    if not any_plotted:
        print("Error: no toolpath data could be plotted.")
        return

    if not polar:
        ax.set_xlabel(f"Channel {x_ch}")
        ax.set_ylabel(f"Channel {y_ch}")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        print(f"Tool path plot saved to: {save_path}")
    plt.show()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plotter = OscilloscopePlotter()

    # ── CNC Tool Path (can run without a main file) ───────────────────────────
    if args.toolpath:
        tp_paths = [Path(f) for f in args.toolpath]
        missing  = [p for p in tp_paths if not p.exists()]
        if missing:
            for m in missing:
                print(f"Error: file not found: {m}")
            return 1

        # Use format options from the first file unless --fmt is explicit
        ref_path = tp_paths[0]
        fmt = args.fmt or _infer_fmt(ref_path)
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

        save_path = ""
        if not args.no_save:
            if args.output:
                save_path = args.output
            else:
                save_path = str(_plots_dir(ref_path) / f"{ref_path.stem}_toolpath.png")

        _plot_toolpath(
            files       = tp_paths,
            x_ch        = args.tp_x,
            y_ch        = args.tp_y,
            ds          = args.tp_ds,
            err_ch      = args.tp_err,
            err2_ch     = args.tp_err2,
            mode        = args.tp_mode,
            exag        = args.tp_exag,
            load_kwargs = load_kwargs,
            save_path   = save_path,
        )
        return 0

    # ── No file given → interactive mode ─────────────────────────────────────
    if not args.file:
        plotter.run_interactive()
        return 0

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: file not found: {file_path}")
        return 1

    # ── Determine format ─────────────────────────────────────────────────────
    fmt = args.fmt or _infer_fmt(file_path)

    load_kwargs = {"fmt": fmt}
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

    # ── Interactive mode ──────────────────────────────────────────────────────
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
            x_col       = x_col,
            y_col       = y_col,
            save_plot   = not args.no_save,
            output_path = _out("phase"),
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
            print("Error: invalid column specification")
            return 1
    else:
        selected = list(range(1, min(5, num_columns + 1)))

    plotter.selected_columns = selected
    n = len(selected)

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
