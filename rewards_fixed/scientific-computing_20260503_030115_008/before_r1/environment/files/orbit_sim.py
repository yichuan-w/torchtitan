"""
Orbital Mechanics Simulation - Two-Body Problem

Simulates a satellite in low Earth orbit using gravitational dynamics.
Uses a fixed-step RK4 integrator with a large time step for efficiency.
"""

import numpy as np
import matplotlib.pyplot as plt
import argparse
from scipy.integrate import solve_ivp

# Physical constants
MU = 3.986004418e14  # Earth's gravitational parameter (m^3/s^2)
R_EARTH = 6.371e6    # Earth's radius (m)

# Initial conditions: 400 km altitude circular orbit
ALTITUDE = 400e3  # meters
R0 = R_EARTH + ALTITUDE  # initial radius

# Circular orbit velocity
V0 = np.sqrt(MU / R0)

# Initial state: [x, y, vx, vy] - starting at positive x-axis
# For circular orbit, velocity is purely tangential (in y-direction)
STATE0 = np.array([R0, 0.0, 0.0, V0])

# Orbital period
T_ORBIT = 2 * np.pi * np.sqrt(R0**3 / MU)

# Fixed timestep (300 seconds = 5 minutes) - larger for faster simulation
# This timestep is chosen for computational efficiency but may not be ideal
# for long-term accuracy
DT = 300.0

# Scheduled finite maneuver: 25 m/s prograde at half an orbit.
BURN_START = T_ORBIT / 2
BURN_DURATION = 1.0
BURN_ACCEL = 25.0


def acceleration(state):
    """Compute gravitational acceleration."""
    x, y, vx, vy = state
    r = np.sqrt(x**2 + y**2)
    ax = -MU * x / r**3
    ay = -MU * y / r**3
    return np.array([vx, vy, ax, ay])


def maneuver_dynamics(t, state):
    """Two-body dynamics with the scheduled finite maneuver."""
    derivative = acceleration(state)
    if BURN_START <= t <= BURN_START + BURN_DURATION:
        speed = np.hypot(state[2], state[3])
        derivative[2:] += BURN_ACCEL * state[2:] / speed
    return derivative


def rk4_step(state, dt):
    """Perform one RK4 integration step."""
    k1 = acceleration(state)
    k2 = acceleration(state + 0.5 * dt * k1)
    k3 = acceleration(state + 0.5 * dt * k2)
    k4 = acceleration(state + dt * k3)
    return state + (dt / 6.0) * (k1 + 2*k2 + 2*k3 + k4)


def compute_energy(state):
    """
    Compute total mechanical energy per unit mass.
    
    E = v^2/2 - mu/r
    
    For a bound orbit, E < 0.
    """
    x, y, vx, vy = state
    r = np.sqrt(x**2 + y**2)
    v_squared = vx**2 + vy**2
    
    kinetic = v_squared / 2
    potential = -MU / r
    
    return kinetic + potential


def compute_angular_momentum(state):
    """Compute specific angular momentum h = r x v (z-component for 2D)."""
    x, y, vx, vy = state
    return x * vy - y * vx


def compute_semi_major_axis(energy):
    """
    Compute semi-major axis from energy.
    
    a = -mu / (2*E)
    """
    return -MU / (2 * energy)


def run_simulation(n_periods=10, dt=None):
    """Run the orbital simulation for n orbital periods."""
    if dt is None:
        dt = DT
    
    t_end = n_periods * T_ORBIT
    times = np.arange(0, t_end, dt)
    result = solve_ivp(maneuver_dynamics, (0, t_end), STATE0, t_eval=times)
    print(f"SCIPY-SOLVE-IVP internal steps: {result.nfev}")
    return result.y.T


