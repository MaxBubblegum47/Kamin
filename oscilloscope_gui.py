"""
PyQt-based GUI for the Virtual Oscilloscope Data Plotter.

Sequence of operations:
0) Select and load one .dat / .csv / .json file.
1) Inspect tracks (columns) and assign labels and scaling.
2) Choose options such as time plots, phase plots, and FFT.
3) Generate plots in an embedded matplotlib widget or in a new window.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        fig = Figure(figsize=(6, 4), tight_layout=True)
        super().__init__(fig)
        self.setParent(parent)


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

        # UI
        self._setup_ui()
        self._apply_windows98_palette()

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

        # Middle: tracks table and options
        mid_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_layout.addWidget(mid_splitter, 1)

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

        mid_splitter.addWidget(tracks_group)

        # Right: options and plotting
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
        generate_btn.clicked.connect(self._on_generate_clicked)
        btn_layout.addStretch(1)
        btn_layout.addWidget(stats_btn)
        btn_layout.addWidget(generate_btn)
        options_layout.addLayout(btn_layout)

        mid_splitter.addWidget(options_group)
        mid_splitter.setStretchFactor(0, 3)
        mid_splitter.setStretchFactor(1, 2)

        # Bottom: embedded matplotlib canvas area (tabbed)
        self.canvas_group = QtWidgets.QGroupBox("Plot preview")
        canvas_layout = QtWidgets.QVBoxLayout(self.canvas_group)
        self.plot_tabs = QtWidgets.QTabWidget(self.canvas_group)
        canvas_layout.addWidget(self.plot_tabs)
        main_layout.addWidget(self.canvas_group, 2)

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
        if not self._main_layout or self._preview_window:
            return

        self._main_layout.removeWidget(self.canvas_group)

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

        if self.canvas_group.parent() is not self:
            self.canvas_group.setParent(self)
        self._main_layout.addWidget(self.canvas_group, 2)

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
            plots_dir = self._plots_dir()
            if self.time_enabled_checkbox.isChecked() and enabled_tracks:
                time_output = (
                    str(plots_dir / f"{self.current_file.stem}_time.png")
                    if plots_dir
                    else ""
                )
                self.plotter.plot_data(save_plot=bool(time_output), output_path=time_output)

            if self.phase_enabled_checkbox.isChecked():
                x_col = self.phase_x_combo.currentData()
                y_col = self.phase_y_combo.currentData()
                if x_col and y_col:
                    phase_output = (
                        str(plots_dir / f"{self.current_file.stem}_phase.png")
                        if plots_dir
                        else ""
                    )
                    self.plotter.plot_phase(
                        x_col=int(x_col),
                        y_col=int(y_col),
                        save_plot=bool(phase_output),
                        output_path=phase_output,
                    )

        if self.embed_plot_checkbox.isChecked() and self.current_file and self._tab_canvases:
            plots_dir = self._plots_dir()
            stem = self.current_file.stem
            for idx, canvas in enumerate(self._tab_canvases, start=1):
                output_path = plots_dir / f"{stem}_gui_{idx}.png"
                try:
                    canvas.figure.savefig(output_path, dpi=300, bbox_inches="tight")
                except Exception:
                    pass

    # -------------------------------------------------------- embedded plotting
    def _render_embedded_plots(self, do_time: bool, do_phase: bool, do_fft: bool) -> None:
        self._clear_plot_tabs()
        made_any = False

        if do_time and self.plotter.selected_columns:
            if self.time_merge_checkbox.isChecked():
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                canvas = MplCanvas(tab)
                toolbar = NavigationToolbar(canvas, tab)
                layout.addWidget(toolbar)
                layout.addWidget(canvas)

                ax = canvas.figure.add_subplot(111)
                x_data = range(len(self.plotter.data))
                for col_idx in self.plotter.selected_columns:
                    scale, offset = self._scaling.get(col_idx, (1.0, 0.0))
                    y_data = scale * self.plotter.data[:, col_idx - 1] + offset
                    label = self.plotter.column_labels.get(col_idx, f"Channel {col_idx}")
                    ax.plot(x_data, y_data, linewidth=1.0, label=label)
                ax.set_xlabel("Sample Number / Time")
                ax.set_ylabel("Amplitude")
                ax.set_title("Merged channels")
                ax.grid(True, alpha=0.3)
                ax.legend()
                if self.logy_checkbox.isChecked():
                    ax.set_yscale("log")

                canvas.draw()
                self.plot_tabs.addTab(tab, "Time (merged)")
                self._tab_canvases.append(canvas)
                made_any = True
            else:
                for col_idx in self.plotter.selected_columns:
                    label = self.plotter.column_labels.get(col_idx, f"Channel {col_idx}")
                    tab = QtWidgets.QWidget()
                    layout = QtWidgets.QVBoxLayout(tab)
                    canvas = MplCanvas(tab)
                    toolbar = NavigationToolbar(canvas, tab)
                    layout.addWidget(toolbar)
                    layout.addWidget(canvas)

                    ax = canvas.figure.add_subplot(111)
                    scale, offset = self._scaling.get(col_idx, (1.0, 0.0))
                    y_data = scale * self.plotter.data[:, col_idx - 1] + offset
                    ax.plot(range(len(self.plotter.data)), y_data, linewidth=1.0, label=label)
                    ax.set_xlabel("Sample Number / Time")
                    ax.set_ylabel("Amplitude")
                    ax.set_title(label)
                    ax.grid(True, alpha=0.3)
                    ax.legend()
                    if self.logy_checkbox.isChecked():
                        ax.set_yscale("log")

                    canvas.draw()
                    self.plot_tabs.addTab(tab, label)
                    self._tab_canvases.append(canvas)
                    made_any = True

        if do_phase:
            x_col = self.phase_x_combo.currentData()
            y_col = self.phase_y_combo.currentData()
            if x_col and y_col:
                x_col, y_col = int(x_col), int(y_col)
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                canvas = MplCanvas(tab)
                toolbar = NavigationToolbar(canvas, tab)
                layout.addWidget(toolbar)
                layout.addWidget(canvas)

                ax_phase = canvas.figure.add_subplot(111)
                x_data = self.plotter.data[:, x_col - 1]
                y_data = self.plotter.data[:, y_col - 1]
                x_label = self.plotter.column_labels.get(x_col, self.plotter.column_names[x_col - 1])
                y_label = self.plotter.column_labels.get(y_col, self.plotter.column_names[y_col - 1])
                ax_phase.plot(x_data, y_data, linewidth=1.0)
                ax_phase.set_xlabel(x_label)
                ax_phase.set_ylabel(y_label)
                ax_phase.set_title(f"Phase Plot: {y_label} vs {x_label}")
                ax_phase.grid(True, alpha=0.3)

                canvas.draw()
                self.plot_tabs.addTab(tab, "Phase")
                self._tab_canvases.append(canvas)
                made_any = True

        if do_fft:
            ch = self.fft_channel_combo.currentData()
            if ch:
                ch = int(ch)
                tab = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(tab)
                canvas = MplCanvas(tab)
                toolbar = NavigationToolbar(canvas, tab)
                layout.addWidget(toolbar)
                layout.addWidget(canvas)

                ax_fft = canvas.figure.add_subplot(111)
                scale, offset = self._scaling.get(ch, (1.0, 0.0))
                y = scale * self.plotter.data[:, ch - 1] + offset
                n = len(y)
                if n > 0:
                    y = y - np.mean(y)
                    freqs = np.fft.rfftfreq(n, d=1.0)
                    spectrum = np.abs(np.fft.rfft(y))
                    ax_fft.plot(freqs, spectrum)
                ch_label = self.plotter.column_labels.get(ch, self.plotter.column_names[ch - 1])
                ax_fft.set_xlabel("Frequency (1 / sample)")
                ax_fft.set_ylabel("Amplitude")
                ax_fft.set_title(f"FFT: {ch_label}")
                ax_fft.grid(True, alpha=0.3)

                canvas.draw()
                self.plot_tabs.addTab(tab, "FFT")
                self._tab_canvases.append(canvas)
                made_any = True

        if not made_any:
            self.plot_tabs.addTab(QtWidgets.QWidget(), "No plots")
