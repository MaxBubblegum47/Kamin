# Virtual Oscilloscope Data Plotter

A Python tool for visualizing oscilloscope data. Supports both a **graphical interface** (PyQt6) and a **command-line interface** for batch processing.

---

## Project structure

```
Kamin/
├── gui_main.py              # Entry point for the GUI
├── main.py                  # Entry point for the CLI
├── oscilloscope_gui.py      # PyQt6 GUI implementation
├── oscilloscope_plotter.py  # Core data loading and plotting logic
├── requirements.txt
└── data/                    # Put your data files here
    └── plots/               # PNGs are saved here automatically
```

---

## Installation

```bash
pip install -r requirements.txt
```

**Dependencies:** `numpy`, `matplotlib`, `pandas`, `PyQt6`

---

## Graphical Interface (recommended)

```bash
python gui_main.py
```

### Layout

The window is divided into three independently resizable panels (drag any divider):

| Panel | Content |
|-------|---------|
| **Tracks / Channels** | Table of all loaded columns; configure per-channel label, scale, offset, and enable/disable |
| **Plot options** | Time-domain, phase, FFT, CNC tool path, and output settings |
| **Plot preview** | Tabbed matplotlib canvas with full navigation toolbar per plot |

### Workflow

1. **Load a file** — click **Browse…** and select your data file (`.dat`, `.txt`, `.csv`, or `.json`).
2. **Configure tracks** — the Tracks / Channels table is populated automatically. For each channel you can:
   - Enable / disable it with the checkbox
   - Edit the label
   - Set a scale factor and offset (`y = scale × raw + offset`)
3. **Choose plot types** in the Plot options panel.
4. Click **Generate Plots** — plots appear as tabs in the preview panel as they are computed.

### Supported file formats

| Format | Notes |
|--------|-------|
| `.dat` / `.txt` | Whitespace, tab, comma, or semicolon separated. Comment lines starting with `#` are ignored. Auto-detected. |
| `.csv` | Comma separated by default. Click **Import options…** to customise. |
| `.json` | pandas `columns` orient by default. Click **Import options…** to change. |

#### CSV import options
Accessible via the **Import options…** button (enabled only for `.csv` and `.json` files):

- **Delimiter** — character separating columns (default `,`)
- **Header row** — 0-based row index containing column names; set to `-1` for no header
- **Skip rows at top** — number of lines to skip before reading data
- **Decimal separator** — use `,` for European locale files

#### JSON import options
- **Orient** — pandas `read_json` orient: `columns`, `records`, `index`, `split`, or `values`

### Plot types

| Option | Description |
|--------|-------------|
| **Time-domain plots** | One tab per enabled channel (or all merged into one) |
| **Merge channels** | Overlays all enabled channels on a single axes |
| **Phase / Lissajous** | Plots Y channel vs X channel |
| **Spectrum (FFT)** | Mean-removed FFT of the selected channel, plotted vs normalised frequency |
| **Log scale (Y axis)** | Applies `yscale("log")` to time-domain plots |

### CNC Tool Path

The **CNC Tool Path** section lets you visualize the 2D trajectory of a CNC machine and analyse its precision. It is independent of the main data file — you can load one or more separate trajectory files.

**Controls:**

| Control | Description |
|---------|-------------|
| X / Y channel | Column numbers for the two position axes |
| Downsample (every N) | Plot every Nth sample to speed up rendering of large files |
| Error ch 1 | Column containing the error signal (contour error, radial error, etc.) |
| Error ch 2 (Y arrow) | Second error component for arrow mode (Y-direction of the error vector) |
| Display mode | See table below |
| Exaggeration × | Deviation scale factor for polar mode |

**Display modes:**

| Mode | Description |
|------|-------------|
| **Plain path** | Simple XY trajectory line |
| **Color by error** | Path colored green → red by error magnitude; shows *where* precision is worst |
| **Deviation arrows** | Small arrows at each point show direction and magnitude of the error vector |
| **Polar deviation** | For circular cuts: radial deviation from the ideal circle, exaggerated for visibility |

> **Polar deviation tip:** the Exaggeration × factor multiplies the deviation before drawing — e.g. ×100 makes a 0.05 mm error on a 50 mm circle appear as 5 mm in the plot. Start at ×100 and adjust.

> **Zoom in polar mode:** the toolbar zoom box does not work on polar axes (matplotlib limitation). Use the **scroll wheel** to zoom in/out instead.

### Other GUI features

