#!/usr/bin/env python3
"""
Alternative solver using explicit Euler method.
This is a simple implementation for debugging purposes.
"""

import json
import numpy as np

def load_parameters(filepath):
    """Load reaction parameters from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)

def euler_step(y, dt, k1, k2, k3):
    """Single explicit Euler step."""
    y1, y2, y3 = y
    
    dy1 = -k1*y1 + k3*y2*y3
    dy2 = k1*y1 - k2*y2**2 - k3*y2*y3
    dy3 = k2*y2**2
    
    return [y1 + dt*dy1, y2 + dt*dy2, y3 + dt*dy3]

def run_euler_simulation():
    """Run simulation using explicit Euler method."""
    params = load_parameters('/app/kinetics/reaction_params.json')
    k1, k2, k3 = params['k1'], params['k2'], params['k3']
    
    y = [1.0, 0.0, 0.0]
    t = 0.0
    t_end = 1e5
    
    # Small time step for stability
    dt = 1e-10
    
    print("Starting Euler simulation...")
    print(f"dt = {dt}")
    
    while t < t_end:
        y = euler_step(y, dt, k1, k2, k3)
        t += dt
        
        if t % 10000 < dt:
            print(f"t = {t:.1f}, y = [{y[0]:.4e}, {y[1]:.4e}, {y[2]:.4e}]")
    
    print(f"\nFinal: t = {t}, y = {y}")
    return y

if __name__ == '__main__':
    run_euler_simulation()