def plot_results(states, dt=None):
    """Plot the orbit trajectory and energy over time."""
    if dt is None:
        dt = DT
    
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    
    # Extract data
    x = states[:, 0]
    y = states[:, 1]
    vx = states[:, 2]
    vy = states[:, 3]
    n_steps = len(states)
    t = np.arange(n_steps) * dt
    
    # Plot 1: Orbit trajectory
    ax1 = axes[0, 0]
    ax1.plot(x / 1e6, y / 1e6, 'b-', linewidth=0.5)
    ax1.plot(0, 0, 'ko', markersize=10, label='Earth')
    circle = plt.Circle((0, 0), R_EARTH / 1e6, color='green', fill=True, alpha=0.3)
    ax1.add_patch(circle)
    ax1.set_xlabel('X (1000 km)')
    ax1.set_ylabel('Y (1000 km)')
    ax1.set_title('Orbit Trajectory')
    ax1.set_aspect('equal')
    ax1.grid(True)
    ax1.legend()
    
    # Plot 2: Energy over time
    ax2 = axes[0, 1]
    energies = np.array([compute_energy(states[i]) for i in range(n_steps)])
    E0 = compute_energy(STATE0)
    energy_error = (energies - E0) / abs(E0) * 100  # percent error
    ax2.plot(t / 3600, energy_error, 'r-')
    ax2.set_xlabel('Time (hours)')
    ax2.set_ylabel('Energy Error (%)')
    ax2.set_title('Energy Conservation')
    ax2.grid(True)
    
    # Plot 3: Semi-major axis over time
    ax3 = axes[1, 0]
    semi_major = np.array([compute_semi_major_axis(e) for e in energies])
    a0 = compute_semi_major_axis(E0)
    ax3.plot(t / 3600, semi_major / 1e6, 'g-')
    ax3.axhline(y=a0 / 1e6, color='k', linestyle='--', label=f'Initial a = {a0/1e6:.1f} km')
    ax3.set_xlabel('Time (hours)')
    ax3.set_ylabel('Semi-major Axis (1000 km)')
    ax3.set_title('Semi-major Axis')
    ax3.grid(True)
    ax3.legend()
    
    # Plot 4: Angular momentum over time
    ax4 = axes[1, 1]
    h = np.array([compute_angular_momentum(states[i]) for i in range(n_steps)])
    h0 = compute_angular_momentum(STATE0)
    h_error = (h - h0) / h0 * 100
    ax4.plot(t / 3600, h_error, 'm-')
    ax4.set_xlabel('Time (hours)')
    ax4.set_ylabel('Angular Momentum Error (%)')
    ax4.set_title('Angular Momentum Conservation')
    ax4.grid(True)
    
    plt.tight_layout()
    plt.savefig('/app/orbit_plot.png', dpi=150)
    print(f"Plot saved to /app/orbit_plot.png")
    
    # Print summary statistics
    print(f"\nSimulation Summary:")
    print(f"Time step: {dt} seconds")
    print(f"Number of steps: {n_steps}")
    print(f"Initial Energy: {E0:.6e} J/kg")
    print(f"Final Energy: {energies[-1]:.6e} J/kg")
    print(f"Energy Drift: {abs(energy_error[-1]):.6f}%")
    print(f"Max Energy Error: {np.max(np.abs(energy_error)):.6f}%")
    print(f"Initial Semi-major Axis: {a0/1e6:.1f} km")
    print(f"Final Semi-major Axis: {semi_major[-1]/1e6:.1f} km")
    print(f"Initial Angular Momentum: {h0:.6e} m²/s")
    print(f"Final Angular Momentum: {h[-1]:.6e} m²/s")
    print(f"Final Semi-major Axis Change: {(semi_major[-1] - a0)/1e3:.3f} km")


def main():
    parser = argparse.ArgumentParser(description='Orbital mechanics simulation')
    parser.add_argument('--periods', type=int, default=10,
                        help='Number of orbital periods to simulate')
    parser.add_argument('--dt', type=float, default=DT,
                        help='Time step (seconds)')
    args = parser.parse_args()
    
    print(f"Simulating {args.periods} orbital periods...")
    print(f"Orbital period: {T_ORBIT/60:.1f} minutes")
    print(f"Time step: {args.dt} seconds")
    
    states = run_simulation(n_periods=args.periods, dt=args.dt)
    plot_results(states, dt=args.dt)


if __name__ == '__main__':
    main()
