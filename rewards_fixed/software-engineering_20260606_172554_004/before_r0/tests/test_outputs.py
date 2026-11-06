import subprocess
import os
import tempfile


def compile_calc():
    """Compile calc.c and return path to binary."""
    result = subprocess.run(
        ["gcc", "-Wall", "-o", "/tmp/calc_test", "/app/calc.c", "-lm"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Compilation failed:\n{result.stderr}")
    return "/tmp/calc_test"


def run_calc(csv_content):
    """Run calc with given CSV content and return stdout lines."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(csv_content)
        tmpname = f.name
    try:
        result = subprocess.run(
            ["/tmp/calc_test", tmpname],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"calc failed: {result.stderr}")
        return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
    finally:
        os.unlink(tmpname)


def parse_output(lines):
    """Parse CSV output lines into a 2D list of float values."""
    result = []
    for line in lines:
        row = []
        for val in line.split(','):
            try:
                row.append(float(val))
            except ValueError:
                row.append(float('nan'))
        result.append(row)
    return result


class TestOperatorPrecedence:
    """Tests that verify operator precedence: * and / bind tighter than + and -."""

    def test_sub_before_mul(self):
        """A1-B1*C1 should be A1-(B1*C1), not (A1-B1)*C1."""
        compile_calc()
        out = run_calc("10,5,2\n=A1-B1*C1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 0.0, (
            f"A1-B1*C1 with values 10,5,2 should be 0 (10-5*2=10-10=0), "
            f"got {parsed[1][0]}. Check operator precedence in eval_add_sub."
        )

    def test_add_before_mul(self):
        """A1+B1*C1 should be A1+(B1*C1), not (A1+B1)*C1."""
        compile_calc()
        out = run_calc("10,5,2\n=A1+B1*C1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 20.0, (
            f"A1+B1*C1 with values 10,5,2 should be 20 (10+5*2=10+10=20), "
            f"got {parsed[1][0]}"
        )

    def test_complex_precedence(self):
        """Test multiple operators: A1+B1*C1-D1/E1."""
        compile_calc()
        out = run_calc("10,5,2,8,4\n=A1+B1*C1-D1/E1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 18.0, (
            f"10+5*2-8/4 = 18, got {parsed[1][0]}"
        )

    def test_mul_then_add(self):
        """A1*B1+C1 should be (A1*B1)+C1."""
        compile_calc()
        out = run_calc("3,4,5\n=A1*B1+C1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 17.0, f"3*4+5 = 17, got {parsed[1][0]}"

    def test_div_then_sub(self):
        """A1/B1-C1 should be (A1/B1)-C1."""
        compile_calc()
        out = run_calc("20,4,1\n=A1/B1-C1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 4.0, f"20/4-1 = 4, got {parsed[1][0]}"

    def test_sub_after_div(self):
        """D1-A1/B1*C1 should be D1 - ((A1/B1)*C1)."""
        compile_calc()
        out = run_calc("20,4,3,100\n=D1-A1/B1*C1\n")
        parsed = parse_output(out)
        # 100 - (20/4)*3 = 100 - 5*3 = 85
        assert parsed[1][0] == 85.0, (
            f"100 - 20/4*3 = 85, got {parsed[1][0]}"
        )

    def test_three_adds_with_mul(self):
        """A1+B1+C1*D1 should be A1+B1+(C1*D1)."""
        compile_calc()
        out = run_calc("1,2,3,4\n=A1+B1+C1*D1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 15.0, f"1+2+3*4 = 15, got {parsed[1][0]}"


class TestCsvParsing:
    """Tests that verify correct CSV parsing, especially empty cells."""

    def test_empty_cell_middle(self):
        """Empty cells between commas should be parsed as 0."""
        compile_calc()
        out = run_calc("1,,3\n=B1,=A1+C1\n")
        parsed = parse_output(out)
        assert parsed[0] == [1.0, 0.0, 3.0], (
            f"Row with '1,,3' should parse as [1, 0, 3], got {parsed[0]}. "
            f"Check CSV parsing for consecutive delimiters."
        )

    def test_empty_cell_formula_ref(self):
        """Formulas referencing empty cells should get 0."""
        compile_calc()
        out = run_calc("5,,10\n=A1+B1+C1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 15.0, (
            f"5+0+10 should be 15, got {parsed[1][0]}. "
            f"Empty cell B1 should be treated as 0."
        )

    def test_multiple_empty_cells(self):
        """Multiple consecutive empty cells."""
        compile_calc()
        out = run_calc(",,,5\n=A1+B1+C1+D1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 5.0, (
            f"0+0+0+5 should be 5, got {parsed[1][0]}"
        )

    def test_leading_empty_cell(self):
        """Empty cell at the start of a row."""
        compile_calc()
        out = run_calc(",10,20\n=A1+B1+C1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 30.0, f"0+10+20 should be 30, got {parsed[1][0]}"
        assert parsed[0] == [0.0, 10.0, 20.0], (
            f"Row ',10,20' should parse as [0, 10, 20], got {parsed[0]}"
        )

    def test_formula_ref_empty_cell(self):
        """Formula referencing cell that was parsed as empty should get 0."""
        compile_calc()
        out = run_calc("100,,200\n=C1,=A1+B1\n")
        parsed = parse_output(out)
        # C1 = 200, A1+B1 = 100+0 = 100 (B1 is empty)
        assert parsed[1][0] == 200.0, f"C1 should be 200, got {parsed[1][0]}"
        assert parsed[1][1] == 100.0, f"A1+B1 with B1 empty should be 100, got {parsed[1][1]}"


class TestSumFunction:
    """Tests for the SUM(range) function."""

    def test_sum_row(self):
        compile_calc()
        out = run_calc("1,2,3,4,5\n=SUM(A1:E1)\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 15.0, f"SUM(A1:E1) of [1,2,3,4,5] should be 15, got {parsed[1][0]}"

    def test_sum_column(self):
        compile_calc()
        out = run_calc("10\n20\n30\n=SUM(A1:A3)\n")
        parsed = parse_output(out)
        assert parsed[3][0] == 60.0, f"SUM(A1:A3) of [10,20,30] should be 60, got {parsed[3][0]}"

    def test_sum_rectangle(self):
        compile_calc()
        out = run_calc("1,2\n3,4\n=SUM(A1:B2)\n")
        parsed = parse_output(out)
        assert parsed[2][0] == 10.0, f"SUM(A1:B2) of [[1,2],[3,4]] should be 10, got {parsed[2][0]}"

    def test_sum_single_cell(self):
        """SUM with same start and end cell."""
        compile_calc()
        out = run_calc("42\n=SUM(A1:A1)\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 42.0, f"SUM(A1:A1) should be 42, got {parsed[1][0]}"


class TestCellReferenceResolution:
    """Tests that cell references resolve to the correct cells."""

    def test_chain_without_cycle(self):
        """A chain A1->B1->C1=42 should work (no cycles)."""
        compile_calc()
        out = run_calc("=B1,=C1,42\n")
        parsed = parse_output(out)
        assert parsed[0][0] == 42.0, (
            f"A1 should be 42, got {parsed[0][0]}. Check column indexing: "
            f"A1=B1, B1=C1, C1=42."
        )
        assert parsed[0][1] == 42.0, (
            f"B1 should be 42, got {parsed[0][1]}"
        )
        assert parsed[0][2] == 42.0, (
            f"C1 should be 42, got {parsed[0][2]}"
        )

    def test_simple_reference(self):
        """A1 referencing a number cell should return that number."""
        compile_calc()
        out = run_calc("77\n=A1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 77.0, (
            f"A1 should reference 77, got {parsed[1][0]}. Check col_to_idx."
        )

    def test_two_column_reference(self):
        """B1 should reference the second column."""
        compile_calc()
        out = run_calc("10,20\n=B1,=A1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 20.0, (
            f"B1 should be 20 (column B), got {parsed[1][0]}"
        )
        assert parsed[1][1] == 10.0, (
            f"A1 should be 10 (column A), got {parsed[1][1]}"
        )

    def test_multi_row_reference(self):
        """References across rows should work."""
        compile_calc()
        out = run_calc("5\n10\n=A1+A2\n")
        parsed = parse_output(out)
        assert parsed[2][0] == 15.0, (
            f"A1+A2 = 5+10 = 15, got {parsed[2][0]}. Check row indexing."
        )


class TestEdgeCases:
    """Additional edge case tests."""

    def test_unary_negation(self):
        """-A1 and --A1."""
        compile_calc()
        out = run_calc("5\n=-A1,=--A1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == -5.0, f"-5 should be -5, got {parsed[1][0]}"
        assert parsed[1][1] == 5.0, f"--5 should be 5, got {parsed[1][1]}"

    def test_negation_with_expression(self):
        """-(A1+B1) should negate the sum."""
        compile_calc()
        out = run_calc("10,3\n=-(A1+B1)\n")
        parsed = parse_output(out)
        assert parsed[1][0] == -13.0, f"-(10+3) should be -13, got {parsed[1][0]}"

    def test_formula_chains(self):
        """A long chain of formula references."""
        compile_calc()
        out = run_calc("5\n=A1+1\n=A2+1\n=A3+1\n=A4+1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 6.0, f"5+1=6, got {parsed[1][0]}"
        assert parsed[2][0] == 7.0, f"6+1=7, got {parsed[2][0]}"
        assert parsed[3][0] == 8.0, f"7+1=8, got {parsed[3][0]}"
        assert parsed[4][0] == 9.0, f"8+1=9, got {parsed[4][0]}"

    def test_column_mapping_with_multi_column(self):
        """Verify column A maps to first column, B to second, C to third."""
        compile_calc()
        out = run_calc("10,20,30\n=C1,=A1,=B1\n")
        parsed = parse_output(out)
        assert parsed[0] == [10.0, 20.0, 30.0], (
            f"Input row should be [10, 20, 30], got {parsed[0]}"
        )
        assert parsed[1][0] == 30.0, (
            f"C1 should reference column C (value 30), got {parsed[1][0]}"
        )
        assert parsed[1][1] == 10.0, (
            f"A1 should reference column A (value 10), got {parsed[1][1]}"
        )
        assert parsed[1][2] == 20.0, (
            f"B1 should reference column B (value 20), got {parsed[1][2]}"
        )

    def test_division_computation(self):
        """Basic division should compute correctly with correct column mapping."""
        compile_calc()
        out = run_calc("20,4\n=A1/B1\n")
        parsed = parse_output(out)
        assert parsed[1][0] == 5.0, (
            f"20/4 should be 5, got {parsed[1][0]}. Check column indexing: "
            f"A1 should reference column A (20), B1 should reference column B (4)."
        )

    def test_sum_with_formula_cells(self):
        """SUM over cells that contain formulas."""
        compile_calc()
        out = run_calc("2,3\n4,5\n=A1+B1,=A2*B2\n=SUM(A1:B2)\n")
        parsed = parse_output(out)
        assert parsed[3][0] == 14.0, (
            f"SUM(A1:B2) of [[2,3],[4,5]] should be 14, got {parsed[3][0]}"
        )
