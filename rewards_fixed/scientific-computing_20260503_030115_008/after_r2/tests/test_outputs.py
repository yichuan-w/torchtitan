import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np


MU = 3.986004418e14
R_EARTH = 6.371e6
ALTITUDE = 400e3
R0 = R_EARTH + ALTITUDE
V0 = np.sqrt(MU / R0)
T_ORBIT = 2 * np.pi * np.sqrt(R0**3 / MU)
STATE0 = np.array([R0, 0.0, 0.0, V0])
DT = 300.0
BURN_START = T_ORBIT / 2
BURN_DURATION = 1.0
DELIVERABLE = Path("/app/orbit_sim_fixed.py")


def load_and_run():
    file_present = DELIVERABLE.is_file()
    required = (
        "run_simulation",
        "compute_energy",
        "compute_angular_momentum",
        "compute_semi_major_axis",
    )
    module = None
    cli = None
    if file_present:
        spec = importlib.util.spec_from_file_location("orbit_sim_fixed", DELIVERABLE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cli = subprocess.run(
            [sys.executable, str(DELIVERABLE), "--periods", "1", "--dt", "300"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd="/app",
        )
    assert file_present and all(callable(getattr(module, name, None)) for name in required) and cli.returncode == 0, \
        f"The required file, four public functions, and CLI arguments must work: {cli.stderr if cli else ''}"

    states = np.asarray(module.run_simulation(n_periods=10, dt=DT), dtype=float)
    return module, states


def relative_span(values):
    return np.max(np.abs((values - values[0]) / values[0])) * 100


def main():
    module, states = load_and_run()
    energies = np.array([module.compute_energy(state) for state in states], dtype=float)
    axes = np.array([module.compute_semi_major_axis(value) for value in energies], dtype=float)
    momenta = np.array([module.compute_angular_momentum(state) for state in states], dtype=float)

    # run_simulation returns states, not timestamps.  Locate the scheduled burn
    # from the independently computed physical-energy jump it produces instead
    # of assuming one row per `dt` or trusting a submitted diagnostic function.
    physical_energies = np.sum(states[:, 2:] ** 2, axis=1) / 2 - MU / np.linalg.norm(states[:, :2], axis=1)
    burn_boundary = int(np.argmax(np.abs(np.diff(physical_energies)))) + 1
    coast_slices = (slice(None, burn_boundary), slice(burn_boundary, None))

    energy_errors = [relative_span(energies[coast]) for coast in coast_slices]
    assert all(error < 0.5 for error in energy_errors), \
        f"Coast-interval energy errors {energy_errors} must each be below 0.5%"

    axis_errors = [relative_span(axes[coast]) for coast in coast_slices]
    assert all(error < 1.0 for error in axis_errors), \
        f"Coast-interval semi-major-axis errors {axis_errors} must each be below 1%"

    momentum_errors = [relative_span(momenta[coast]) for coast in coast_slices]
    assert all(error < 0.01 for error in momentum_errors), \
        f"Coast-interval angular-momentum errors {momentum_errors} must each be below 0.01%"

    final = states[-1]
    final_energy = (final[2] ** 2 + final[3] ** 2) / 2 - MU / np.hypot(final[0], final[1])
    final_axis = -MU / (2 * final_energy)
    axis_increase_km = (final_axis - R0) / 1000
    assert abs(axis_increase_km - 44.49) <= 0.10, \
        f"Final semi-major-axis increase {axis_increase_km:.6f} km is not 44.49 +/- 0.10 km"


if __name__ == "__main__":
    main()
