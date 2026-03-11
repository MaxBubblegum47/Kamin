"""
PyQt-based GUI for the Virtual Oscilloscope Data Plotter.

Sequence of operations:
0) Select and load one .dat / .csv / .json file.
1) Inspect tracks (columns) and assign labels and scaling.
2) Choose options such as time plots, phase plots, and FFT.
3) Generate plots in an embedded matplotlib widget or in a new window.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PyQt6 import QtCore, QtGui, QtWidgets

from oscilloscope_plotter import OscilloscopePlotter


@dataclass
class TrackConfig:
    index: int
    enabled: bool
    label: str
    scale: float = 1.0
    offset: float = 0.0


class MplCanvas(FigureCanvas):
    """Matplotlib canvas embedded in a Qt widget."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        fig: Optional[Figure] = None,
    ) -> None:
        if fig is None:
            fig = Figure(figsize=(6, 4), tight_layout=True)
        super().__init__(fig)
        self.setParent(parent)


class PlotWorker(QtCore.QThread):
    """Runs plot-building tasks off the main thread.

    Each task is a callable that returns (Figure, tab_name).
    Results are emitted one-by-one via *figure_ready* so the UI can
    display each tab as soon as it is computed.  PNG-save tasks return
    (None, "") and are just fire-and-forget.
    """

    figure_ready = QtCore.pyqtSignal(object, str)   # (Figure | None, tab_name)
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str)

    def __init__(
        self,
        tasks: List[Callable[[], Tuple[Optional[Figure], str]]],
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._tasks = tasks

    def run(self) -> None:
        with ThreadPoolExecutor(max_workers=min(len(self._tasks), 8)) as pool:
            future_map = {pool.submit(t): t for t in self._tasks}
            for future in as_completed(future_map):
                try:
                    fig, name = future.result()
                    self.figure_ready.emit(fig, name)
                except Exception as exc:
                    self.error.emit(str(exc))
        self.finished.emit()


class _SaveFigureRunnable(QtCore.QRunnable):
    """QThreadPool task: save a matplotlib Figure to disk without blocking the GUI."""

    def __init__(self, fig: Figure, path: Path) -> None:
        super().__init__()
        self._fig = fig
        self._path = path

    def run(self) -> None:
        try:
            self._fig.savefig(self._path, dpi=300, bbox_inches="tight")
        except Exception:
            pass


class OscilloscopeMainWindow(QtWidgets.QMainWindow):
    """Main GUI window implementing the workflow."""

    # Supported file formats and their pandas read kwargs.
    # Each entry: (display_label, glob_pattern, loader_key)
    # loader_key is passed to OscilloscopePlotter.load_data so it can
    # dispatch to the right reader.
    _SUPPORTED_FORMATS = [
        ("Data files (*.dat *.txt *.csv *.json)", "*.dat *.txt *.csv *.json"),
        ("DAT / TXT (*.dat *.txt)", "*.dat *.txt"),
        ("CSV (*.csv)", "*.csv"),
        ("JSON (*.json)", "*.json"),
        ("All files (*)", "*"),
    ]

    # Colours cycled for each tool-path file loaded
    _TOOLPATH_COLORS = [
        "#1f77b4",  # blue
        "#d62728",  # red
        "#2ca02c",  # green
        "#ff7f0e",  # orange
        "#9467bd",  # purple
        "#8c564b",  # brown
        "#e377c2",  # pink
        "#17becf",  # cyan
    ]

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Virtual Oscilloscope")
        self.resize(1100, 700)

        # Win98-style navy title bar (Windows 11 DWM only; no-op elsewhere)
        try:
            import ctypes
            DWMWA_CAPTION_COLOR = 35
            color = 0x00800000  # BGR order → #000080 navy
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), DWMWA_CAPTION_COLOR,
                ctypes.byref(ctypes.c_int(color)), ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

        # Core plotter
        self.plotter = OscilloscopePlotter()

        # State
        self.current_file: Optional[Path] = None
        self.track_rows: List[TrackConfig] = []
        self._tab_canvases: List[MplCanvas] = []
        self._preview_window: Optional[QtWidgets.QMainWindow] = None
        self._main_layout: Optional[QtWidgets.QVBoxLayout] = None
        self._scaling: dict = {}
        self._toolpath_files: List[dict] = []  # list of {path, color, enabled}

        # UI
        self._setup_ui()
        self._apply_windows98_palette()
        self._load_session()

        # Keep a reference so the worker isn't GC'd while running
        self._plot_worker: Optional[PlotWorker] = None

    # ------------------------------------------------------------------ session persistence
    _SETTINGS_ORG = "VirtualOscilloscope"
    _SETTINGS_APP = "OscilloscopeGUI"

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._save_session()
        super().closeEvent(event)

    def _save_session(self) -> None:
        s = QtCore.QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("splitter_sizes", self._main_splitter.sizes())

        # Data file
        s.setValue("current_file", str(self.current_file) if self.current_file else "")

        # CSV / JSON import options
        s.setValue("csv_sep", getattr(self, "_csv_sep", ","))
        s.setValue("csv_header_row", getattr(self, "_csv_header_row", 0))
        s.setValue("json_orient", getattr(self, "_json_orient", ""))

        # Track table
        configs = self._collect_track_config()
        s.beginWriteArray("tracks")
        for i, cfg in enumerate(configs):
            s.setArrayIndex(i)
            s.setValue("index", cfg.index)
            s.setValue("enabled", cfg.enabled)
            s.setValue("label", cfg.label)
            s.setValue("scale", cfg.scale)
            s.setValue("offset", cfg.offset)
        s.endArray()

        # Plot options
        s.setValue("time_enabled", self.time_enabled_checkbox.isChecked())
        s.setValue("time_merge", self.time_merge_checkbox.isChecked())
        s.setValue("phase_enabled", self.phase_enabled_checkbox.isChecked())
        s.setValue("phase_x_idx", self.phase_x_combo.currentIndex())
        s.setValue("phase_y_idx", self.phase_y_combo.currentIndex())
        s.setValue("fft_enabled", self.fft_enabled_checkbox.isChecked())
        s.setValue("fft_ch_idx", self.fft_channel_combo.currentIndex())
        s.setValue("embed_plot", self.embed_plot_checkbox.isChecked())
        s.setValue("logy", self.logy_checkbox.isChecked())

        # Toolpath files
        s.beginWriteArray("toolpath_files")
        for i, entry in enumerate(self._toolpath_files):
            s.setArrayIndex(i)
            s.setValue("path", str(entry["path"]))
            s.setValue("color", entry["color"])
            s.setValue("enabled", entry["enabled"])
        s.endArray()

        # Toolpath parameters
        s.setValue("tp_x_ch", self._tp_x_spin.value())
        s.setValue("tp_y_ch", self._tp_y_spin.value())
        s.setValue("tp_ds", self._tp_ds_spin.value())
        s.setValue("tp_err_ch", self._tp_err_spin.value())
        s.setValue("tp_err2_ch", self._tp_err2_spin.value())
        s.setValue("tp_mode", self._tp_mode_combo.currentIndex())
        s.setValue("tp_exag", self._tp_exag_spin.value())

    def _load_session(self) -> None:
        s = QtCore.QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)

        geom = s.value("geometry")
        if geom:
            self.restoreGeometry(geom)

        sizes = s.value("splitter_sizes")
        if sizes:
            try:
                self._main_splitter.setSizes([int(v) for v in sizes])
            except Exception:
                pass

        # CSV / JSON import options (restore before loading file)
        self._csv_sep = s.value("csv_sep", ",")
        try:
            self._csv_header_row = int(s.value("csv_header_row", 0))
        except (TypeError, ValueError):
            self._csv_header_row = 0
        self._json_orient = s.value("json_orient", "")

        # Data file
        file_path_str = s.value("current_file", "")
        saved_configs: List[dict] = []
        if file_path_str:
            path = Path(file_path_str)
            if not path.exists():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Session restore — file not found",
                    f"The previously loaded file no longer exists:\n{path}\n\n"
                    "Other settings have been restored.",
                )
            else:
                try:
                    self._load_file(path)
                except Exception as exc:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Session restore — load error",
                        f"Could not reload the previous file:\n{path}\n\n{exc}",
                    )

            # Read saved track configs even if load failed (we apply when table populates)
            n = s.beginReadArray("tracks")
            for i in range(n):
                s.setArrayIndex(i)
                saved_configs.append({
                    "index": int(s.value("index", i + 1)),
                    "enabled": s.value("enabled", True) in (True, "true", "True"),
                    "label": s.value("label", ""),
                    "scale": float(s.value("scale", 1.0)),
                    "offset": float(s.value("offset", 0.0)),
                })
            s.endArray()

            # Apply saved configs to the already-populated table
            if saved_configs:
                self._restore_track_configs(saved_configs)

        # Plot options
        def _bool(key: str, default: bool) -> bool:
            v = s.value(key, default)
            return v in (True, "true", "True") if isinstance(v, (str, bool)) else bool(v)

        self.time_enabled_checkbox.setChecked(_bool("time_enabled", True))
        self.time_merge_checkbox.setChecked(_bool("time_merge", False))
        self.phase_enabled_checkbox.setChecked(_bool("phase_enabled", False))
        self.fft_enabled_checkbox.setChecked(_bool("fft_enabled", False))
        self.embed_plot_checkbox.setChecked(_bool("embed_plot", True))
        self.logy_checkbox.setChecked(_bool("logy", False))

        # Combo indices restored after combos are populated (which happens in _load_file)
        try:
            px = int(s.value("phase_x_idx", 0))
            py = int(s.value("phase_y_idx", 1))
            if self.phase_x_combo.count() > px:
                self.phase_x_combo.setCurrentIndex(px)
            if self.phase_y_combo.count() > py:
                self.phase_y_combo.setCurrentIndex(py)
        except (TypeError, ValueError):
            pass
        try:
            fi = int(s.value("fft_ch_idx", 0))
            if self.fft_channel_combo.count() > fi:
                self.fft_channel_combo.setCurrentIndex(fi)
        except (TypeError, ValueError):
            pass

        # Toolpath files
        n = s.beginReadArray("toolpath_files")
        for i in range(n):
            s.setArrayIndex(i)
            raw = s.value("path", "")
            color = s.value("color", self._TOOLPATH_COLORS[i % len(self._TOOLPATH_COLORS)])
            enabled = s.value("enabled", True) in (True, "true", "True")
            if not raw:
                continue
            tp_path = Path(raw)
            if not tp_path.exists():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Session restore — toolpath file not found",
                    f"Toolpath file no longer exists:\n{tp_path}",
                )
                continue
            if not any(d["path"] == tp_path for d in self._toolpath_files):
                self._toolpath_files.append({"path": tp_path, "color": color, "enabled": enabled})
        s.endArray()
        self._refresh_tp_table()

        # Toolpath parameters
        try:
            self._tp_x_spin.setValue(int(s.value("tp_x_ch", 1)))
            self._tp_y_spin.setValue(int(s.value("tp_y_ch", 2)))
            self._tp_ds_spin.setValue(int(s.value("tp_ds", 10)))
            self._tp_err_spin.setValue(int(s.value("tp_err_ch", 0)))
            self._tp_err2_spin.setValue(int(s.value("tp_err2_ch", 0)))
            self._tp_mode_combo.setCurrentIndex(int(s.value("tp_mode", 0)))
            self._tp_exag_spin.setValue(float(s.value("tp_exag", 100.0)))
        except (TypeError, ValueError):
            pass

    def _restore_track_configs(self, configs: List[dict]) -> None:
        """Apply saved track label/scale/offset/enabled back to the table rows."""
        cfg_by_idx = {c["index"]: c for c in configs}
        for row in range(self.tracks_table.rowCount()):
            idx_item = self.tracks_table.item(row, 0)
            if not idx_item:
                continue
            col_idx = int(idx_item.text())
            cfg = cfg_by_idx.get(col_idx)
            if not cfg:
                continue
            en_item = self.tracks_table.item(row, 1)
            if en_item:
                en_item.setCheckState(
                    QtCore.Qt.CheckState.Checked if cfg["enabled"]
                    else QtCore.Qt.CheckState.Unchecked
                )
            lbl_item = self.tracks_table.item(row, 2)
            if lbl_item and cfg["label"]:
                lbl_item.setText(cfg["label"])
            sc_item = self.tracks_table.item(row, 3)
            if sc_item:
                sc_item.setText(str(cfg["scale"]))
            off_item = self.tracks_table.item(row, 4)
            if off_item:
                off_item.setText(str(cfg["offset"]))

    # ------------------------------------------------------------------ UI setup
    def _setup_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        self._main_layout = QtWidgets.QVBoxLayout(central)
        main_layout = self._main_layout

        # Top: file selection and options
        file_group = QtWidgets.QGroupBox("Data file")
        file_layout = QtWidgets.QHBoxLayout(file_group)

        self.file_edit = QtWidgets.QLineEdit()
        self.file_edit.setReadOnly(True)
        browse_btn = QtWidgets.QPushButton("Browse...")
        browse_btn.clicked.connect(self._on_browse_clicked)

        reload_btn = QtWidgets.QPushButton("Reload")
        reload_btn.clicked.connect(self._on_reload_clicked)

        # CSV / JSON import options (shown only when relevant file is loaded)
        self._csv_options_btn = QtWidgets.QPushButton("Import options…")
        self._csv_options_btn.setEnabled(False)
        self._csv_options_btn.clicked.connect(self._on_import_options_clicked)

        file_layout.addWidget(self.file_edit, 1)
        file_layout.addWidget(browse_btn)
        file_layout.addWidget(reload_btn)
        file_layout.addWidget(self._csv_options_btn)

        main_layout.addWidget(file_group)

        # Three-panel splitter: Tracks | Options | Preview
        self._main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_layout.addWidget(self._main_splitter, 1)
        main_splitter = self._main_splitter

        # Left: table of tracks (channels)
        tracks_group = QtWidgets.QGroupBox("Tracks / Channels")
        tracks_layout = QtWidgets.QVBoxLayout(tracks_group)

        self.tracks_table = QtWidgets.QTableWidget(0, 6)
        self.tracks_table.setHorizontalHeaderLabels(
            ["#", "Enabled", "Label", "Scale", "Offset", "Preview"],
        )
        self.tracks_table.verticalHeader().setVisible(False)
        self.tracks_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.tracks_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.tracks_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.tracks_table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.tracks_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.tracks_table.horizontalHeader().setSectionResizeMode(
            4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.tracks_table.horizontalHeader().setSectionResizeMode(
            5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )

        tracks_layout.addWidget(self.tracks_table)

        main_splitter.addWidget(tracks_group)

        # Middle: options — wrapped in a scroll area so it can shrink freely
        options_group = QtWidgets.QGroupBox("Plot options")
        options_layout = QtWidgets.QVBoxLayout(options_group)

        # Time plot options
        time_box = QtWidgets.QGroupBox("Time-domain plots")
        time_layout = QtWidgets.QVBoxLayout(time_box)
        self.time_enabled_checkbox = QtWidgets.QCheckBox("Generate time plots")
        self.time_enabled_checkbox.setChecked(True)
        self.time_merge_checkbox = QtWidgets.QCheckBox("Merge enabled channels into one plot")
        self.time_merge_checkbox.setChecked(False)
        time_layout.addWidget(self.time_enabled_checkbox)
        time_layout.addWidget(self.time_merge_checkbox)
        options_layout.addWidget(time_box)

        # Phase plot options
        phase_box = QtWidgets.QGroupBox("Phase / Lissajous plot")
        phase_layout = QtWidgets.QFormLayout(phase_box)
        self.phase_enabled_checkbox = QtWidgets.QCheckBox("Generate phase plot")
        self.phase_x_combo = QtWidgets.QComboBox()
        self.phase_y_combo = QtWidgets.QComboBox()
        phase_layout.addRow(self.phase_enabled_checkbox)
        phase_layout.addRow("X channel:", self.phase_x_combo)
        phase_layout.addRow("Y channel:", self.phase_y_combo)
        options_layout.addWidget(phase_box)

        # FFT options
        fft_box = QtWidgets.QGroupBox("Spectrum (FFT)")
        fft_layout = QtWidgets.QFormLayout(fft_box)
        self.fft_enabled_checkbox = QtWidgets.QCheckBox("Generate FFT for channel")
        self.fft_channel_combo = QtWidgets.QComboBox()
        fft_layout.addRow(self.fft_enabled_checkbox)
        fft_layout.addRow("Channel:", self.fft_channel_combo)
        options_layout.addWidget(fft_box)

        # CNC Tool Path
        toolpath_box = QtWidgets.QGroupBox("CNC Tool Path")
        tp_layout = QtWidgets.QVBoxLayout(toolpath_box)

        self._tp_table = QtWidgets.QTableWidget(0, 2)
        self._tp_table.setHorizontalHeaderLabels(["File", "On"])
        self._tp_table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self._tp_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self._tp_table.setMaximumHeight(90)
        self._tp_table.verticalHeader().setVisible(False)
        tp_layout.addWidget(self._tp_table)

        tp_btn_row = QtWidgets.QHBoxLayout()
        tp_add_btn = QtWidgets.QPushButton("Add…")
        tp_add_btn.clicked.connect(self._on_tp_add_clicked)
        tp_remove_btn = QtWidgets.QPushButton("Remove")
        tp_remove_btn.clicked.connect(self._on_tp_remove_clicked)
        tp_btn_row.addWidget(tp_add_btn)
        tp_btn_row.addWidget(tp_remove_btn)
        tp_btn_row.addStretch(1)
        tp_layout.addLayout(tp_btn_row)

        tp_ch_form = QtWidgets.QFormLayout()
        self._tp_x_spin = QtWidgets.QSpinBox()
        self._tp_x_spin.setRange(1, 99)
        self._tp_x_spin.setValue(1)
        self._tp_y_spin = QtWidgets.QSpinBox()
        self._tp_y_spin.setRange(1, 99)
        self._tp_y_spin.setValue(2)
        self._tp_ds_spin = QtWidgets.QSpinBox()
        self._tp_ds_spin.setRange(1, 1000)
        self._tp_ds_spin.setValue(10)
        self._tp_ds_spin.setToolTip("Plot every Nth sample (1 = all, 10 = every 10th)")
        self._tp_err_spin = QtWidgets.QSpinBox()
        self._tp_err_spin.setRange(0, 99)
        self._tp_err_spin.setValue(0)
        self._tp_err_spin.setToolTip("0 = plain path; set to a column number to overlay error data")
        self._tp_err2_spin = QtWidgets.QSpinBox()
        self._tp_err2_spin.setRange(0, 99)
        self._tp_err2_spin.setValue(0)
        self._tp_err2_spin.setToolTip("Second error component for arrow mode (Y-error). 0 = use Error ch1 as magnitude only")
        self._tp_mode_combo = QtWidgets.QComboBox()
        self._tp_mode_combo.addItems(["Plain path", "Color by error", "Deviation arrows", "Polar deviation"])
        self._tp_exag_spin = QtWidgets.QDoubleSpinBox()
        self._tp_exag_spin.setRange(1.0, 10000.0)
        self._tp_exag_spin.setValue(100.0)
        self._tp_exag_spin.setToolTip("Exaggeration factor for polar deviation mode")
        tp_ch_form.addRow("X channel:", self._tp_x_spin)
        tp_ch_form.addRow("Y channel:", self._tp_y_spin)
        tp_ch_form.addRow("Downsample (every N):", self._tp_ds_spin)
        tp_ch_form.addRow("Error ch 1:", self._tp_err_spin)
        tp_ch_form.addRow("Error ch 2 (Y arrow):", self._tp_err2_spin)
        tp_ch_form.addRow("Display mode:", self._tp_mode_combo)
        tp_ch_form.addRow("Exaggeration ×:", self._tp_exag_spin)
        tp_layout.addLayout(tp_ch_form)

        tp_plot_btn = QtWidgets.QPushButton("Plot Tool Path")
        tp_plot_btn.clicked.connect(self._on_tp_plot_clicked)
        tp_layout.addWidget(tp_plot_btn)

        options_layout.addWidget(toolpath_box)

        # Plot target
        target_box = QtWidgets.QGroupBox("Output")
        target_layout = QtWidgets.QVBoxLayout(target_box)
        self.embed_plot_checkbox = QtWidgets.QCheckBox("Show plots inside this window")
        self.embed_plot_checkbox.setChecked(True)
        target_layout.addWidget(self.embed_plot_checkbox)
        self.logy_checkbox = QtWidgets.QCheckBox("Log scale (Y axis)")
        self.logy_checkbox.setChecked(False)
        target_layout.addWidget(self.logy_checkbox)

        self.detach_button = QtWidgets.QPushButton("Detach preview")
        self.detach_button.setCheckable(True)
        self.detach_button.toggled.connect(self._on_detach_toggled)
        target_layout.addWidget(self.detach_button)
        options_layout.addWidget(target_box)

        options_layout.addStretch(1)

        # Action buttons
        btn_layout = QtWidgets.QHBoxLayout()
        stats_btn = QtWidgets.QPushButton("Show stats")
        stats_btn.clicked.connect(self._on_show_stats_clicked)
        generate_btn = QtWidgets.QPushButton("Generate Plots")
        generate_btn.setObjectName("_generate_btn")
        generate_btn.clicked.connect(self._on_generate_clicked)
        btn_layout.addStretch(1)
        btn_layout.addWidget(stats_btn)
        btn_layout.addWidget(generate_btn)
        options_layout.addLayout(btn_layout)

        options_scroll = QtWidgets.QScrollArea()
        options_scroll.setWidgetResizable(True)
        options_scroll.setWidget(options_group)
        options_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        main_splitter.addWidget(options_scroll)

        # Right: embedded matplotlib canvas area (tabbed)
        self.canvas_group = QtWidgets.QGroupBox("Plot preview")
        canvas_layout = QtWidgets.QVBoxLayout(self.canvas_group)
        self.plot_tabs = QtWidgets.QTabWidget(self.canvas_group)
        canvas_layout.addWidget(self.plot_tabs)
        main_splitter.addWidget(self.canvas_group)

        main_splitter.setStretchFactor(0, 2)   # tracks
        main_splitter.setStretchFactor(1, 1)   # options
        main_splitter.setStretchFactor(2, 4)   # preview

    # ---------------------------------------------------------------- palette / style
    def _apply_windows98_palette(self) -> None:
        """Full Windows 98 look: beveled widgets, classic grey, system font."""
        import tempfile, os
        app = QtWidgets.QApplication.instance()
        if not app:
            return

        # Write a tiny checkmark SVG so Qt can reference it by file path.
        # (Qt stylesheets don't reliably support inline data: URIs for images.)
        checkmark_svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='11' height='11'>"
            "<polyline points='1,5 4,9 10,1' "
            "style='fill:none;stroke:#000000;stroke-width:2;stroke-linecap:square'/>"
            "</svg>"
        )
        tmp_dir = tempfile.gettempdir()
        check_path = os.path.join(tmp_dir, "win98_check.svg").replace("\\", "/")
        with open(check_path, "w") as f:
            f.write(checkmark_svg)

        # Use Windows style if available (gives native Win controls on Windows),
        # otherwise fall back to Fusion which we can fully override via stylesheet.
        available = QtWidgets.QStyleFactory.keys()
        if "Windows" in available:
            app.setStyle("Windows")
        else:
            app.setStyle("Fusion")

        # --- Palette (used by widgets that don't read the stylesheet) ---
        pal = QtGui.QPalette()
        grey      = QtGui.QColor(192, 192, 192)   # classic Win98 face colour
        white     = QtGui.QColor(255, 255, 255)
        black     = QtGui.QColor(0,   0,   0)
        dark      = QtGui.QColor(128, 128, 128)   # dark shadow
        darker    = QtGui.QColor(64,  64,  64)
        highlight = QtGui.QColor(0,   0,   128)   # navy selection
        light     = QtGui.QColor(223, 223, 223)   # light highlight edge
        tooltip   = QtGui.QColor(255, 255, 220)

        pal.setColor(QtGui.QPalette.ColorRole.Window,          grey)
        pal.setColor(QtGui.QPalette.ColorRole.WindowText,      black)
        pal.setColor(QtGui.QPalette.ColorRole.Base,            white)
        pal.setColor(QtGui.QPalette.ColorRole.AlternateBase,   QtGui.QColor(240, 240, 240))
        pal.setColor(QtGui.QPalette.ColorRole.Text,            black)
        pal.setColor(QtGui.QPalette.ColorRole.Button,          grey)
        pal.setColor(QtGui.QPalette.ColorRole.ButtonText,      black)
        pal.setColor(QtGui.QPalette.ColorRole.Highlight,       highlight)
        pal.setColor(QtGui.QPalette.ColorRole.HighlightedText, white)
        pal.setColor(QtGui.QPalette.ColorRole.ToolTipBase,     tooltip)
        pal.setColor(QtGui.QPalette.ColorRole.ToolTipText,     black)
        pal.setColor(QtGui.QPalette.ColorRole.Light,           white)
        pal.setColor(QtGui.QPalette.ColorRole.Midlight,        light)
        pal.setColor(QtGui.QPalette.ColorRole.Mid,             dark)
        pal.setColor(QtGui.QPalette.ColorRole.Dark,            darker)
        pal.setColor(QtGui.QPalette.ColorRole.Shadow,          black)

        # Disabled colours
        pal.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.WindowText, dark)
        pal.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.ButtonText, dark)
        pal.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text,       dark)

        app.setPalette(pal)

        # --- Stylesheet ---
        # Pixels and borders are tuned to match the real Win98 look:
        #   • Raised buttons: white top-left, dark bottom-right bevel
        #   • Pressed buttons: sunken (borders flip)
        #   • GroupBox: etched border with title
        #   • QLineEdit / QTableWidget: sunken inset
        #   • QTabBar: Win98-style tab (selected tab is raised higher)
        #   • Title font: MS Sans Serif / Tahoma 8 pt (Win98 system font)
        stylesheet = """
            /* ── Global ── */
            * {
                font-family: "Tahoma", "MS Sans Serif", "Arial", sans-serif;
                font-size: 9pt;
                color: #000000;
            }

            /* ── Labels and checkboxes: crisp black, normal weight ── */
            QLabel {
                font-size: 9pt;
                color: #000000;
                background-color: transparent;
            }
            QCheckBox {
                font-size: 9pt;
                color: #000000;
                font-weight: normal;
            }
            QGroupBox {
                font-size: 9pt;
                font-weight: bold;
                color: #000000;
            }
            QGroupBox::title {
                font-size: 9pt;
                font-weight: bold;
                color: #000000;
            }

            QMainWindow, QDialog {
                background-color: #c0c0c0;
            }

            QWidget {
                background-color: #c0c0c0;
            }

            /* ── Buttons ── */
            QPushButton {
                background-color: #c0c0c0;
                border-top:    2px solid #ffffff;
                border-left:   2px solid #ffffff;
                border-right:  2px solid #404040;
                border-bottom: 2px solid #404040;
                padding: 3px 10px;
                min-width: 60px;
                min-height: 20px;
            }
            QPushButton:hover {
                background-color: #d0d0d0;
            }
            QPushButton:pressed {
                border-top:    2px solid #404040;
                border-left:   2px solid #404040;
                border-right:  2px solid #ffffff;
                border-bottom: 2px solid #ffffff;
                padding: 4px 9px 2px 11px;
            }
            QPushButton:disabled {
                color: #808080;
                border-top:    2px solid #ffffff;
                border-left:   2px solid #ffffff;
                border-right:  2px solid #404040;
                border-bottom: 2px solid #404040;
            }
            QPushButton:checked {
                border-top:    2px solid #404040;
                border-left:   2px solid #404040;
                border-right:  2px solid #ffffff;
                border-bottom: 2px solid #ffffff;
                background-color: #b0b0b0;
            }

            /* ── GroupBox border (title defined above in global block) ── */
            QGroupBox {
                border-top:    2px solid #808080;
                border-left:   2px solid #808080;
                border-right:  2px solid #ffffff;
                border-bottom: 2px solid #ffffff;
                margin-top: 12px;
                padding-top: 8px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 8px;
                top: 1px;
                background-color: #c0c0c0;
                padding: 0 3px;
            }

            /* ── Text inputs ── */
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #ffffff;
                border-top:    2px solid #808080;
                border-left:   2px solid #808080;
                border-right:  2px solid #dfdfdf;
                border-bottom: 2px solid #dfdfdf;
                padding: 1px 3px;
                selection-background-color: #000080;
                selection-color: #ffffff;
            }
            QLineEdit:read-only {
                background-color: #c0c0c0;
            }
            QComboBox::drop-down {
                width: 16px;
                border-left: 1px solid #808080;
                background-color: #c0c0c0;
            }
            QComboBox::down-arrow {
                width: 7px;
                height: 5px;
                border-top:   4px solid #000000;
                border-left:  4px solid transparent;
                border-right: 4px solid transparent;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                selection-background-color: #000080;
                selection-color: #ffffff;
                border: 1px solid #000000;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #c0c0c0;
                border-top:    1px solid #ffffff;
                border-left:   1px solid #ffffff;
                border-right:  1px solid #404040;
                border-bottom: 1px solid #404040;
                width: 16px;
                height: 10px;
            }
            QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {
                border-top:    1px solid #404040;
                border-left:   1px solid #404040;
                border-right:  1px solid #ffffff;
                border-bottom: 1px solid #ffffff;
            }
            QSpinBox::up-arrow {
                width: 0;
                height: 0;
                border-left:   4px solid transparent;
                border-right:  4px solid transparent;
                border-bottom: 5px solid #000000;
            }
            QSpinBox::down-arrow {
                width: 0;
                height: 0;
                border-left:   4px solid transparent;
                border-right:  4px solid transparent;
                border-top:    5px solid #000000;
            }

            /* ── Checkboxes ── */
            QCheckBox {
                spacing: 6px;
                background-color: transparent;
            }
            QCheckBox::indicator {
                width: 13px;
                height: 13px;
                background-color: #ffffff;
                border-top:    2px solid #808080;
                border-left:   2px solid #808080;
                border-right:  2px solid #dfdfdf;
                border-bottom: 2px solid #dfdfdf;
            }
            QCheckBox::indicator:checked {
                background-color: #ffffff;
                border-top:    2px solid #808080;
                border-left:   2px solid #808080;
                border-right:  2px solid #dfdfdf;
                border-bottom: 2px solid #dfdfdf;
                image: url(CHECK_PATH_PLACEHOLDER);
            }
            QCheckBox::indicator:unchecked {
                background-color: #ffffff;
                border-top:    2px solid #808080;
                border-left:   2px solid #808080;
                border-right:  2px solid #dfdfdf;
                border-bottom: 2px solid #dfdfdf;
                image: none;
            }

            /* ── Table ── */
            QTableWidget {
                background-color: #ffffff;
                gridline-color: #c0c0c0;
                border-top:    2px solid #808080;
                border-left:   2px solid #808080;
                border-right:  2px solid #dfdfdf;
                border-bottom: 2px solid #dfdfdf;
                selection-background-color: #000080;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #c0c0c0;
                border-top:    1px solid #ffffff;
                border-left:   1px solid #ffffff;
                border-right:  1px solid #404040;
                border-bottom: 1px solid #404040;
                padding: 2px 4px;
                font-weight: bold;
            }

            /* ── Tabs ── */
            QTabWidget::pane {
                border-top:    2px solid #808080;
                border-left:   2px solid #808080;
                border-right:  2px solid #ffffff;
                border-bottom: 2px solid #ffffff;
                background-color: #c0c0c0;
            }
            QTabBar::tab {
                background-color: #c0c0c0;
                border-top:    2px solid #ffffff;
                border-left:   2px solid #ffffff;
                border-right:  2px solid #808080;
                border-bottom: none;
                padding: 3px 10px;
                margin-right: 2px;
                margin-bottom: -1px;
            }
            QTabBar::tab:selected {
                background-color: #c0c0c0;
                border-top:    2px solid #ffffff;
                border-left:   2px solid #ffffff;
                border-right:  2px solid #808080;
                border-bottom: 2px solid #c0c0c0;
                font-weight: bold;
                padding-bottom: 4px;
            }
            QTabBar::tab:!selected {
                margin-top: 2px;
                background-color: #b0b0b0;
            }

            /* ── Scrollbars ── */
            QScrollBar:vertical {
                background: #c0c0c0;
                width: 16px;
                border: 1px solid #808080;
            }
            QScrollBar::handle:vertical {
                background: #c0c0c0;
                border-top:    1px solid #ffffff;
                border-left:   1px solid #ffffff;
                border-right:  1px solid #404040;
                border-bottom: 1px solid #404040;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background: #c0c0c0;
                height: 16px;
                border-top:    1px solid #ffffff;
                border-left:   1px solid #ffffff;
                border-right:  1px solid #404040;
                border-bottom: 1px solid #404040;
            }
            QScrollBar:horizontal {
                background: #c0c0c0;
                height: 16px;
                border: 1px solid #808080;
            }
            QScrollBar::handle:horizontal {
                background: #c0c0c0;
                border-top:    1px solid #ffffff;
                border-left:   1px solid #ffffff;
                border-right:  1px solid #404040;
                border-bottom: 1px solid #404040;
                min-width: 20px;
            }

            /* ── Splitter ── */
            QSplitter::handle {
                background-color: #c0c0c0;
                width: 4px;
            }

            /* ── Menu ── */
            QMenuBar {
                background-color: #c0c0c0;
            }
            QMenuBar::item:selected {
                background-color: #000080;
                color: #ffffff;
            }
            QMenu {
                background-color: #c0c0c0;
                border: 1px solid #000000;
            }
            QMenu::item:selected {
                background-color: #000080;
                color: #ffffff;
            }

            /* ── ToolTip ── */
            QToolTip {
                background-color: #ffffe0;
                border: 1px solid #000000;
                color: #000000;
                padding: 2px;
            }

            /* ── MessageBox ── */
            QMessageBox {
                background-color: #c0c0c0;
            }
        """
        app.setStyleSheet(stylesheet.replace("CHECK_PATH_PLACEHOLDER", check_path))

    # ---------------------------------------------------------------- helpers
    def _clear_plot_tabs(self) -> None:
        self._tab_canvases.clear()
        while self.plot_tabs.count():
            widget = self.plot_tabs.widget(0)
            self.plot_tabs.removeTab(0)
            if widget is not None:
                widget.deleteLater()

    def _file_suffix(self) -> str:
        """Return the lowercase extension of the current file, e.g. '.csv'."""
        if self.current_file is None:
            return ""
        return self.current_file.suffix.lower()

    # ---------------------------------------------------------------- import options dialog
    def _on_import_options_clicked(self) -> None:
        """Open a small dialog to configure CSV / JSON import parameters."""
        suffix = self._file_suffix()
        if suffix == ".csv":
            self._show_csv_options_dialog()
        elif suffix == ".json":
            self._show_json_options_dialog()

    def _show_csv_options_dialog(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("CSV import options")
        layout = QtWidgets.QFormLayout(dlg)

        sep_edit = QtWidgets.QLineEdit(getattr(self, "_csv_sep", ","))
        header_spin = QtWidgets.QSpinBox()
        header_spin.setRange(-1, 100)
        header_spin.setValue(getattr(self, "_csv_header_row", 0))
        header_spin.setSpecialValueText("none")  # -1 → no header

        skip_spin = QtWidgets.QSpinBox()
        skip_spin.setRange(0, 1000)
        skip_spin.setValue(getattr(self, "_csv_skiprows", 0))

        decimal_edit = QtWidgets.QLineEdit(getattr(self, "_csv_decimal", "."))
        decimal_edit.setMaxLength(1)

        layout.addRow("Delimiter:", sep_edit)
        layout.addRow("Header row (0-based, -1 = none):", header_spin)
        layout.addRow("Skip rows at top:", skip_spin)
        layout.addRow("Decimal separator:", decimal_edit)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addRow(btns)

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._csv_sep = sep_edit.text() or ","
            self._csv_header_row = header_spin.value()
            self._csv_skiprows = skip_spin.value()
            self._csv_decimal = decimal_edit.text() or "."
            # Reload so the new options take effect immediately
            if self.current_file:
                self._load_file(self.current_file)

    def _show_json_options_dialog(self) -> None:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("JSON import options")
        layout = QtWidgets.QFormLayout(dlg)

        orient_combo = QtWidgets.QComboBox()
        # pandas read_json orient values that make sense for oscilloscope data
        for o in ("columns", "records", "index", "split", "values"):
            orient_combo.addItem(o)
        current_orient = getattr(self, "_json_orient", "columns")
        idx = orient_combo.findText(current_orient)
        if idx >= 0:
            orient_combo.setCurrentIndex(idx)

        layout.addRow("Orient:", orient_combo)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addRow(btns)

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._json_orient = orient_combo.currentText()
            if self.current_file:
                self._load_file(self.current_file)

    # ---------------------------------------------------------------- detach / reattach
    def _on_detach_toggled(self, checked: bool) -> None:
        if checked:
            self.detach_button.setText("Reattach preview")
            self._detach_preview()
        else:
            self.detach_button.setText("Detach preview")
            self._reattach_preview()

    def _detach_preview(self) -> None:
        if self._preview_window:
            return

        # Store splitter sizes so we can restore them on reattach
        self._splitter_sizes_before_detach = self._main_splitter.sizes()

        self._preview_window = QtWidgets.QMainWindow(self)
        self._preview_window.setWindowTitle("Plot preview")

        def handle_close(event: QtGui.QCloseEvent) -> None:
            if self.detach_button.isChecked():
                self.detach_button.setChecked(False)
            event.accept()

        self._preview_window.closeEvent = handle_close  # pyright: ignore[reportAttributeAccessIssue]

        self.canvas_group.setParent(self._preview_window)
        self._preview_window.setCentralWidget(self.canvas_group)
        self._preview_window.resize(900, 600)
        self._preview_window.show()

    def _reattach_preview(self) -> None:
        if not self._main_layout:
            return

        if self._preview_window is not None:
            self._preview_window.takeCentralWidget()
            self._preview_window.close()
            self._preview_window = None

        # Put canvas_group back as the third panel of the splitter
        self._main_splitter.insertWidget(2, self.canvas_group)
        self._main_splitter.setStretchFactor(2, 4)
        # Restore sizes if we saved them, otherwise let Qt decide
        if hasattr(self, "_splitter_sizes_before_detach"):
            sizes = self._splitter_sizes_before_detach
            # Only restore if total is still reasonable
            if len(sizes) == 3:
                self._main_splitter.setSizes(sizes)

    # ---------------------------------------------------------------- file loading
    def _build_load_kwargs(self, path: Path) -> dict:
        """Return format-specific kwargs to pass to OscilloscopePlotter.load_data."""
        suffix = path.suffix.lower()
        if suffix == ".csv":
            header_val = getattr(self, "_csv_header_row", 0)
            return {
                "fmt": "csv",
                "sep": getattr(self, "_csv_sep", ","),
                # pandas uses None to mean "no header"
                "header": None if header_val < 0 else header_val,
                "skiprows": getattr(self, "_csv_skiprows", 0),
                "decimal": getattr(self, "_csv_decimal", "."),
            }
        if suffix == ".json":
            return {
                "fmt": "json",
                "orient": getattr(self, "_json_orient", "columns"),
            }
        # .dat / .txt — existing behaviour, no extra kwargs
        return {"fmt": "dat"}

    def _load_file(self, path: Path) -> None:
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "File not found", f"{path} does not exist.")
            return

        kwargs = self._build_load_kwargs(path)
        if not self.plotter.load_data(path, **kwargs):
            QtWidgets.QMessageBox.critical(
                self,
                "Load error",
                "Failed to load data from file.\n\n"
                "Tip: for CSV files use 'Import options…' to set the correct\n"
                "delimiter, header row, or decimal separator.",
            )
            return

        self.current_file = path
        self.file_edit.setText(str(path))

        # Enable import-options button only for formats that have configurable options
        suffix = path.suffix.lower()
        self._csv_options_btn.setEnabled(suffix in (".csv", ".json"))

        self._populate_tracks_table()
        self._populate_phase_combos()
        self._populate_fft_combo()

    # ---------------------------------------------------------------- table helpers
    def _populate_tracks_table(self) -> None:
        self.tracks_table.setRowCount(0)
        self.track_rows.clear()

        if self.plotter.data is None:
            return

        num_columns = self.plotter.data.shape[1]
        for col_idx in range(1, num_columns + 1):
            row = self.tracks_table.rowCount()
            self.tracks_table.insertRow(row)

            idx_item = QtWidgets.QTableWidgetItem(str(col_idx))
            idx_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            self.tracks_table.setItem(row, 0, idx_item)

            enabled_item = QtWidgets.QTableWidgetItem()
            enabled_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsUserCheckable
                | QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            enabled_item.setCheckState(QtCore.Qt.CheckState.Checked)
            self.tracks_table.setItem(row, 1, enabled_item)

            default_label = self.plotter.column_names[col_idx - 1]
            label_item = QtWidgets.QTableWidgetItem(default_label)
            self.tracks_table.setItem(row, 2, label_item)

            scale_item = QtWidgets.QTableWidgetItem("1.0")
            self.tracks_table.setItem(row, 3, scale_item)

            offset_item = QtWidgets.QTableWidgetItem("0.0")
            self.tracks_table.setItem(row, 4, offset_item)

            preview_item = QtWidgets.QTableWidgetItem("time plot")
            preview_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            self.tracks_table.setItem(row, 5, preview_item)

            self.track_rows.append(
                TrackConfig(index=col_idx, enabled=True, label=default_label, scale=1.0, offset=0.0),
            )

    def _collect_track_config(self) -> List[TrackConfig]:
        configs: List[TrackConfig] = []
        for row in range(self.tracks_table.rowCount()):
            idx_item = self.tracks_table.item(row, 0)
            enabled_item = self.tracks_table.item(row, 1)
            label_item = self.tracks_table.item(row, 2)
            scale_item = self.tracks_table.item(row, 3)
            offset_item = self.tracks_table.item(row, 4)

            if not idx_item or not enabled_item or not label_item:
                continue

            index = int(idx_item.text())
            enabled = enabled_item.checkState() == QtCore.Qt.CheckState.Checked
            label = label_item.text().strip() or f"Channel_{index}"

            def parse_float(item: Optional[QtWidgets.QTableWidgetItem], default: float) -> float:
                if item is None:
                    return default
                text = item.text().strip()
                if not text:
                    return default
                try:
                    return float(text)
                except ValueError:
                    return default

            scale = parse_float(scale_item, 1.0)
            offset = parse_float(offset_item, 0.0)

            configs.append(
                TrackConfig(index=index, enabled=enabled, label=label, scale=scale, offset=offset),
            )
        return configs

    def _populate_phase_combos(self) -> None:
        self.phase_x_combo.clear()
        self.phase_y_combo.clear()

        if self.plotter.data is None:
            return

        num_columns = self.plotter.data.shape[1]
        for col_idx in range(1, num_columns + 1):
            name = self.plotter.column_names[col_idx - 1]
            label = f"{col_idx}: {name}"
            self.phase_x_combo.addItem(label, col_idx)
            self.phase_y_combo.addItem(label, col_idx)

        if num_columns >= 2:
            self.phase_x_combo.setCurrentIndex(0)
            self.phase_y_combo.setCurrentIndex(1)

    def _populate_fft_combo(self) -> None:
        self.fft_channel_combo.clear()

        if self.plotter.data is None:
            return

        num_columns = self.plotter.data.shape[1]
        for col_idx in range(1, num_columns + 1):
            name = self.plotter.column_names[col_idx - 1]
            label = f"{col_idx}: {name}"
            self.fft_channel_combo.addItem(label, col_idx)

    # ---------------------------------------------------------------- helpers
    def _plots_dir(self) -> Optional[Path]:
        """Return (and create) a 'plots' subfolder next to the current file."""
        if self.current_file is None:
            return None
        plots_dir = self.current_file.parent / "plots"
        plots_dir.mkdir(exist_ok=True)
        return plots_dir

    # ---------------------------------------------------------------- slots
    def _on_browse_clicked(self) -> None:
        dialog = QtWidgets.QFileDialog(self, "Select data file")
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFile)
        # Build filter list from _SUPPORTED_FORMATS
        filters = [label for label, _ in self._SUPPORTED_FORMATS]
        dialog.setNameFilters(filters)
        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                self._load_file(Path(files[0]))

    def _on_reload_clicked(self) -> None:
        if self.current_file:
            self._load_file(self.current_file)

    def _on_show_stats_clicked(self) -> None:
        if self.plotter.data is None:
            QtWidgets.QMessageBox.information(self, "No data", "Load a data file first.")
            return

        configs = self._collect_track_config()
        enabled = [c for c in configs if c.enabled]
        if not enabled:
            QtWidgets.QMessageBox.information(
                self, "No channels", "Enable at least one channel to compute statistics."
            )
            return

        lines: List[str] = []
        for cfg in enabled:
            col = cfg.index - 1
            if col < 0 or col >= self.plotter.data.shape[1]:
                continue
            y_raw = self.plotter.data[:, col]
            y = cfg.scale * y_raw + cfg.offset
            y = y[~np.isnan(y)]
            if y.size == 0:
                continue
            lines.append(
                f"{cfg.label}: min={y.min():.3g}, max={y.max():.3g}, "
                f"mean={y.mean():.3g}, rms={np.sqrt(np.mean(y**2)):.3g}"
            )

        msg = "\n".join(lines) if lines else "No numeric data available for the selected channels."
        QtWidgets.QMessageBox.information(self, "Channel statistics", msg)

    def _on_generate_clicked(self) -> None:
        if self.plotter.data is None:
            QtWidgets.QMessageBox.information(self, "No data", "Load a data file first.")
            return

        configs = self._collect_track_config()
        enabled_tracks = [c for c in configs if c.enabled]
        if not enabled_tracks and not self.phase_enabled_checkbox.isChecked():
            QtWidgets.QMessageBox.information(
                self, "Nothing to plot", "Enable at least one track or a phase plot."
            )
            return

        self.plotter.column_labels.clear()
        self.plotter.selected_columns = [c.index for c in enabled_tracks]
        self._scaling = {c.index: (c.scale, c.offset) for c in configs}
        for cfg in configs:
            self.plotter.column_labels[cfg.index] = cfg.label

        if self.embed_plot_checkbox.isChecked():
            self._render_embedded_plots(
                do_time=self.time_enabled_checkbox.isChecked(),
                do_phase=self.phase_enabled_checkbox.isChecked(),
                do_fft=self.fft_enabled_checkbox.isChecked(),
            )
        else:
            # PNG-only mode: run in background thread
            plots_dir = self._plots_dir()
            do_time = self.time_enabled_checkbox.isChecked()
            do_phase = self.phase_enabled_checkbox.isChecked()
            x_col = self.phase_x_combo.currentData()
            y_col = self.phase_y_combo.currentData()
            plotter = self.plotter
            stem = self.current_file.stem if self.current_file else "output"

            tasks: List[Callable[[], Tuple[Optional[Figure], str]]] = []

            if do_time and enabled_tracks:
                time_output = str(plots_dir / f"{stem}_time.png") if plots_dir else ""
                def _save_time(p=plotter, o=time_output):
                    p.plot_data(save_plot=bool(o), output_path=o)
                    return None, ""
                tasks.append(_save_time)

            if do_phase and x_col and y_col:
                phase_output = str(plots_dir / f"{stem}_phase.png") if plots_dir else ""
                def _save_phase(p=plotter, xc=int(x_col), yc=int(y_col), o=phase_output):
                    p.plot_phase(x_col=xc, y_col=yc, save_plot=bool(o), output_path=o)
                    return None, ""
                tasks.append(_save_phase)

            if tasks:
                self._start_worker(tasks)

    # -------------------------------------------------------- worker helpers
    def _start_worker(
        self, tasks: List[Callable[[], Tuple[Optional[Figure], str]]]
    ) -> None:
        """Launch a PlotWorker for *tasks*, disabling the Generate button while running."""
        # Cancel any previous worker still running
        if self._plot_worker and self._plot_worker.isRunning():
            self._plot_worker.quit()
            self._plot_worker.wait(500)

        # Disable generate button so the user doesn't double-click
        sender = self.sender()
        generate_btn = self.findChild(QtWidgets.QPushButton, "_generate_btn")
        if generate_btn:
            generate_btn.setEnabled(False)

        worker = PlotWorker(tasks, parent=self)
        self._plot_worker = worker

        worker.figure_ready.connect(self._on_figure_ready)
        worker.error.connect(lambda msg: QtWidgets.QMessageBox.warning(
            self, "Plot error", msg
        ))
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def _on_figure_ready(self, fig: Optional[Figure], name: str) -> None:
        """Called on the main thread when one figure is ready; attach it as a tab."""
        if fig is None:
            return  # PNG-save task — nothing to attach
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        canvas = MplCanvas(tab, fig=fig)
        toolbar = NavigationToolbar(canvas, tab)
        layout.addWidget(toolbar)
        layout.addWidget(canvas)
        canvas.draw()
        self.plot_tabs.addTab(tab, name)
        self._tab_canvases.append(canvas)
        # Save PNG in background
        if self.current_file:
            plots_dir = self._plots_dir()
            if plots_dir:
                idx = self.plot_tabs.count()
                output_path = plots_dir / f"{self.current_file.stem}_gui_{idx}.png"
                QtCore.QThreadPool.globalInstance().start(
                    _SaveFigureRunnable(fig, output_path)
                )

    def _on_worker_finished(self) -> None:
        generate_btn = self.findChild(QtWidgets.QPushButton, "_generate_btn")
        if generate_btn:
            generate_btn.setEnabled(True)
        if self.plot_tabs.count() == 0:
            self.plot_tabs.addTab(QtWidgets.QWidget(), "No plots")

    # -------------------------------------------------------- embedded plotting
    def _render_embedded_plots(self, do_time: bool, do_phase: bool, do_fft: bool) -> None:
        self._clear_plot_tabs()

        tasks: List[Callable[[], Tuple[Optional[Figure], str]]] = []
        data = self.plotter.data
        scaling = self._scaling
        labels = self.plotter.column_labels
        col_names = self.plotter.column_names
        logy = self.logy_checkbox.isChecked()

        if do_time and self.plotter.selected_columns:
            if self.time_merge_checkbox.isChecked():
                cols = list(self.plotter.selected_columns)

                def _make_merged(
                    _cols=cols, _data=data, _sc=scaling, _lb=labels, _log=logy
                ) -> Tuple[Figure, str]:
                    fig = Figure(figsize=(6, 4), tight_layout=True)
                    ax = fig.add_subplot(111)
                    x_data = range(len(_data))
                    for ci in _cols:
                        sc, off = _sc.get(ci, (1.0, 0.0))
                        y = sc * _data[:, ci - 1] + off
                        ax.plot(x_data, y, linewidth=1.0, label=_lb.get(ci, f"Ch {ci}"))
                    ax.set_xlabel("Sample Number / Time")
                    ax.set_ylabel("Amplitude")
                    ax.set_title("Merged channels")
                    ax.grid(True, alpha=0.3)
                    ax.legend()
                    if _log:
                        ax.set_yscale("log")
                    return fig, "Time (merged)"

                tasks.append(_make_merged)
            else:
                for col_idx in self.plotter.selected_columns:
                    lbl = labels.get(col_idx, f"Channel {col_idx}")

                    def _make_time(
                        _ci=col_idx, _data=data, _sc=scaling, _lb=lbl, _log=logy
                    ) -> Tuple[Figure, str]:
                        fig = Figure(figsize=(6, 4), tight_layout=True)
                        ax = fig.add_subplot(111)
                        sc, off = _sc.get(_ci, (1.0, 0.0))
                        y = sc * _data[:, _ci - 1] + off
                        ax.plot(range(len(_data)), y, linewidth=1.0, label=_lb)
                        ax.set_xlabel("Sample Number / Time")
                        ax.set_ylabel("Amplitude")
                        ax.set_title(_lb)
                        ax.grid(True, alpha=0.3)
                        ax.legend()
                        if _log:
                            ax.set_yscale("log")
                        return fig, _lb

                    tasks.append(_make_time)

        if do_phase:
            x_col = self.phase_x_combo.currentData()
            y_col = self.phase_y_combo.currentData()
            if x_col and y_col:
                xc, yc = int(x_col), int(y_col)
                x_lbl = labels.get(xc, col_names[xc - 1])
                y_lbl = labels.get(yc, col_names[yc - 1])

                def _make_phase(
                    _data=data, _xc=xc, _yc=yc, _xl=x_lbl, _yl=y_lbl
                ) -> Tuple[Figure, str]:
                    fig = Figure(figsize=(6, 4), tight_layout=True)
                    ax = fig.add_subplot(111)
                    ax.plot(_data[:, _xc - 1], _data[:, _yc - 1], linewidth=1.0)
                    ax.set_xlabel(_xl)
                    ax.set_ylabel(_yl)
                    ax.set_title(f"Phase Plot: {_yl} vs {_xl}")
                    ax.grid(True, alpha=0.3)
                    return fig, "Phase"

                tasks.append(_make_phase)

        if do_fft:
            ch = self.fft_channel_combo.currentData()
            if ch:
                ch = int(ch)
                ch_lbl = labels.get(ch, col_names[ch - 1])
                sc, off = scaling.get(ch, (1.0, 0.0))

                def _make_fft(
                    _data=data, _ch=ch, _sc=sc, _off=off, _lbl=ch_lbl
                ) -> Tuple[Figure, str]:
                    fig = Figure(figsize=(6, 4), tight_layout=True)
                    ax = fig.add_subplot(111)
                    y = _sc * _data[:, _ch - 1] + _off
                    n = len(y)
                    if n > 0:
                        y = y - np.mean(y)
                        freqs = np.fft.rfftfreq(n, d=1.0)
                        spectrum = np.abs(np.fft.rfft(y))
                        ax.plot(freqs, spectrum)
                    ax.set_xlabel("Frequency (1 / sample)")
                    ax.set_ylabel("Amplitude")
                    ax.set_title(f"FFT: {_lbl}")
                    ax.grid(True, alpha=0.3)
                    return fig, f"FFT"

                tasks.append(_make_fft)

        if tasks:
            self._start_worker(tasks)
        else:
            self.plot_tabs.addTab(QtWidgets.QWidget(), "No plots")

    # -------------------------------------------------------- CNC Tool Path
    def _on_tp_add_clicked(self) -> None:
        dialog = QtWidgets.QFileDialog(self, "Add tool-path file(s)")
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFiles)
        filters = [label for label, _ in self._SUPPORTED_FORMATS]
        dialog.setNameFilters(filters)
        if not dialog.exec():
            return
        for f in dialog.selectedFiles():
            path = Path(f)
            if any(d["path"] == path for d in self._toolpath_files):
                continue  # already loaded
            color = self._TOOLPATH_COLORS[
                len(self._toolpath_files) % len(self._TOOLPATH_COLORS)
            ]
            self._toolpath_files.append({"path": path, "color": color, "enabled": True})
        self._refresh_tp_table()

    def _on_tp_remove_clicked(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._tp_table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            if 0 <= row < len(self._toolpath_files):
                del self._toolpath_files[row]
        self._refresh_tp_table()

    def _refresh_tp_table(self) -> None:
        self._tp_table.setRowCount(0)
        for entry in self._toolpath_files:
            row = self._tp_table.rowCount()
            self._tp_table.insertRow(row)

            name_item = QtWidgets.QTableWidgetItem(entry["path"].name)
            name_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            name_item.setBackground(QtGui.QColor(entry["color"]))
            name_item.setForeground(QtGui.QColor("#ffffff"))
            self._tp_table.setItem(row, 0, name_item)

            enabled_item = QtWidgets.QTableWidgetItem()
            enabled_item.setFlags(
                QtCore.Qt.ItemFlag.ItemIsUserCheckable
                | QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            enabled_item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if entry["enabled"]
                else QtCore.Qt.CheckState.Unchecked
            )
            self._tp_table.setItem(row, 1, enabled_item)

    def _on_tp_plot_clicked(self) -> None:
        # Sync enabled flags from table checkboxes
        for row in range(self._tp_table.rowCount()):
            en_item = self._tp_table.item(row, 1)
            if en_item and row < len(self._toolpath_files):
                self._toolpath_files[row]["enabled"] = (
                    en_item.checkState() == QtCore.Qt.CheckState.Checked
                )

        active = [d for d in self._toolpath_files if d["enabled"]]
        if not active:
            QtWidgets.QMessageBox.information(
                self, "No files", "Add and enable at least one file first."
            )
            return

        from matplotlib.collections import LineCollection
        import matplotlib.cm as cm
        import matplotlib.colors as mcolors

        x_ch = self._tp_x_spin.value()
        y_ch = self._tp_y_spin.value()
        ds = self._tp_ds_spin.value()
        err_ch = self._tp_err_spin.value()
        err2_ch = self._tp_err2_spin.value()
        mode = self._tp_mode_combo.currentText()
        exag = self._tp_exag_spin.value()

        # Remove existing Tool Path tab
        for i in range(self.plot_tabs.count()):
            if self.plot_tabs.tabText(i) == "Tool Path":
                widget = self.plot_tabs.widget(i)
                self.plot_tabs.removeTab(i)
                if widget:
                    widget.deleteLater()
                break

        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        canvas = MplCanvas(tab)
        toolbar = NavigationToolbar(canvas, tab)
        layout.addWidget(toolbar)
        layout.addWidget(canvas)

        polar_mode = (mode == "Polar deviation")
        if polar_mode:
            canvas.figure.clear()
            ax = canvas.figure.add_subplot(111, projection="polar")
        else:
            ax = canvas.figure.add_subplot(111)
            ax.set_aspect("equal", adjustable="datalim")

        any_plotted = False
        for entry in active:
            p = OscilloscopePlotter()
            kwargs = self._build_load_kwargs(entry["path"])
            if not p.load_data(entry["path"], **kwargs):
                QtWidgets.QMessageBox.warning(
                    self, "Load error", f"Could not load: {entry['path'].name}"
                )
                continue
            ncols = p.data.shape[1]
            if x_ch > ncols or y_ch > ncols:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Channel out of range",
                    f"{entry['path'].name} has only {ncols} channels "
                    f"(X=ch{x_ch}, Y=ch{y_ch} requested).",
                )
                continue
            x = p.data[::ds, x_ch - 1]
            y = p.data[::ds, y_ch - 1]

            has_err = err_ch > 0 and err_ch <= ncols
            err = p.data[::ds, err_ch - 1] if has_err else None
            has_err2 = err2_ch > 0 and err2_ch <= ncols
            err2 = p.data[::ds, err2_ch - 1] if has_err2 else None

            if mode == "Color by error" and has_err:
                points = np.column_stack([x, y]).reshape(-1, 1, 2)
                segments = np.concatenate([points[:-1], points[1:]], axis=1)
                e = err[:-1]
                norm = mcolors.Normalize(vmin=e.min(), vmax=e.max())
                lc = LineCollection(segments, cmap="RdYlGn_r", norm=norm, linewidth=1.5)
                lc.set_array(e)
                ax.add_collection(lc)
                canvas.figure.colorbar(lc, ax=ax, label=f"Error (ch {err_ch})", shrink=0.8)
                ax.autoscale()
                ax.set_title(f"Tool Path — colored by error ch{err_ch}")

            elif mode == "Deviation arrows" and has_err:
                ax.plot(x, y, linewidth=0.5, color=entry["color"], alpha=0.4,
                        label=entry["path"].name)
                ex = err
                ey = err2 if has_err2 else np.zeros_like(ex)
                ax.quiver(x, y, ex, ey, np.hypot(ex, ey),
                          cmap="RdYlGn_r", scale_units="xy", angles="xy",
                          width=0.003, label=f"Error ch{err_ch}")
                ax.set_title("Tool Path — deviation arrows")

            elif polar_mode:
                # Polar deviation: compute centroid, angles, radial errors
                cx, cy = x.mean(), y.mean()
                theta = np.arctan2(y - cy, x - cx)
                r_actual = np.hypot(x - cx, y - cy)
                r_ideal = r_actual.mean()
                if has_err:
                    r_dev = err
                else:
                    r_dev = r_actual - r_ideal

                # Sort by angle for a clean polar line
                order = np.argsort(theta)
                th_s = theta[order]
                rd_s = r_dev[order]

                r_plot = r_ideal + rd_s * exag
                ax.plot(th_s, np.full_like(th_s, r_ideal),
                        "--", color="grey", linewidth=0.8, label="Ideal")
                ax.plot(th_s, r_plot, linewidth=1.0, color=entry["color"],
                        label=entry["path"].name)
                ax.set_title(f"Polar deviation (×{exag:.0f} exaggeration)")

            else:  # Plain path
                ax.plot(x, y, linewidth=0.8, color=entry["color"],
                        label=entry["path"].name)
                ax.set_title("CNC Tool Path")

            any_plotted = True

        if not any_plotted:
            tab.deleteLater()
            return

        if not polar_mode:
            ax.set_xlabel(f"Channel {x_ch}")
            ax.set_ylabel(f"Channel {y_ch}")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

        if polar_mode:
            # Toolbar zoom/pan don't work on polar axes — wire scroll wheel instead
            def _polar_scroll(event, _ax=ax):
                if event.inaxes is not _ax:
                    return
                rmin, rmax = _ax.get_rlim()
                center = (rmin + rmax) / 2.0
                half = (rmax - rmin) / 2.0
                factor = 0.85 if event.button == "up" else 1.0 / 0.85
                half *= factor
                _ax.set_rlim(max(0.0, center - half), center + half)
                _ax.figure.canvas.draw_idle()

            canvas.mpl_connect("scroll_event", _polar_scroll)

        canvas.draw()

        self.plot_tabs.addTab(tab, "Tool Path")
        self.plot_tabs.setCurrentWidget(tab)
        self._tab_canvases.append(canvas)
