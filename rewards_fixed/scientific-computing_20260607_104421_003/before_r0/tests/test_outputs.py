import math
from pathlib import Path


# Reference I(q) values computed using correct Debye formula implementation.
# Computed with double-precision arithmetic, 327 atoms, Cromer-Mann form factors.
REFERENCE_IQ = {
    0.01: 4844820.537015,
    0.02: 4800066.314509,
    0.03: 4726364.225432,
    0.04: 4625020.821056,
    0.05: 4497814.484113,
    0.06: 4346946.823816,
    0.07: 4174983.199674,
    0.08: 3984784.459235,
    0.09: 3779432.252276,
    0.10: 3562150.463883,
    0.11: 3336225.385423,
    0.12: 3104927.214341,
    0.13: 2871435.344781,
    0.14: 2638769.689242,
    0.15: 2409729.968915,
    0.16: 2186844.542546,
    0.17: 1972329.928438,
    0.18: 1768061.731107,
    0.19: 1575557.233174,
    0.20: 1395969.473950,
    0.21: 1230092.227205,
    0.22: 1078374.927845,
    0.23: 940946.294027,
    0.24: 817645.157381,
    0.25: 708056.855687,
    0.26: 611553.461939,
    0.27: 527336.119660,
    0.28: 454477.821846,
    0.29: 391965.101933,
    0.30: 338737.289342,
    0.31: 293722.207146,
    0.32: 255867.441948,
    0.33: 224166.582471,
    0.34: 197680.090241,
    0.35: 175550.720975,
    0.36: 157013.647914,
    0.37: 141401.639792,
    0.38: 128145.809891,
    0.39: 116772.574646,
    0.40: 106897.539087,
    0.41: 98217.062666,
    0.42: 90498.255663,
    0.43: 83568.117952,
    0.44: 77302.464299,
    0.45: 71615.190565,
    0.46: 66448.330202,
    0.47: 61763.237802,
    0.48: 57533.122797,
    0.49: 53737.048034,
    0.50: 50355.409866,
}

# Cromer-Mann f(0) values for each element
CM_F0 = {
    "C": 0.215600 + 2.310000 + 1.020000 + 1.588600 + 0.865000,
    "N": -11.529000 + 12.212600 + 3.132200 + 2.012500 + 1.166300,
    "O": 0.250800 + 3.048500 + 2.286800 + 1.546300 + 0.867000,
    "S": 0.866900 + 6.905300 + 5.203400 + 1.437900 + 1.586300,
}


