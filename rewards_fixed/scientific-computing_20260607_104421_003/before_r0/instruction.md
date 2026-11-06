I've collected SAXS data for crambin at the synchrotron. The experimental scattering curve is in `experimental_saxs.dat` (three columns: q, I(q), σ(q)). I need to validate this against the crystal structure 1CRN by computing the theoretical profile, scaling it to match the experiment, and assessing the quality of fit.

The directory contains:
  * `1crn.pdb` — Crystal structure of crambin at 1.5 Å resolution (PDB entry 1CRN). Use ATOM and HETATM records. Coordinates are in columns 31-54, element symbols in columns 77-78 (may be blank — infer from atom name if needed).
  * `cromer_mann.dat` — Cromer-Mann coefficients for C, N, O, S. The file header explains the parameterization and column layout.
  * `experimental_saxs.dat` — Experimental SAXS data: q (Å⁻¹), I(q), and estimated error σ(q).

First, compute the theoretical spherically-averaged scattering intensity I_model(q) from the atomic model. The standard approach for solution scattering is the Debye equation, summing over all ordered atom pairs using pairwise distances and atomic form factors. Handle the self-term correctly.

Second, the model and experiment are on different absolute scales (experimental intensities are in arbitrary units). Find the optimal scale factor k by minimizing:

  χ² = Σ_q (k · I_model(q) − I_exp(q))² / σ(q)²

Solve for k analytically (set dχ²/dk = 0) using all q values.

Third, perform Guinier analysis on the scaled model profile (k · I_model(q)) to extract the radius of gyration Rg and forward scattering I(0). Use iterative refinement with the q·Rg < 1.3 validity criterion.

Output:
  * `saxs_profile.txt` — Five columns for each q in the experimental data: q, I_model(q), k·I_model(q) (scaled), I_exp(q), σ(q).
  * `guinier_fit.txt` — Three values on one line: Rg (Å), I(0) (scaled forward scattering), and the reduced χ² = χ² / (N_points − 1).
