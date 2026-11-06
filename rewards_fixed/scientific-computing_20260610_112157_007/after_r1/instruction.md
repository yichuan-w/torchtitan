Run the script at `/app/simulation.py` to solve the 1D time-independent Schrödinger equation for a finite square well (V₀ = 50, a = 1, ℏ = m = 1) using the Numerov shooting method. The output in `/app/results.json` is physically wrong — fix all bugs so the solver correctly finds the three lowest bound-state energies and normalized wavefunctions (∫|ψ|² dx = 1).

Focus on how the shooting search brackets and selects each successive state from the node count; stable two-sided wavefunction construction is already provided.