def parse_experimental_data():
    """Parse experimental_saxs.dat, return (q_list, iq_list, sigma_list)."""
    exp_path = Path("/app/experimental_saxs.dat")
    q_vals, iq_vals, sig_vals = [], [], []
    with open(exp_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            q_vals.append(float(parts[0]))
            iq_vals.append(float(parts[1]))
            sig_vals.append(float(parts[2]))
    return q_vals, iq_vals, sig_vals


def test_saxs_profile_exists():
    """Verify saxs_profile.txt exists with correct format (4 columns)."""
    profile_path = Path("/app/saxs_profile.txt")
    assert profile_path.exists(), f"{profile_path} does not exist."

    with open(profile_path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    exp_q, _, _ = parse_experimental_data()
    assert len(lines) == len(exp_q), (
        f"Expected {len(exp_q)} data lines in saxs_profile.txt, got {len(lines)}"
    )

    for i, line in enumerate(lines):
        parts = line.split()
        assert len(parts) == 5, (
            f"Line {i+1}: expected 5 columns (q, I_model, I_scaled, I_exp, sigma), "
            f"got {len(parts)}: '{line}'"
        )
        try:
            for p in parts:
                float(p)
        except ValueError:
            raise AssertionError(
                f"Line {i+1}: non-numeric values: '{line}'"
            )


def test_saxs_model_accuracy():
    """Verify I_model(q) values match reference within 0.5% tolerance."""
    profile_path = Path("/app/saxs_profile.txt")

    with open(profile_path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    for line in lines:
        parts = line.split()
        q = float(parts[0])
        i_model = float(parts[1])

        q_key = round(q, 6)
        if q_key in REFERENCE_IQ:
            ref = REFERENCE_IQ[q_key]
            rel_err = abs(i_model - ref) / ref
            assert rel_err < 0.005, (
                f"I_model({q:.3f}) = {i_model:.6f} deviates from reference "
                f"{ref:.6f} (relative error {rel_err:.6f}, must be < 0.5%)"
            )


def test_scale_factor_and_chi2():
    """Verify scale factor and chi-squared are physically reasonable."""
    profile_path = Path("/app/saxs_profile.txt")
    fit_path = Path("/app/guinier_fit.txt")

    # Read profile data
    q_vals, i_model, i_scaled, i_exp, sig = [], [], [], [], []
    with open(profile_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            q_vals.append(float(parts[0]))
            i_model.append(float(parts[1]))
            i_scaled.append(float(parts[2]))
            i_exp.append(float(parts[3]))
            sig.append(float(parts[4]))

    # Verify scaled column: i_scaled should be proportional to i_model
    ratios = []
    for im, isc in zip(i_model, i_scaled):
        if im > 0:
            ratios.append(isc / im)

    # All ratios should be very close (same scale factor)
    k = sum(ratios) / len(ratios)
    for r in ratios:
        assert abs(r - k) / k < 0.01, (
            f"Scale factor inconsistency: ratios vary from {min(ratios):.6f} "
            f"to {max(ratios):.6f} (expected constant {k:.6f})"
        )

    # Scale factor should be positive and reasonable
    assert 0.5 < k < 2.0, (
        f"Scale factor k = {k:.6f} outside expected range (0.5-2.0)"
    )

    # Compute chi^2 from the data
    chi2 = sum(
        (isc - ie) ** 2 / (s ** 2)
        for isc, ie, s in zip(i_scaled, i_exp, sig)
        if s > 0
    )
    n_points = len(q_vals)
    reduced_chi2 = chi2 / (n_points - 1)

    # With ~3% noise, reduced chi2 should be around 1.0 (0.5 to 3.0)
    assert 0.3 < reduced_chi2 < 5.0, (
        f"Reduced chi^2 = {reduced_chi2:.4f} outside expected range (0.3-5.0). "
        f"Expected ~1.0 for a good fit with correct error estimates."
    )

    # Read reported values
    with open(fit_path, "r") as f:
        parts = f.read().strip().split()
    reported_rg = float(parts[0])
    reported_i0 = float(parts[1])
    reported_chi2 = float(parts[2])

    # Reported chi^2 should match computed chi^2
    assert abs(reported_chi2 - reduced_chi2) < 0.001, (
        f"Reported reduced chi^2 ({reported_chi2:.6f}) does not match "
        f"computed value ({reduced_chi2:.6f})"
    )


def test_guinier_fit_accuracy():
    """Verify Guinier parameters are self-consistent."""
    fit_path = Path("/app/guinier_fit.txt")
    profile_path = Path("/app/saxs_profile.txt")

    with open(fit_path, "r") as f:
        parts = f.read().strip().split()
    rg = float(parts[0])
    i0 = float(parts[1])

    # Read scaled I(q) values (column 3)
    q_vals = []
    iq_scaled = []
    with open(profile_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            q_vals.append(float(parts[0]))
            iq_scaled.append(float(parts[2]))

    # Rg range check for crambin
    assert 9.0 < rg < 10.3, (
        f"Rg = {rg:.3f} Å outside expected range (9.0-10.3 Å) for crambin"
    )

    # I(0) should be reasonable
    assert 2.0e6 < i0 < 8.0e6, (
        f"I(0) = {i0:.2f} outside expected range (2-8 million)"
    )

    # Check Guinier linearity using reported Rg
    q_cutoff = 1.3 / rg
    guinier_points = [
        (q, iq) for q, iq in zip(q_vals, iq_scaled)
        if q < q_cutoff and iq > 0
    ]

    assert len(guinier_points) >= 5, (
        f"Only {len(guinier_points)} points in Guinier region (q < {q_cutoff:.4f})"
    )

    # Linear regression of ln(I) vs q^2
    x_vals = [q * q for q, _ in guinier_points]
    y_vals = [math.log(iq) for _, iq in guinier_points]
    n = len(x_vals)

    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n
    ss_xx = sum((x - x_mean) ** 2 for x in x_vals)
    ss_yy = sum((y - y_mean) ** 2 for y in y_vals)
    ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))

    slope = ss_xy / ss_xx
    intercept = y_mean - slope * x_mean
    r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0

    assert r_squared > 0.98, (
        f"Guinier fit R^2 = {r_squared:.4f} too low (< 0.98)"
    )

    rg_from_slope = math.sqrt(-3.0 * slope)
    assert abs(rg_from_slope - rg) < 0.2, (
        f"Reported Rg ({rg:.3f}) inconsistent with Guinier slope "
        f"(implies Rg = {rg_from_slope:.3f})"
    )

    i0_from_intercept = math.exp(intercept)
    assert abs(i0_from_intercept - i0) / i0 < 0.03, (
        f"Reported I(0) ({i0:.2f}) inconsistent with Guinier intercept "
        f"(implies I(0) = {i0_from_intercept:.2f})"
    )


def test_saxs_form_factor_check():
    """Verify I_model(0) is consistent with the sum of atomic form factors squared."""
    profile_path = Path("/app/saxs_profile.txt")

    # Read I_model values
    q_vals, i_model = [], []
    with open(profile_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            q_vals.append(float(parts[0]))
            i_model.append(float(parts[1]))

    # Count atoms by element from the PDB
    element_counts = {}
    pdb_path = Path("/app/1crn.pdb")
    with open(pdb_path, "r") as f:
        for line in f:
            if line.startswith("ATOM") or line.startswith("HETATM"):
                elem = line[76:78].strip()
                if not elem:
                    aname = line[12:16].strip()
                    if aname and aname[0] in "CNOS":
                        elem = aname[0]
                    else:
                        continue
                element_counts[elem] = element_counts.get(elem, 0) + 1

    f0_total = sum(
        count * CM_F0.get(elem, 0.0)
        for elem, count in element_counts.items()
    )
    expected_i0 = f0_total * f0_total

    # I_model at smallest q should approach I(0)
    i_model_qmin = i_model[0]

    # At q = 0.01 A^{-1}, I(q) / I(0) ~ 0.996 for Rg ~ 9.7 A
    # So I_model(qmin) should be close to expected_i0
    rel_err = abs(i_model_qmin - expected_i0) / expected_i0
    assert rel_err < 0.02, (
        f"I_model(q_min) = {i_model_qmin:.1f} deviates from expected "
        f"I(0) = {expected_i0:.1f} (relative error {rel_err:.4f}). "
        f"Check Debye formula implementation."
    )

    # Also check that I_model is monotonically decreasing
    for i in range(1, len(i_model)):
        assert i_model[i] <= i_model[i-1] * 1.001, (
            f"I_model not monotonically decreasing at q={q_vals[i]:.3f}"
        )
