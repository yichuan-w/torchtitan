"""
Sensor Calibration Pipeline

This script performs calibration of temperature sensor response curves.
The sensors have a known exponential decay response that must be characterized
for accurate temperature readings.

Usage:
    python calibrate.py

Output:
    Prints calibrated parameters for each sensor and saves to calibration_results.json
"""

import json
import pandas as pd
import numpy as np
from scipy.optimize import curve_fit
from pathlib import Path

# Import data utilities
from data_utils import load_calibration_data


def exponential_response(t, T_initial, T_final, tau):
    """
    Exponential decay response model for temperature sensors.
    
    T(t) = T_final + (T_initial - T_final) * exp(-t / tau)
    
    Parameters:
        t: time in seconds
        T_initial: initial temperature reading (asymptotic at t=0)
        T_final: final equilibrium temperature
        tau: time constant in seconds
    """
    return T_final + (T_initial - T_final) * np.exp(-t / tau)


def calibrate_sensor(sensor_df, sensor_id):
    """
    Fit exponential response model to sensor calibration data.
    
    Returns fitted parameters (T_initial, T_final, tau) and covariance matrix.
    """
    # Filter data for this sensor
    data = sensor_df[sensor_df['sensor_id'] == sensor_id].copy()
    
    # Extract time and temperature columns
    t = data['time_sec']
    temperature = data['temperature']
    
    # Initial parameter guess based on data endpoints
    T_initial_guess = temperature.iloc[0]
    T_final_guess = temperature.iloc[-1]
    tau_guess = 8.0  # reasonable starting point for thermal time constant
    
    p0 = [T_initial_guess, T_final_guess, tau_guess]
    
    # Physical bounds on parameters
    bounds = (
        [-50.0, -50.0, 0.5],   # lower bounds: reasonable temp range, tau >= 0.5
        [150.0, 150.0, 50.0]   # upper bounds
    )
    
    # Fit the model using bounded least squares
    popt, pcov = curve_fit(
        exponential_response,
        t,
        temperature,
        p0=p0,
        bounds=bounds,
        maxfev=10000
    )
    
    return popt, pcov


def main():
    """Run calibration pipeline for all sensors."""
    # Load configuration
    config_path = Path(__file__).parent / 'config.json'
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    data_path = Path(__file__).parent / config['data_file']
    
    # Load data using utility function
    sensor_df = load_calibration_data(data_path)
    
    print(f"Loaded {len(sensor_df)} calibration readings")
    print(f"Data dtype: {sensor_df['temperature'].dtype}")
    print()
    
    # Calibrate each sensor
    results = {}
    sensor_ids = sensor_df['sensor_id'].unique()
    
    for sensor_id in sorted(sensor_ids):
        popt, pcov = calibrate_sensor(sensor_df, sensor_id)
        
        T_initial, T_final, tau = popt
        results[int(sensor_id)] = {
            'T_initial': float(T_initial),
            'T_final': float(T_final),
            'tau': float(tau)
        }
        
        print(f"Sensor {sensor_id}:")
        print(f"  T_initial = {T_initial:.4f}°C")
        print(f"  T_final   = {T_final:.4f}°C")
        print(f"  tau       = {tau:.4f} s")
        print()
    
    # Save results
    output_path = Path(__file__).parent / 'calibration_results.json'
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"Results saved to {output_path}")


if __name__ == '__main__':
    main()