- **Show stats** — displays min, max, mean, and RMS for every enabled channel.
- **Detach preview** — pops the plot panel out into a separate, resizable window. Closing that window re-attaches it to its original position automatically.
- **Reload** — re-reads the file from disk using the current import options (useful after editing a CSV).
- **Navigation toolbar** — each plot tab has a full matplotlib toolbar (pan, zoom, save individual figure).
- **Session persistence** — when you close and reopen the app, all settings are restored automatically: loaded file, track configuration, plot options, toolpath files, and window layout. If a previously loaded file is no longer on disk, a warning is shown and the rest of the session is still restored.
- **Threaded plot generation** — plots are computed in parallel background threads, so the GUI stays responsive. Tabs appear one by one as each plot finishes. PNG files are also saved in the background.

### PNG export

Every time you click **Generate Plots**, the embedded figures are automatically saved as high-resolution PNGs (300 dpi) into a `plots/` subfolder next to the data file:

```
data/
├── c1.dat
└── plots/
    ├── c1_gui_1.png
    ├── c1_gui_2.png
    └── ...
```

---

## Command-line interface

```bash
python main.py [OPTIONS] [file]
```

Run without arguments to start the interactive prompt.

### Options

| Flag | Short | Description |
|------|-------|-------------|
| `--columns` | `-c` | Columns to plot, e.g. `"1,3,5"` or `"1-4"` |
| `--labels` | `-l` | Comma-separated labels |
| `--scale` | | Comma-separated scale factors per column |
| `--offset` | | Comma-separated offsets per column |
| `--output` | `-o` | Save plot to this path |
| `--no-save` | | Display only, do not write PNG files |
| `--non-interactive` | `-n` | Skip all prompts |
| `--phase` | `-p` | Phase plot from two columns, e.g. `"1,2"` (X,Y) |
| `--fft` | | FFT plot for the given channel number |
| `--merge` | | Merge all selected channels into one plot |
| `--logy` | | Logarithmic Y axis |

#### File format options

| Flag | Default | Description |
|------|---------|-------------|
| `--fmt` | auto | Force format: `dat`, `csv`, or `json` |
| `--sep` | `,` | CSV delimiter |
| `--header` | `0` | CSV header row (0-based); `-1` = no header |
| `--skiprows` | `0` | Rows to skip at the top of a CSV |
| `--decimal` | `.` | Decimal separator (use `,` for European locales) |
| `--orient` | `columns` | pandas `read_json` orient for JSON files |

#### CNC Tool Path options

| Flag | Default | Description |
|------|---------|-------------|
| `--toolpath FILE [FILE...]` | | One or more trajectory files to plot |
| `--tp-x CH` | `1` | X-axis channel |
| `--tp-y CH` | `2` | Y-axis channel |
| `--tp-ds N` | `1` | Downsample: plot every Nth sample |
| `--tp-err CH` | `0` | Error channel (0 = none) |
| `--tp-err2 CH` | `0` | Second error channel for arrow mode |
| `--tp-mode` | `plain` | Display mode: `plain`, `color`, `arrows`, or `polar` |
| `--tp-exag` | `100` | Exaggeration factor for polar mode |

### Examples

```bash
# Interactive mode
python main.py

# Plot columns 1–4 with labels
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

# CNC tool path — path colored by error in channel 3
python main.py --toolpath data/cut.dat --tp-x 1 --tp-y 2 --tp-err 3 --tp-mode color

# CNC polar deviation — circular cut precision, ×200 exaggeration
python main.py --toolpath data/cut.dat --tp-x 1 --tp-y 2 --tp-err 3 --tp-mode polar --tp-exag 200

# Multiple toolpath files overlaid (e.g. before/after tuning)
python main.py --toolpath data/pass1.dat data/pass2.dat --tp-x 1 --tp-y 2
```

---

## Column selection syntax

| Syntax | Meaning |
|--------|---------|
| `1,3,5` | Columns 1, 3, and 5 |
| `1-4` | Columns 1 through 4 |
| `1,3-5,7` | Columns 1, 3, 4, 5, and 7 |

Columns are **1-based**.

---

## Data format (`.dat` / `.txt`)

```
# Comment lines are ignored
0.0    1.2    0.8    2.1
0.1    1.4    0.9    2.3
0.2    1.6    1.1    2.5
```

- Delimiters: whitespace, tab, comma, or semicolon (auto-detected)
- Non-numeric and all-NaN rows are dropped automatically
- Up to 16 channels recommended; no hard limit

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| CSV loads but all channels are missing | Open **Import options…** and check the delimiter and header row settings. |
| Checkboxes appear but don't show a tick | Ensure `win98_check.svg` can be written to the system temp folder. Check write permissions on `%TEMP%`. |
| "Could not load data" on a `.dat` file | Verify the file contains only numeric data and that comment lines start with `#`. |
| Plots not saved | Check that the `plots/` subfolder can be created next to the data file (write permissions). |
| Session file not found warning on startup | The file was moved or deleted. Dismiss the warning — all other settings are still restored. |
| Polar zoom doesn't work with toolbar | Use the scroll wheel to zoom in/out on polar plots. |
