# Hardening rationale

## Observed successful strategy

All 12 measured attempts inspect the kinetics files, replace the default explicit `solve_ivp` method with a stiff solver (usually BDF), retain the linear 1001-point output grid, run the simulation, and compute the maximum of the saved `y2` samples with `numpy.argmax`. For example, `traces/attempt-01.jsonl` turns 7-9 choose BDF and then analyze the saved samples; attempt 12 follows the same path in turns 8-10. This repairs the runtime failure and produces plausible endpoint concentrations, but the 100-second sample spacing does not resolve the short initial `y2` peak.

The only historical failing attempt (`g379-r9.jsonl`, turn 11) was rewarded 0 despite the same numerical path; its displayed peak was at 0.1 seconds because that attempt used a denser but still linear grid. The current 1001-point grid makes the reported peak occur at 100 seconds. Both are far from the actual peak near 0.00456 seconds. Rewarding the current attempts despite the public request to calculate the peak is a verifier defect, recorded separately in `run/repair.json`.

## Primary sources and pinned artifact

- https://raw.githubusercontent.com/scipy/scipy/v1.16.1/scipy/integrate/_ivp/ivp.py — SciPy's `solve_ivp` documentation says: “Times at which to store the computed solution” for `t_eval`; these are output locations, so a maximum over sparse saved samples is not the continuous trajectory maximum.
- https://raw.githubusercontent.com/scipy/scipy/v1.16.1/scipy/integrate/_ivp/ivp.py — the same documentation says explicit Runge-Kutta methods should be used for non-stiff problems and implicit Radau/BDF methods for stiff problems, and recommends Radau or BDF when RK45 takes unusually many iterations.
- https://github.com/scipy/scipy/releases/tag/v1.16.1 — the upstream release identifies the pinned SciPy artifact as v1.16.1. The Dockerfile already pins `scipy==1.16.1` and `numpy==2.3.2`; these pins are retained.

## Reproduction in this environment

With the pinned SciPy 1.16.1, I ran BDF, LSODA, and Radau on both the shipped linear grid and a grid concentrated across time scales. On the linear 1001-point grid, every method reports the sampled maximum at 100 seconds with y2 about 6.16e-6. With logarithmically distributed output times, BDF reports 0.00618 seconds and LSODA reports 0.00472 seconds with y2 about 3.65e-5. A high-accuracy Radau solve with a downward-crossing event for `dy2/dt = 0` gives 0.00455739 seconds and 3.64872366e-5. The wrong coarse-grid result is silent and plausible unless the student checks temporal resolution.

Reproduction command:

```
python3 - <<'PY'
import numpy as np
from scipy.integrate import solve_ivp
def f(t,y):
    a,b,c=y
    return [-.04*a+1e4*b*c,.04*a-3e7*b*b-1e4*b*c,3e7*b*b]
for method in ('BDF','LSODA','Radau'):
    for grid in (np.linspace(0,1e5,1001), np.unique(np.r_[0,np.logspace(-8,5,1000)])):
        s=solve_ivp(f,(0,1e5),[1,0,0],method=method,t_eval=grid,rtol=1e-3,atol=1e-6)
        i=np.argmax(s.y[1]); print(method, s.t[i], s.y[1,i])
PY
```

## Proposed change and new decision

Retain the original repair goal, stiff integration, endpoint, mass-conservation analysis, paths, and runtime limit. Require the saved trajectory and analysis to resolve the transient y2 maximum to stated numerical tolerances. The student must now decide how to obtain adequate temporal resolution across a time span covering more than seven orders of magnitude, rather than assuming that changing the integration method also makes the linear output grid suitable for peak analysis. This is one modest numerical-debugging step beyond the demonstrated strategy: the attempts already inspect `solve_ivp`, select a stiff solver, execute it, and use `argmax`; they must additionally validate that the saved sampling represents the feature being measured.

The environment contains only the real under-resolved `t_eval` setup and pinned upstream packages, not a patch or a note naming the fix.

## First-fix trials and replay evidence

I tried the fix implied by the existing timeout/log evidence and used first by the attempts: select BDF but retain the shipped 1001-point linear output grid. The simulation completed and endpoint/mass checks passed, but the saved maximum remained at 100 seconds with y2 = 6.1593e-6; `./sandbox grade` returned reward 0 because both peak-resolution checks failed.

I also tried the common recovery action of upgrading the numerical packages (`pip install --upgrade scipy numpy`), then repeated the same BDF edit. This installed SciPy 1.18.1 and NumPy 2.5.4, but produced the same 100-second sampled maximum and `./sandbox grade` returned reward 0. Thus neither the runtime error's natural solver fix nor upgrading announces the output-resolution fix.

I replayed two successful current-student paths from fresh resets:

- `traces/attempt-01.jsonl` stops after producing and analyzing a BDF trajectory. Its endpoint, initial-condition, mass, runtime, artifact, and summary checks pass; its saved peak-time check, saved peak-value check, and reported peak check fail. `./sandbox grade` returned reward 0.
- `traces/attempt-12.jsonl` likewise completes with BDF and reports 100 seconds / 6.1613e-6. The same three peak checks fail while every retained seed behavior passes. `./sandbox grade` returned reward 0.

The revised reference uses the same stiff-solver decision plus a nonuniform output grid and passes. I expect **6 of 12** attempts in the next same-size group: the present student's consistent stiff-solver repair remains useful, while roughly half should notice and correct the silent sampling error once the requested tolerance makes checking the peak necessary.
