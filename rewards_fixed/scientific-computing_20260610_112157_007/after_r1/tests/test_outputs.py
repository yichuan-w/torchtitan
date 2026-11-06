import json
from pathlib import Path
import importlib.util
import numpy as np


def load_module():
    spec = importlib.util.spec_from_file_location(
        "simulation", "/app/simulation.py"
    )
    sim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sim)
    return sim


def analytic_finite_well(V0, a, n=3):
    """
    Solve the finite square well analytically using the transcendental equations.
    Returns the first n bound state energies.
    V0 > 0 is well depth, a is half-width. hbar = m = 1.
    """
    energies = []
    # Solve for even and odd states by root-finding
    for parity in [0, 1]:  # 0 = even, 1 = odd
        k0 = np.sqrt(2 * V0)
        for guess in np.linspace(0.1, k0 - 0.01, 500):
            k = guess
            q = np.sqrt(2 * V0 - k * k)
            if parity == 0:
                f = k * np.tan(k * a) - q
            else:
                f = k / np.tan(k * a) + q
            if len(energies) >= n:
                break
            # Look for sign change
            if guess > 0.1:
                k_prev = guess - (k0 - 0.01) / 499
                q_prev = np.sqrt(2 * V0 - k_prev * k_prev)
                if parity == 0:
                    f_prev = k_prev * np.tan(k_prev * a) - q_prev
                else:
                    f_prev = k_prev / np.tan(k_prev * a) + q_prev
                if f * f_prev < 0:
                    # Found a root
                    k_root = (guess + k_prev) / 2  # approximate
                    E = -V0 + k_root * k_root / 2
                    if E < 0 and all(abs(E - e) > 0.1 for e in energies):
                        energies.append(E)
    return sorted(energies)[:n]


def test_results_file_exists():
    result_path = Path("/app/results.json")
    assert result_path.exists(), (
        f"File {result_path} does not exist."
    )

    with open(result_path, "r") as f:
        data = json.load(f)

    for key in ["energies", "x", "wavefunctions"]:
        assert key in data, f"results.json missing '{key}' key"

    assert len(data["energies"]) >= 3, (
        f"Expected at least 3 energies, got {len(data['energies'])}"
    )
    assert len(data["wavefunctions"]) >= 3, (
        f"Expected at least 3 wavefunctions, got {len(data['wavefunctions'])}"
    )

    for i, (E, wf) in enumerate(zip(data["energies"], data["wavefunctions"])):
        assert isinstance(E, (int, float)), f"Energy[{i}] is not a number"
        assert len(wf) == len(data["x"]), (
            f"Wavefunction[{i}] length mismatch: {len(wf)} vs {len(data['x'])}"
        )


def test_ground_state_energy():
    """The ground state energy should be near -49.0 for V0=50, a=1."""
    sim = load_module()

    L = 10.0
    N = 500
    x = np.linspace(-L/2, L/2, N)
    V0 = 50.0
    a = 1.0
    V = np.where(np.abs(x) < a, -V0, 0.0)

    eigenvalues, _ = sim.solve_schrodinger(x, V, n_states=3)

    E0 = eigenvalues[0]
    assert -50.0 < E0 < -47.0, (
        f"Ground state energy should be near -49.0, got {E0:.4f}. "
        "Check the kinetic energy finite-difference coefficients."
    )


def test_bound_states_negative():
    """The first 3 states should all be bound (negative energies near expected values)."""
    sim = load_module()

    L = 10.0
    N = 500
    x = np.linspace(-L/2, L/2, N)
    V0 = 50.0
    a = 1.0
    V = np.where(np.abs(x) < a, -V0, 0.0)

    eigenvalues, wavefunctions = sim.solve_schrodinger(x, V, n_states=5)

    # First 3 states must be negative and in a reasonable range
    for i in range(3):
        assert -50.0 < eigenvalues[i] < 0, (
            f"State {i} energy = {eigenvalues[i]:.4f} should be between -50 and 0"
        )

    # Energies should be strictly increasing
    assert eigenvalues[0] < eigenvalues[1] < eigenvalues[2] < eigenvalues[3] < eigenvalues[4], (
        "Eigenvalues should be in strictly ascending order"
    )


def test_wavefunction_normalization():
    """Each wavefunction should be normalized: integral |psi|^2 dx = 1."""
    sim = load_module()

    L = 10.0
    N = 500
    x = np.linspace(-L/2, L/2, N)
    V0 = 50.0
    a = 1.0
    V = np.where(np.abs(x) < a, -V0, 0.0)

    _, wavefunctions = sim.solve_schrodinger(x, V, n_states=3)

    for i in range(3):
        norm = np.trapezoid(wavefunctions[:, i]**2, x)
        assert 0.99 < norm < 1.01, (
            f"Wavefunction {i} norm = {norm:.4f}, expected ~1.0. "
            "Check the normalization formula in solve_schrodinger."
        )


def test_results_file_energies():
    """Results file should have correct bound state energies."""
    result_path = Path("/app/results.json")
    with open(result_path, "r") as f:
        data = json.load(f)

    energies = data["energies"]
    assert len(energies) == 3, f"Expected 3 energies, got {len(energies)}"

    # All energies should be negative (bound states)
    for i, E in enumerate(energies):
        assert E < 0, f"Energy[{i}] = {E:.4f} should be negative (bound state)"

    # Ground state near -49.0
    assert -50.0 < energies[0] < -47.0, (
        f"Ground state energy should be near -49.0, got {energies[0]:.4f}"
    )

    # Energies should be increasing
    assert energies[0] < energies[1] < energies[2], (
        "Energies should be strictly increasing"
    )