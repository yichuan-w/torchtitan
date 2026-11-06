import numpy as np
import json


def numerov_step(psi_prev, psi_curr, k_prev, k_curr, k_next, dx):
    """
    One step of the Numerov integration:
    psi_{n+1} = [2(1 - 5h^2 k_n / 12) psi_n - (1 + h^2 k_{n-1} / 12) psi_{n-1}] / (1 + h^2 k_{n+1} / 12)
    where k(x) = 2m/hbar^2 * (E - V(x))
    """
    h = dx
    h2 = h * h
    num = 2.0 * (1.0 - 5.0 * h2 * k_curr / 12.0) * psi_curr - (1.0 + h2 * k_prev / 12.0) * psi_prev
    den = 1.0 + h2 * k_next / 12.0
    return num / den


def integrate_outward(V, x, E):
    """Integrate the Schrodinger equation outward from the left boundary."""
    dx = x[1] - x[0]
    n = len(x)
    psi = np.zeros(n)
    psi[0] = 0.0
    psi[1] = 1e-3

    k = lambda xi: 2.0 * (E - np.interp(xi, x, V))

    for i in range(1, n - 1):
        k_prev = k(x[i - 1])
        k_curr = k(x[i])
        k_next = k(x[i + 1])
        psi[i + 1] = numerov_step(psi[i - 1], psi[i], k_prev, k_curr, k_next, dx)

    return psi


def count_nodes(psi):
    """Count sign changes in the wavefunction."""
    nodes = 0
    for i in range(1, len(psi)):
        if psi[i - 1] * psi[i] < 0:
            nodes += 1
    return nodes


def find_eigenvalue(V, x, n_nodes, E_min, E_max, tol=1e-6):
    """
    Find the eigenvalue with n_nodes using bisection.
    Returns the eigenvalue and corresponding wavefunction.
    """
    E_left = E_min
    E_right = E_max

    for iteration in range(100):
        E_mid = (E_left + E_right) / 2.0
        psi = integrate_outward(V, x, E_mid)
        nodes = count_nodes(psi)

        if nodes <= n_nodes:
            E_right = E_mid
        else:
            E_left = E_mid

        if E_right - E_left < tol:
            break

    E = (E_left + E_right) / 2.0
    psi = integrate_outward(V, x, E)

    # Normalize
    dx = x[1] - x[0]
    norm = np.sqrt(np.trapz(psi**2, x))
    psi /= norm

    return E, psi


def solve_schrodinger(x, V, n_states=3):
    """Find the n_states lowest eigenvalues using the shooting method."""
    V_min = np.min(V)
    eigenvalues = []
    wavefunctions = []

    E_max = 0.0  # upper bound for bound states
    E_min = V_min  # lower bound

    for n in range(n_states):
        E, psi = find_eigenvalue(V, x, n, E_min, E_max)
        eigenvalues.append(E)
        wavefunctions.append(psi)

    return np.array(eigenvalues), np.column_stack(wavefunctions)


def main():
    L = 10.0
    N = 500
    x = np.linspace(-L / 2, L / 2, N)

    V0 = 50.0
    a = 1.0
    V = np.where(np.abs(x) < a, -V0, 0.0)

    eigenvalues, wavefunctions = solve_schrodinger(x, V, n_states=3)

    results = {
        "energies": eigenvalues.tolist(),
        "x": x.tolist(),
        "wavefunctions": [wf.tolist() for wf in wavefunctions.T],
    }

    for i, E in enumerate(eigenvalues):
        print(f"State {i}: E = {E:.6f}")

    with open("/app/results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    np.random.seed(42)
    main()