# Chemical Kinetics Simulation

This project simulates a three-species chemical reaction system.

## The Reaction System

The system models reactions between three chemical species (A, B, C):
- Species concentrations: y1, y2, y3
- Initial conditions: y1=1.0, y2=0.0, y3=0.0

## Files

- `simulate.py` - Main simulation script
- `reaction_params.json` - Reaction rate constants and scheme
- `results.npz` - Output file with simulation results (generated after running)

## Usage

```bash
python3 simulate.py
```

## Notes

The simulation tracks species concentrations over a time span from 0 to 100,000 seconds.
