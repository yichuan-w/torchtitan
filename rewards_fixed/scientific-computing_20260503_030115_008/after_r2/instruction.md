A colleague wrote a simulation of a satellite in low Earth orbit using a fixed-step RK4 integrator. The physics should be simple: two-body gravitational dynamics where total mechanical energy is conserved. However, the simulation shows significant energy drift over time, causing the orbit to become unphysical.

The simulation code is in `/app/orbit_sim.py`. Running it produces an energy plot showing the problem.

Fix the simulation so that:
1. Total mechanical energy is conserved within 0.5% over each coast interval
2. The orbit remains stable (semi-major axis varies by less than 1% during each coast interval)
3. Angular momentum is conserved within 0.01% over each coast interval
4. The scheduled one-second prograde maneuver is modeled accurately, producing a final semi-major-axis increase of 44.49 km, within 0.10 km, after 10 orbital periods

You may modify any part of the code, including the integration method and timestep.

Save your corrected implementation as `/app/orbit_sim_fixed.py`. It should define the same functions as the original (`run_simulation`, `compute_energy`, `compute_angular_momentum`, `compute_semi_major_axis`) and accept the same command-line arguments.

Physical constants and the maneuver schedule are already in the code: gravitational parameter μ = GM_earth = 3.986004418 × 10^14 m³/s², initial orbit is 400 km altitude circular orbit, simulation runs for 10 orbital periods.

Hint: the issue is related to numerical methods for Hamiltonian systems.
