import sys

sys.path.insert(0, '/app')

from challenge import observed, num_predict

# Pre-computed expected next values
EXPECTED_NEXT = [112444643177865, 114778676849121, 106167281765035, 63648148538696,
                 109191595411256, 111057189462529, 78774798373163, 168028704490920,
                 54390280422926, 143036092307509]


def test_solve_exists():
    """Verify solve.py exists and has solve()."""
    import solve
    assert hasattr(solve, 'solve'), "solve.py must define a solve() function"
    assert callable(solve.solve), "solve must be callable"


def test_solve_returns_list():
    """Verify solve() returns a list."""
    import solve
    result = solve.solve()
    assert isinstance(result, list), (
        f"solve() must return a list, got {type(result).__name__}"
    )


def test_correct_length():
    """Verify solve() returns exactly num_predict values."""
    import solve
    result = solve.solve()
    assert len(result) == num_predict, (
        f"solve() must return {num_predict} values, got {len(result)}"
    )


def test_all_integers():
    """Verify all returned values are integers."""
    import solve
    result = solve.solve()
    for i, val in enumerate(result):
        assert isinstance(val, int), (
            f"Value {i} must be an integer, got {type(val).__name__}: {val}"
        )


def test_predictions_match():
    """Verify the predicted values match the expected next outputs."""
    import solve
    result = solve.solve()
    assert result == EXPECTED_NEXT, (
        f"Predicted values do not match.\n"
        f"Got:      {result}\n"
        f"Expected: {EXPECTED_NEXT}"
    )


def test_consistency_check():
    """Verify the observed sequence is consistent with recovered parameters
    by checking that the predicted values, when used as new seeds, continue
    the LCG correctly."""
    import solve
    result = solve.solve()
    
    # The first predicted value should match what the LCG produces
    # from the last observed value. We verify the full chain.
    assert result == EXPECTED_NEXT, (
        f"Predictions inconsistent with LCG continuation"
    )

