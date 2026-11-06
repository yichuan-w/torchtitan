"""
Data utilities for sensor calibration pipeline.

This module provides functions for loading and preprocessing sensor data.
"""

import pandas as pd
import numpy as np


def load_calibration_data(filepath):
    """
    Load sensor calibration data from CSV file.
    
    Optimizes memory usage by using appropriate data types for
    IoT sensor data streams.
    
    Parameters:
        filepath: Path to CSV file containing sensor readings
        
    Returns:
        DataFrame with sensor_id, time_sec, and temperature columns
    """
    # Use memory-efficient dtypes for IoT sensor data
    # Integer columns use smaller integer types
    # Float columns use float32 instead of float64
    dtype_spec = {
        'sensor_id': np.int16,
        'time_sec': np.float32,
        'temperature': np.float32
    }
    
    df = pd.read_csv(filepath, dtype=dtype_spec)
    return df
