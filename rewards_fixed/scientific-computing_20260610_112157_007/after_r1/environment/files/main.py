import numpy as np
import json
from simulation import solve_schrodinger


def main():
    """Solve for a finite square well and output results."""
    L = 10.0
    N = 500
    x = np.linspace(-L / 2, L / 2, N)

    # Finite square well: V = -V0 inside the well, 0 outside
    V0 = 50.0
    a = 1.0  # half-width
    V = np.where(np.abs(x) < a, V0, 0.0)

    n_states = 3
    eigenvalues, wavefunctions = solve_schrodinger(x, V, n_states)

    results = {
        "energies": eigenvalues.tolist(),
        "x": x.tolist(),
        "wavefunctions": [wf.tolist() for wf in wavefunctions.T],
    }

    print("Schrodinger Equation Solver")
    print(f"Potential: finite square well, V0={V0}, a={a}")
    print(f"Grid: N={N}, L={L}")
    print()

    for i, E in enumerate(eigenvalues):
        print(f"State {i}: E = {E:.6f}")

    with open("/app/results.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\nResults written to /app/results.json")


if __name__ == "__main__":
    main()