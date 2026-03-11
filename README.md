# Virtual Oscilloscope Data Plotter

A Python program designed to visualize plots from virtual oscilloscope data files. This tool helps you analyze and visualize up to 16 channels/tracks of oscilloscope data with customizable labels and plotting options.

## Features

- **Multi-channel support**: Handle up to 16 channels of oscilloscope data
- **Flexible data loading**: Automatically detects common delimiters (tabs, commas, spaces, semicolons)
- **Interactive mode**: User-friendly command-line interface for selecting columns and labels
- **Batch processing**: Command-line arguments for automated plotting
- **Customizable labels**: Assign meaningful names to each channel for better visualization
- **High-quality plots**: Generate publication-ready plots with matplotlib
- **Save functionality**: Export plots to various image formats

## Installation

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Make the script executable (optional):**
   ```bash
   chmod +x oscilloscope_plotter.py
   ```

## Usage

### Interactive Mode

Run the program without arguments to start the interactive mode:

```bash
python main.py
```

The program will guide you through:
1. Loading your .dat file
2. Selecting which columns to plot
3. Assigning labels to each selected column
4. Choosing whether to save the plot

### Command Line Arguments

For automated or batch processing:

```bash
python main.py [OPTIONS] file.dat
```

**Available options:**
- `--columns, -c`: Specify columns to plot (e.g., "1,3,5" or "1-4")
- `--labels, -l`: Assign labels to columns (e.g., "Voltage,Current,Temperature")
- `--output, -o`: Save plot to specified file path
- `--non-interactive, -n`: Run without interactive prompts
- `--phase, -p`: Create a phase plot using two columns, e.g. `"1,2"` (X,Y)

**Examples:**

1. **Plot specific columns with labels:**
   ```bash
   python main.py --columns "1,3,5" --labels "Input,Output,Reference" data.dat
   ```
   
2. **Plot a range of columns:**
   ```bash
   python main.py --columns "1-4" --labels "CH1,CH2,CH3,CH4" data.dat
   ```
   
3. **Save plot to file:**
   ```bash
   python main.py --columns "1,2" --output "output_plot.png" data.dat
   ```
   
4. **Non-interactive batch processing:**
   ```bash
   python main.py --non-interactive --columns "1-8" --labels "A,B,C,D,E,F,G,H" data.dat
   ```

5. **Phase (Lissajous) plot from two channels:**
   ```bash
   python main.py data.dat --non-interactive --phase "1,2" --labels "X_signal,Y_signal" --output "phase_plot.png"
   ```
   This plots column 2 versus column 1 (Y vs X). If the two channels are sinusoids in perfect quadrature with the same amplitude, the result is a circle; otherwise you will see an ellipse or a more general Lissajous figure.

## Data Format

The program supports .dat files with the following characteristics:
- **Delimiters**: Tabs, commas, spaces, or semicolons
- **Header**: Optional (if present, it will be ignored)
- **Data**: Numeric values only
- **Columns**: Up to 16 columns supported
- **Rows**: No limit on the number of data points

**Example data format:**
```
# Optional comment lines start with #
0.0    1.2    0.8    2.1    1.5
0.1    1.4    0.9    2.3    1.6
0.2    1.6    1.1    2.5    1.7
...
```

## Column Selection Syntax

- **Single columns**: `1,3,5` (plots columns 1, 3, and 5)
- **Ranges**: `1-4` (plots columns 1, 2, 3, and 4)
- **Mixed**: `1,3-5,7` (plots columns 1, 3, 4, 5, and 7)

## Output

The program generates individual subplots for each selected column with:
- **X-axis**: Sample number or time index
- **Y-axis**: Amplitude values
- **Title**: Channel label
- **Grid**: Enabled for better readability
- **Legend**: Shows the channel label

## Example

Using the provided `example_data.dat` file:

```bash
python oscilloscope_plotter.py example_data.dat
```

This will load the example data with 5 channels and guide you through the plotting process.

## Dependencies

- **numpy**: For numerical operations
- **matplotlib**: For plotting and visualization
- **pandas**: For data loading and manipulation
- **argparse**: For command-line argument parsing

## Troubleshooting

### Common Issues

1. **"Could not load data" error**:
   - Check that the file exists and has read permissions
   - Ensure the file contains valid numeric data
   - Try different delimiters in your data file

2. **"Invalid column specification"**:
   - Verify column numbers are within the range of your data
   - Check the syntax for ranges and comma-separated values

3. **Plot not displaying**:
   - Ensure you have a display environment (for GUI plots)
   - Try saving the plot to a file instead

### File Format Tips

- Use consistent delimiters throughout the file
- Avoid mixing different delimiter types
- Remove any non-numeric characters from data columns
- Comment lines should start with `#`

## License

This project is open source and available under the MIT License.

## Support

For issues, questions, or feature requests, please create an issue in the project repository or contact the maintainer.