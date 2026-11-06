"""
Tests for the spectral peak analysis task.
Verifies that peaks are correctly identified and fitted with proper uncertainties.
"""
import json
import math
import os
from pathlib import Path


def test_results_file_exists():
    """Test that the results file was created."""
    results_path = Path("/app/results/peaks.json")
    assert results_path.exists(), "Results file /app/results/peaks.json not found"


def test_correct_number_of_peaks():
    """Test that exactly 4 peaks were identified."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    assert "peaks" in results, "Results must contain 'peaks' key"
    assert len(results["peaks"]) == 4, f"Expected 4 peaks, found {len(results['peaks'])}"


def test_peak_structure():
    """Test that each peak has the required fields."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    required_fields = ["center_nm", "amplitude", "width_nm",
                       "center_uncertainty", "amplitude_uncertainty", "width_uncertainty"]
    
    for i, peak in enumerate(results["peaks"]):
        for field in required_fields:
            assert field in peak, f"Peak {i} missing field: {field}"


def test_peak_positions():
    """Test that peaks are identified at approximately correct wavelengths."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    centers = sorted([p["center_nm"] for p in results["peaks"]])
    
    assert len(centers) == 4, "Should have 4 peaks"
    
    # Peaks should be spread across the wavelength range
    # First two peaks should be close together (the overlapping pair near 450nm)
    assert centers[0] < 500, f"First peak should be in lower wavelength range, got {centers[0]}"
    
    # Check that we have peaks in different spectral regions
    assert centers[1] - centers[0] < 20, f"First two peaks should be relatively close together (overlapping pair), got separation {centers[1] - centers[0]:.1f}nm"
    assert centers[2] > 480, f"Third peak should be in mid wavelength range, got {centers[2]}"
    assert centers[3] > 550, f"Fourth peak should be in higher wavelength range, got {centers[3]}"


def test_uncertainties_are_finite():
    """Test that all uncertainties are finite positive numbers."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    for i, peak in enumerate(results["peaks"]):
        for field in ["center_uncertainty", "amplitude_uncertainty", "width_uncertainty"]:
            val = peak[field]
            assert isinstance(val, (int, float)), f"Peak {i} {field} must be a number"
            assert math.isfinite(val), f"Peak {i} {field} must be finite (got {val})"
            assert val > 0, f"Peak {i} {field} must be positive (got {val})"


def test_parameters_are_finite():
    """Test that all parameters are finite numbers."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    for i, peak in enumerate(results["peaks"]):
        for field in ["center_nm", "amplitude", "width_nm"]:
            val = peak[field]
            assert isinstance(val, (int, float)), f"Peak {i} {field} must be a number"
            assert math.isfinite(val), f"Peak {i} {field} must be finite (got {val})"


def test_overlapping_peaks_resolved():
    """Test that the two close peaks are resolved as separate peaks."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    centers = sorted([p["center_nm"] for p in results["peaks"]])
    
    # The first two peaks should be separated by at least 2nm (they should not be merged)
    separation = centers[1] - centers[0]
    assert separation > 2, f"First two peaks appear merged (separation={separation:.1f}nm). Two close peaks should be resolved."
    assert separation < 25, f"First two peaks too far apart ({separation:.1f}nm). They should be within ~10nm."


def test_widths_are_positive():
    """Test that all peak widths are positive."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    for i, peak in enumerate(results["peaks"]):
        assert peak["width_nm"] > 0, f"Peak {i} width must be positive"


def test_reasonable_uncertainty_magnitudes():
    """Test that uncertainties are reasonable (not extremely large)."""
    with open("/app/results/peaks.json", "r") as f:
        results = json.load(f)
    
    for i, peak in enumerate(results["peaks"]):
        # Center uncertainty should be reasonable relative to wavelength
        assert peak["center_uncertainty"] < 50, \
            f"Peak {i} center uncertainty too large: {peak['center_uncertainty']}"
        
        # Width uncertainty should be reasonable
        assert peak["width_uncertainty"] < 50, \
            f"Peak {i} width uncertainty too large: {peak['width_uncertainty']}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
