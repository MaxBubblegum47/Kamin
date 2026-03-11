import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional


class OscilloscopePlotter:
    """Main class for oscilloscope data visualization."""

    def __init__(self):
        self.data = None
        self.column_names = []
        self.selected_columns = []
        self.column_labels = {}
        self.file_path = ""

    def load_data(
        self,
        file_path: str | Path,
        *,
        fmt: str = "dat",
        # CSV options
        sep: str = ",",
        header=0,
        skiprows: int = 0,
        decimal: str = ".",
        # JSON options
        orient: str = "columns",
    ) -> bool:
        """
        Load data from a .dat / .txt, .csv, or .json file.

        Args:
            file_path: Path to the data file.
            fmt: One of "dat" (auto-detect delimiter, original behaviour),
                 "csv", or "json".
            sep: Column delimiter for CSV files (default ",").
            header: Row number to use as column names for CSV, or None for
                    no header (columns will be named ch1, ch2, …).
            skiprows: Number of rows to skip at the top of a CSV file.
            decimal: Decimal separator for CSV files (e.g. "," for
                     European locale files).
            orient: pandas read_json orient argument for JSON files.

        Returns:
            bool: True if successful, False otherwise.
        """
        self.file_path = str(file_path)
        file_path = Path(file_path)

        try:
            if fmt == "csv":
                df = pd.read_csv(
                    file_path,
                    sep=sep,
                    header=header,
                    skiprows=skiprows,
                    decimal=decimal,
                )
                # Drop non-numeric columns silently so the plotter always
                # receives a clean float array.
                df = df.select_dtypes(include="number")
                if df.empty:
                    raise ValueError("No numeric columns found in CSV file.")

                # Ensure all column names are non-empty strings.
                if header is None:
                    df.columns = [f"ch{i + 1}" for i in range(len(df.columns))]
                else:
                    df.columns = [
                        str(c).strip() if str(c).strip() else f"ch{i + 1}"
                        for i, c in enumerate(df.columns)
                    ]

                self.data = df.to_numpy(dtype=float, na_value=float("nan"))
                self.column_names = list(df.columns)

            elif fmt == "json":
                df = pd.read_json(file_path, orient=orient)
                df = df.select_dtypes(include="number")
                if df.empty:
                    raise ValueError("No numeric columns found in JSON file.")
                df.columns = [
                    str(c).strip() if str(c).strip() else f"ch{i + 1}"
                    for i, c in enumerate(df.columns)
                ]
                self.data = df.to_numpy(dtype=float, na_value=float("nan"))
                self.column_names = list(df.columns)

            else:
                # Original .dat / .txt behaviour: try common delimiters in
                # order and pick the first one that yields numeric data.
                delimiters = [r'\s+', '\t', ',', ';']
                best_df = None

                for delimiter in delimiters:
                    try:
                        df = pd.read_csv(
                            file_path,
                            sep=delimiter,
                            comment='#',
                            header=None,
                            engine='python',
                        )
                        if df.empty:
                            continue
                        df_num = df.apply(pd.to_numeric, errors='coerce')
                        valid_cols = df_num.columns[df_num.notna().any()].tolist()
                        if len(valid_cols) >= 1:
                            best_df = df_num[valid_cols]
                            break
                    except Exception:
                        continue

                if best_df is None or best_df.empty:
                    raise ValueError(f"Could not parse numeric data from {file_path}")

                best_df = best_df.dropna(how='all').reset_index(drop=True)
                if best_df.empty:
                    raise ValueError(f"No numeric data found in {file_path}")

                self.data = best_df.values
                self.column_names = [f"Channel_{i + 1}" for i in range(self.data.shape[1])]

        except Exception as e:
            print(f"[OscilloscopePlotter] load_data error: {e}")
            self.data = None
            self.column_names = []
            return False

        # Reset downstream state
        num_columns = self.data.shape[1]
        self.column_labels = {}
        self.selected_columns = list(range(1, num_columns + 1))

        print(f"Successfully loaded data from {file_path}")
        print(f"Found {num_columns} columns with {len(self.data)} rows")

        print("\nFirst 5 rows of data:")
        sample = self.data[:5] if len(self.data) >= 5 else self.data
        for i, row in enumerate(sample):
            print(f"  Row {i + 1}: {row}")

        print("\nData statistics:")
        for i in range(min(num_columns, 5)):
            col = self.data[:, i]
            col = col[~np.isnan(col)]
            if len(col) > 0:
                print(f"  {self.column_names[i]}: min={col.min():.3f}, "
                      f"max={col.max():.3f}, mean={col.mean():.3f}")
            else:
                print(f"  {self.column_names[i]}: no valid numeric data")

        return True

    def get_user_input(self) -> bool:
        """
        Get user preferences for plotting.

        Returns:
            bool: True if user provided valid input, False if they want to quit
        """
        num_columns = self.data.shape[1]

        print(f"\nAvailable columns: {num_columns}")
        print("Column indices: 1 to", num_columns)

        while True:
            try:
                columns_input = input("\nEnter column numbers to plot (e.g., '1,3,5' or '1-4'): ").strip()

                if columns_input.lower() in ['q', 'quit', 'exit']:
                    return False

                selected_cols = self.parse_column_input(columns_input, num_columns)

                if selected_cols:
                    self.selected_columns = selected_cols
                    break
                else:
                    print("Invalid input. Please enter valid column numbers.")

            except KeyboardInterrupt:
                return False
            except Exception:
                print("Invalid input format. Please try again.")

        print(f"\nSelected columns: {self.selected_columns}")
        print("Please provide labels for each selected column:")

        for col_idx in self.selected_columns:
            default_label = self.column_names[col_idx - 1]
            label = input(f"Label for column {col_idx} (default: {default_label}): ").strip()
            self.column_labels[col_idx] = label if label else default_label

        return True

    def parse_column_input(self, input_str: str, max_columns: int) -> List[int]:
        """
        Parse column input string to extract column numbers.

        Args:
            input_str: User input string
            max_columns: Maximum number of columns available

        Returns:
            List[int]: List of column numbers (1-based)
        """
        columns = set()
        parts = [part.strip() for part in input_str.split(',')]

        for part in parts:
            if '-' in part:
                try:
                    start, end = map(int, part.split('-'))
                    if 1 <= start <= max_columns and 1 <= end <= max_columns:
                        columns.update(range(min(start, end), max(start, end) + 1))
                    else:
                        return []
                except ValueError:
                    return []
            else:
                try:
                    col_num = int(part)
                    if 1 <= col_num <= max_columns:
                        columns.add(col_num)
                    else:
                        return []
                except ValueError:
                    return []

        return sorted(list(columns))

    def plot_data(self, save_plot: bool = False, output_path: str = "") -> None:
        """
        Create and display plots for selected columns.

        Args:
            save_plot: Whether to save the plot to file
            output_path: Path to save the plot (if save_plot is True)
        """
        if not self.selected_columns:
            print("No columns selected for plotting.")
            return

        num_plots = len(self.selected_columns)
        fig, axes = plt.subplots(num_plots, 1, figsize=(12, 3 * num_plots))

        if num_plots == 1:
            axes = [axes]

        x_data = np.arange(len(self.data))

        for i, col_idx in enumerate(self.selected_columns):
            y_data = self.data[:, col_idx - 1]
            label = self.column_labels.get(col_idx, f"Channel {col_idx}")

            axes[i].plot(x_data, y_data, linewidth=1.5, label=label)
            axes[i].set_title(f"Oscilloscope Channel: {label}")
            axes[i].set_ylabel("Amplitude")
            axes[i].grid(True, alpha=0.3)
            axes[i].legend()

        axes[-1].set_xlabel("Sample Number / Time")
        plt.tight_layout()

        if save_plot and output_path:
            try:
                plt.savefig(output_path, dpi=300, bbox_inches='tight')
                print(f"Plot saved to: {output_path}")
            except Exception as e:
                print(f"Error saving plot: {e}")

        plt.show()

    def plot_phase(
        self,
        x_col: int,
        y_col: int,
        save_plot: bool = False,
        output_path: str = "",
    ) -> None:
        """
        Create and display a phase plot (Y vs X) for two columns.

        Args:
            x_col: Column index (1-based) for X axis
            y_col: Column index (1-based) for Y axis
            save_plot: Whether to save the plot to file
            output_path: Path to save the plot (if save_plot is True)
        """
        if self.data is None:
            print("No data loaded.")
            return

        num_columns = self.data.shape[1]
        if not (1 <= x_col <= num_columns and 1 <= y_col <= num_columns):
            print(f"Invalid column indices. Must be between 1 and {num_columns}.")
            return

        x_data = self.data[:, x_col - 1]
        y_data = self.data[:, y_col - 1]

        x_label = self.column_labels.get(x_col, self.column_names[x_col - 1])
        y_label = self.column_labels.get(y_col, self.column_names[y_col - 1])

        plt.figure(figsize=(6, 6))
        plt.plot(x_data, y_data, linewidth=1.5)
        plt.xlabel(x_label)
        plt.ylabel(y_label)
        plt.title(f"Phase Plot: {y_label} vs {x_label}")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        if save_plot and output_path:
            try:
                plt.savefig(output_path, dpi=300, bbox_inches='tight')
                print(f"Phase plot saved to: {output_path}")
            except Exception as e:
                print(f"Error saving phase plot: {e}")

        plt.show()

    def run_interactive(self) -> None:
        """Run the interactive plotting session."""
        print("=== Virtual Oscilloscope Data Plotter ===")

        while True:
            file_path = input("Enter path to .dat file: ").strip()

            if file_path.lower() in ['q', 'quit', 'exit']:
                print("Goodbye!")
                return

            if self.load_data(Path(file_path)):
                break
            else:
                print("Please try again or enter 'q' to quit.")

        if not self.get_user_input():
            print("Goodbye!")
            return

        print("\nGenerating plots...")

        save_choice = input("\nSave plot to file? (y/n): ").strip().lower()
        save_plot = save_choice in ['y', 'yes']

        output_path = ""
        if save_plot:
            default_name = f"{Path(self.file_path).stem}_plot.png"
            output_path = input(f"Enter output path (default: {default_name}): ").strip()
            if not output_path:
                output_path = default_name

        self.plot_data(save_plot=save_plot, output_path=output_path)
        print("Plotting complete!")