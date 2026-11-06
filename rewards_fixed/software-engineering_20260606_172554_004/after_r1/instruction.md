The file /app/calc.c is a simple spreadsheet calculator. It reads a CSV file where each cell can contain:
- A number (e.g., 42, -3.14)
- A formula starting with '=' that references other cells using standard arithmetic: +, -, *, /, parentheses, and unary negation
- The SUM function: =SUM(A1:B3) sums all cells in the rectangular range

An empty CSV field represents an empty cell; it keeps its position in the row and has value 0.

The program outputs the computed values as CSV to stdout. Cell references use Excel-style notation (A=column 0, B=column 1, ..., Z=column 25; rows are 1-indexed).

However, calc.c has several bugs that cause incorrect results for certain inputs. A Makefile is provided — build with `make`.

Find and fix all bugs so that the program correctly evaluates any valid spreadsheet. Your fixes should stay within the existing architecture of the program (no complete rewrite needed).
