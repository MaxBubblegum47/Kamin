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

### Workflow

1. **Load a file** — click **Browse…** and select your data file (`.dat`, `.txt`, `.csv`, or `.json`).
2. **Configure tracks** — the Tracks / Channels table is populated automatically. For each channel you can:
   - Enable / disable it with the checkbox
   - Edit the label
   - Set a scale factor and offset (applied as `y = scale × raw + offset`)
3. **Choose plot types** in the Plot options panel (right side).
4. Click **Generate Plots**.

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
| **Time-domain plots** | One subplot per enabled channel (or all merged into one) |
| **Merge channels** | Overlays all enabled channels on a single axes |
| **Phase / Lissajous** | Plots Y channel vs X channel; great for checking quadrature signals |
| **Spectrum (FFT)** | Mean-removed FFT of the selected channel, plotted vs normalised frequency |
| **Log scale (Y axis)** | Applies `yscale("log")` to time-domain plots |

### Other GUI features

- **Show stats** — displays min, max, mean, and RMS for every enabled channel.
- **Detach preview** — pops the plot panel out into a separate, resizable window. Closing that window re-attaches it automatically.
- **Reload** — re-reads the file from disk using the current import options (useful after editing a CSV).
- **Navigation toolbar** — each plot tab has a full matplotlib toolbar (pan, zoom, save individual figure).

### PNG export

Every time you click **Generate Plots**, the embedded figures are automatically saved as high-resolution PNGs (300 dpi) into a `plots/` subfolder next to the data file:

```
data/
├── c1.dat
└── plots/
    ├── c1_gui_1.png   ← Channel_1 time plot
    ├── c1_gui_2.png   ← Channel_2 time plot
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
| `--labels` | `-l` | Comma-separated labels, e.g. `"Voltage,Current"` |
| `--output` | `-o` | Save plot to this path |
| `--non-interactive` | `-n` | Skip all prompts |
| `--phase` | `-p` | Phase plot from two columns, e.g. `"1,2"` (X,Y) |

### Examples

```bash
# Interactive mode
python main.py

# Plot columns 1–4 with labels, save to file
python main.py -n -c "1-4" -l "CH1,CH2,CH3,CH4" -o out.png data/c1.dat

# Phase (Lissajous) plot
python main.py -n --phase "1,2" -l "X,Y" -o phase.png data/c1.dat
```

> **Note:** The CLI only supports `.dat` / `.txt` files (auto-delimiter detection). Use the GUI for `.csv` and `.json`.

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
| `TypeError: load_data() got unexpected keyword argument 'fmt'` | Replace `oscilloscope_plotter.py` with the updated version — the old file predates CSV/JSON support. |
| CSV loads but all channels are missing | Open **Import options…** and check the delimiter and header row settings. |
| Checkboxes appear but don't show a tick | Ensure `win98_check.svg` can be written to the system temp folder. Check write permissions on `%TEMP%`. |
| "Could not load data" on a `.dat` file | Verify the file contains only numeric data and that comment lines start with `#`. |
| Plots not saved | Check that the `plots/` subfolder can be created next to the data file (write permissions). |