"""CLI commands for datatools package."""

import argparse
import sys


def main_csv2json():
    """Convert CSV files to JSON format."""
    parser = argparse.ArgumentParser(description="Convert CSV to JSON")
    parser.add_argument("input", nargs="?", help="Input CSV file")
    parser.add_argument("-o", "--output", help="Output JSON file")
    args = parser.parse_args()
    
    import csv
    import json
    
    if args.input:
        with open(args.input, 'r') as f:
            reader = csv.DictReader(f)
            data = list(reader)
    else:
        reader = csv.DictReader(sys.stdin)
        data = list(reader)
    
    output = json.dumps(data, indent=2)
    
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output)
    else:
        print(output)


def main_json2csv():
    """Convert JSON files to CSV format."""
    parser = argparse.ArgumentParser(description="Convert JSON to CSV")
    parser.add_argument("input", nargs="?", help="Input JSON file")
    parser.add_argument("-o", "--output", help="Output CSV file")
    args = parser.parse_args()
    
    import csv
    import json
    
    if args.input:
        with open(args.input, 'r') as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)
    
    if not data:
        return
    
    fieldnames = list(data[0].keys())
    
    if args.output:
        with open(args.output, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def main_validate():
    """Validate data files (JSON/CSV)."""
    parser = argparse.ArgumentParser(description="Validate data files")
    parser.add_argument("files", nargs="+", help="Files to validate")
    parser.add_argument("-f", "--format", choices=["json", "csv"], help="Expected format")
    args = parser.parse_args()
    
    import json
    import csv
    
    errors = []
    for filepath in args.files:
        try:
            ext = args.format or filepath.rsplit('.', 1)[-1].lower()
            if ext == "json":
                with open(filepath, 'r') as f:
                    json.load(f)
            elif ext == "csv":
                with open(filepath, 'r') as f:
                    csv.reader(f)  # Just check it can be read
            else:
                errors.append(f"{filepath}: unknown format '{ext}'")
        except Exception as e:
            errors.append(f"{filepath}: {e}")
    
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"All {len(args.files)} file(s) valid.")


def main_stats():
    """Show statistics about data files."""
    parser = argparse.ArgumentParser(description="Show data statistics")
    parser.add_argument("input", help="Input file (JSON or CSV)")
    args = parser.parse_args()
    
    import json
    import csv
    
    ext = args.input.rsplit('.', 1)[-1].lower()
    
    if ext == "json":
        with open(args.input, 'r') as f:
            data = json.load(f)
    elif ext == "csv":
        with open(args.input, 'r') as f:
            reader = csv.DictReader(f)
            data = list(reader)
    else:
        print(f"Unknown format: {ext}", file=sys.stderr)
        sys.exit(1)
    
    if isinstance(data, list):
        print(f"Records: {len(data)}")
        if data and isinstance(data[0], dict):
            print(f"Fields: {len(data[0])}")
            print(f"Field names: {', '.join(data[0].keys())}")
    elif isinstance(data, dict):
        print(f"Top-level keys: {len(data)}")
        print(f"Key names: {', '.join(data.keys())}")
    else:
        print(f"Type: {type(data).__name__}")


def main_datatools():
    """Main entry point - list all available commands."""
    print("datatools - CLI utilities for data processing")
    print()
    print("Available commands:")
    print("  csv2json  - Convert CSV to JSON format")
    print("  json2csv  - Convert JSON to CSV format")
    print("  validate  - Validate data files")
    print("  stats     - Show statistics about data files")
    print()
    print("Use 'datatools <command> --help' for more information.")
