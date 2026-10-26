## Overfit bench: the original task is the exam

The student is scored only on the original task, unchanged: its environment, its
instruction and its verifier. Whatever an easier version does for the student,
the student has to do for itself there. Each rule below comes from a measured
case where an easier version was solved and the original readout fell.

- What the student can obtain in the original environment is work, not a
  broken setup. A missing interpreter, package or tool it can install is not
  repaired by installing it in the image. Say in the instruction what to install
  and how, as sentences a later hardening can remove. (Measured: once PyTorch
  came preinstalled, the original's attempts stopped installing it, from 9 of 16
  to 0 of 15.)
- Do not supply a checker for the retained skill: no smoke test, diagnostic
  script or test harness that exercises the student's implementation. Where
  feedback is the help, tell the student what to check and on what input, and
  leave writing and running the check to it. (Measured: every solved attempt of
  the versions that shipped a checker ran it; the original's attempts that
  wrote their own check fell from 6 of 16 to 0.)
- A convention the original leaves for the student to infer -- a framework's
  storage layout, an attribute name the verifier reads, an axis the wording
  names in another convention -- is part of the retained skill when a careful
  solver can infer it from the instruction and the framework. Do not record it
  as a repair. State it, if the attempts need it, as removable hint sentences,
  and say in change that a hardening must withdraw them. (Measured: once the
  weight layout was stated, the original's attempts that chose the right axis
  fell from 4 of 16 to 0.)
- Setting up and checking are part of the original's work. How to install the
  runtime, and an instruction to run the implementation and check its result,
  go into an easier version only as removable hint sentences, and change says a
  hardening must withdraw them. (Measured: the most-trained version told the
  student how to install CPU PyTorch and to validate on two ranks; on it the
  student took a median 26 turns and tested, while on the original, which says
  neither, its turns fell from a median 27 to 7 and torch installs from 15 of
  32 to 7 of 48 as training went on.)

## When you calibrate (harden a simplified version)

- Withdraw the setup and checking hint sentences first, before any part of the
  task itself: the student has to reach the original's habit of installing and
  testing unprompted, and nothing in a later version teaches it once those
  sentences stay.
- A part the original requires that no version has asked for yet -- a forward
  pass, its gradient, a second class -- is the next part to restore once the
  hints are gone, before refining parts already asked for. (Measured: the
  most-trained version left forward and gradients out of scope, and across 80
  readout attempts ColumnParallelLinear's forward was correct 0 times.)
