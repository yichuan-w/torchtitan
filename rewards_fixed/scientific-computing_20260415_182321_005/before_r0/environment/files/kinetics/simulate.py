#!/usr/bin/env python3
"""
Chemical kinetics simulation for three-species reaction system.
This script simulates a system of coupled ODEs describing chemical reactions.
"""

import json
import configparser
import numpy as np
from scipy.integrate import solve_ivp

def load_parameters(filepath):
    """Load reaction parameters from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)

def load_config(filepath):
    """Load configuration from INI file."""
    config = configparser.ConfigParser()
    config.read(filepath)
    return config

def robertson_system(t, y, k1, k2, k3):
    """
    Three-species chemical kinetics problem.
    
    Reactions:
        y1 -> y2       (rate k1*y1)
        2*y2 -> y3+y2  (rate k2*y2^2)
        y2 + y3 -> y1  (rate k3*y2*y3)
    
    dy1/dt = -k1*y1 + k3*y2*y3
    dy2/dt = k1*y1 - k2*y2^2 - k3*y2*y3
    dy3/dt = k2*y2^2
    """
    y1, y2, y3 = y
    
    # Handle potential numerical issues with very small values
    y1 = max(y1, 0.0)
    y2 = max(y2, 0.0)
    y3 = max(y3, 0.0)
    
    dy1 = -k1*y1 + k3*y2*y3
    dy2 = k1*y1 - k2*y2**2 - k3*y2*y3
    dy3 = k2*y2**2
    
    return [dy1, dy2, dy3]

def run_simulation():
    """Run the chemical kinetics simulation."""
    # Load parameters
    params = load_parameters('/app/kinetics/reaction_params.json')
    k1 = params['k1']
    k2 = params['k2']
    k3 = params['k3']
    
    # Load configuration
    config = load_config('/app/kinetics/config.ini')
    rtol = config.getfloat('solver', 'rtol', fallback=1e-3)
    atol = config.getfloat('solver', 'atol', fallback=1e-6)
    
    # Initial conditions
    y0 = [1.0, 0.0, 0.0]
    
    # Time span (0 to 100,000 seconds)
    t_span = (0.0, 1e5)
    t_eval = np.linspace(0, 1e5, 1001)
    
    # Solve the ODE system
    print("Starting simulation...")
    print(f"Parameters: k1={k1}, k2={k2}, k3={k3}")
    print(f"Initial conditions: y1={y0[0]}, y2={y0[1]}, y3={y0[2]}")
    print(f"Time span: {t_span[0]} to {t_span[1]} seconds")
    print(f"Tolerances: rtol={rtol}, atol={atol}")
    
    solution = solve_ivp(
        robertson_system,
        t_span,
        y0,
        t_eval=t_eval,
        args=(k1, k2, k3),
        rtol=rtol,
        atol=atol
    )
    
    if not solution.success:
        print(f"Simulation failed: {solution.message}")
        return None
    
    print(f"Simulation completed successfully!")
    print(f"Number of function evaluations: {solution.nfev}")
    
    # Save results
    np.savez(
        '/app/kinetics/results.npz',
        t=solution.t,
        y1=solution.y[0],
        y2=solution.y[1],
        y3=solution.y[2]
    )
    print("Results saved to /app/kinetics/results.npz")
    
    # Print final concentrations
    print("\nFinal concentrations at t=100,000 seconds:")
    print(f"  y1 (species A): {solution.y[0][-1]:.8e}")
    print(f"  y2 (species B): {solution.y[1][-1]:.8e}")
    print(f"  y3 (species C): {solution.y[2][-1]:.8e}")
    
    # Verify mass conservation
    total_mass = solution.y[0][-1] + solution.y[1][-1] + solution.y[2][-1]
    print(f"\nMass conservation check: y1+y2+y3 = {total_mass:.8e}")
    if abs(total_mass - 1.0) < 0.01:
        print("Mass conservation maintained.")
    else:
        print("WARNING: Mass conservation violated!")
    
    return solution

if __name__ == '__main__':
    run_simulation()
