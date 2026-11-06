# Spectral Peak Analysis Pipeline

A colleague has left a spectral analysis script at `/app/spectral_analyzer.py` that processes spectrometer data. The script is supposed to identify absorption peaks in the spectrum, fit Gaussian profiles to each peak, and report peak positions and widths with uncertainties.

The data file is at `/app/data/spectrum.csv` (columns: wavelength_nm, intensity).

The current script runs but produces clearly wrong results - it reports far too many peaks and the fitted parameters are nonsensical.

Fix the analysis pipeline so that it:
1. Correctly identifies all 4 absorption peaks in the spectrum (absorption peaks appear as dips in intensity)
2. Fits Gaussian profiles to each peak with proper background subtraction
3. Reports peak parameters (center, amplitude, width) with finite uncertainty estimates
4. Writes results to `/app/results/peaks.json` with structure:
   ```json
   {
     "peaks": [
       {"center_nm": ..., "amplitude": ..., "width_nm": ..., 
        "center_uncertainty": ..., "amplitude_uncertainty": ..., "width_uncertainty": ...},
       ...
     ]
   }
   ```

All uncertainty values must be finite positive numbers. The spectrum has a complex varying background with oscillations. Two of the peaks are very close together (within ~10nm) and must be resolved as separate peaks.
