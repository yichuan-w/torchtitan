# Chemical Kinetics Simulation Not Completing

I'm working on a chemical kinetics simulation in `/app/kinetics/` that models a three-species reaction system. The simulation is supposed to track species concentrations over a time span from 0 to 100,000 seconds.

The script at `/app/kinetics/simulate.py` should read the reaction parameters from `/app/kinetics/reaction_params.json` and output results.

**The problem:** The simulation appears to hang indefinitely and never completes. I've let it run for several minutes with no output. The initial concentrations are y1=1.0, y2=0.0, y3=0.0.

**What I need:**
1. Fix the simulation so it completes successfully within a reasonable time (under 30 seconds)
2. Create a script `/app/kinetics/analyze.py` that:
   - Loads the simulation results
   - Calculates the time at which y2 reaches its maximum concentration; the saved trajectory and reported peak must resolve the transient accurately enough that the peak time is within 5% and its concentration within 1% of the continuous solution
   - Prints a summary with:
     - Final concentrations at t=100,000 seconds
     - Time and value of maximum y2 concentration
     - Verification of mass conservation (y1 + y2 + y3 should always sum to 1.0)
3. Run the simulation and analysis, saving results to `/app/kinetics/results.npz`

The rate constants and reaction scheme are in the configuration file. Please make the minimal necessary changes to get this working correctly.
