# datatools

CLI utilities for data processing.

## Installation

```bash
pip install .
```

## Usage

After installation, the following commands will be available:

- `datatools` - Main command, shows available tools
- `csv2json` - Convert CSV to JSON format
- `json2csv` - Convert JSON to CSV format
- `validate` - Validate data files
- `stats` - Show statistics about data files

### Examples

```bash
# List all commands
datatools

# Convert CSV to JSON
csv2json data.csv -o output.json

# Convert JSON to CSV
json2csv data.json -o output.csv

# Validate files
validate data1.json data2.json

# Show statistics
stats data.json
```

## Development

Install in editable mode:

```bash
pip install -e .
```
