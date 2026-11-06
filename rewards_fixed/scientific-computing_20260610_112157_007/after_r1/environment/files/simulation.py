import numpy as np
import json


def numerov_integrate(k, dx):
    """
    Integrate psi'' = -k(x) psi using the Numerov scheme:
    psi_{n+1} = [2(1 - 5h^2 k_n/12) psi_n - (1 + h^2 k_{n-1}/12) psi_{n-1}]
                / (1 + h^2 k_{n+1}/12)
    with psi[0] = 0, psi[1] = 1e-3.
    """
    n = len(k)
    psi = np.zeros(n)
    psi[0] = 0.0
    psi[1] = 1e-3
    h2 = dx * dx
    for i in range(1, n - 1):
        num = (2.0 * (1.0 - 5.0 * h2 * k[i] / 12.0) * psi[i]
               - (1.0 + h2 * k[i - 1] / 12.0) * psi[i - 1])
        den = 1.0 + h2 * k[i + 1] / 12.0
        psi[i + 1] = num / den
    return psi


def count_nodes(psi):
    """Count sign changes (nodes) in the interior of the wavefunction."""
    s = np.sign(psi)
    s = s[s != 0]
    return int(np.sum(s[1:] * s[:-1] < 0))


def find_eigenvalue(V, x, n_nodes, E_min, E_max, tol=1e-10):
    """
    Find the bound-state eigenvalue whose wavefunction has n_nodes nodes.
    Node count increases monotonically with E, so bisect on it.
    """
    dx = x[1] - x[0]
    E_left, E_right = E_min, E_max

    for _ in range(200):
        E_mid = 0.5 * (E_left + E_right)
        k = 2.0 * (E_mid - V)
        nodes = count_nodes(numerov_integrate(k, dx))
        if nodes <= n_nodes:
            E_right = E_mid
        else:
            E_left = E_mid
        if E_right - E_left < tol:
            break

    E = 0.5 * (E_left + E_right)
    k = 2.0 * (E - V)

    # Two-sided integration to avoid the exponentially growing
    # contamination in the classically forbidden regions.
    psi_L = numerov_integrate(k, dx)
    psi_R = numerov_integrate(k[::-1], dx)[::-1]

    inside = np.flatnonzero(V < 0.0)
    valid = inside[np.abs(psi_R[inside]) > 1e-300]
    m = valid[np.argmax(np.abs(psi_L[valid]))]
    scale = psi_L[m] / psi_R[m]
    psi = np.concatenate([psi_L[:m], scale * psi_R[m:]])

    # Normalize: integral |psi|^2 dx = 1
    norm = np.sqrt(np.trapezoid(psi**2, x))
    psi /= norm

    # Fix overall sign: positive at its maximum
    if psi[np.argmax(np.abs(psi))] < 0:
        psi = -psi

    return E, psi


def solve_schrodinger(x, V, n_states=3):
    """Find the n_states lowest eigenvalues using the shooting method."""
    V_min = np.min(V)
    eigenvalues = []
    wavefunctions = []

    E_max = 0.0   # bound states have E < 0
    E_min = V_min  # and E > min(V)

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
    main()
