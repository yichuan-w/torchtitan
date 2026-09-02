#!/bin/bash
# Which reachable machines have a Blackwell SM100 part.
#
# The investigation needs a compute-capability 10.0 device: the B300 is 10.3,
# and the stall has to be compared against the same backward file on a
# neighbouring architecture. The one known B200 is currently unusable — its
# NVLink fabric is stuck "In Progress", so CUDA cannot initialise for anyone on
# the machine — so this looks for another.
#
# Read-only: one nvidia-smi query per host, short timeouts, no allocation.
set -u

HOSTS="narrow-oak voio-s1 voio-s2 hyperbolic2 hyperbolic3 together-ai-secure
       together-new together-gpu-26 together-gpu-31 together-gpu-32
       FlaminioDGXH100 BrewsterH200 FlowMaticH100"

for h in $HOSTS; do
  out=$(timeout 35 ssh -o ConnectTimeout=12 -o BatchMode=yes "$h" \
        'nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader | sort -u | head -2' \
        2>/dev/null)
  if [ -z "$out" ]; then
    printf '%-22s unreachable\n' "$h"
  else
    printf '%-22s %s\n' "$h" "$(echo "$out" | tr '\n' ' | ')"
  fi
done
