"""
Spectral Peak Analyzer
Identifies and fits Gaussian peaks in absorption spectrum data.
"""
import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks
import json
import os

# Load spectrum data
data = np.loadtxt('/app/data/spectrum.csv', delimiter=',', skiprows=1)
wavelength = data[:, 0]
intensity = data[:, 1]

# Gaussian function for peak fitting
def gaussian(x, center, amplitude, width):
    return amplitude * np.exp(-(x - center)**2 / (2 * width**2))

# Find peaks in intensity
# Use inverted signal since absorption peaks are dips
peak_indices, properties = find_peaks(intensity, height=50, distance=5)

print(f"Found {len(peak_indices)} peaks")

# Fit each peak
peaks = []
for idx in peak_indices:
    # Extract region around peak
    margin = 50
    start = max(0, idx - margin)
    end = min(len(wavelength), idx + margin)
    
    x_region = wavelength[start:end]
    y_region = intensity[start:end]
    
    # Initial guesses
    center_guess = wavelength[idx]
    amp_guess = intensity[idx] - np.min(y_region)
    width_guess = 10.0
    
    # Fit Gaussian
    try:
        popt, pcov = curve_fit(gaussian, x_region, y_region,
                              p0=[center_guess, amp_guess, width_guess])
        perr = np.sqrt(np.diag(pcov))
        
        peaks.append({
            "center_nm": float(popt[0]),
            "amplitude": float(popt[1]),
            "width_nm": float(abs(popt[2])),
            "center_uncertainty": float(perr[0]),
            "amplitude_uncertainty": float(perr[1]),
            "width_uncertainty": float(perr[2])
        })
        print(f"Peak at {popt[0]:.1f}nm, amp={popt[1]:.1f}")
    except Exception as e:
        print(f"Failed to fit peak at {center_guess:.1f}nm: {e}")

# Sort peaks by wavelength
peaks.sort(key=lambda p: p["center_nm"])

# Save results
os.makedirs('/app/results', exist_ok=True)
with open('/app/results/peaks.json', 'w') as f:
    json.dump({"peaks": peaks}, f, indent=2)

print(f"\nFound {len(peaks)} peaks, saved to /app/results/peaks.json")
