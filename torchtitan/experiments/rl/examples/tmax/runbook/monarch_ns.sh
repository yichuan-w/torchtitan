#!/bin/bash
# Run a command with this node's Kubernetes pod DNS name as its hostname.
#
#   monarch_ns.sh CMD [ARG...]     "{host}" in any ARG becomes that name
#
# Monarch's TCP transport addresses every process by its host's name, and on
# this cluster a compute node resolves only its own name, never a peer's. The
# pod DNS name (10-0-1-66.<namespace>.pod.cluster.local) resolves from every
# node, so launch_9b.sh runs each Monarch process of a multi-node run through
# this: the per-node workers and the controller. "hostname" needs root, which
# the user namespace maps to this same uid outside it, so files and devices are
# accessed exactly as without it. RL_POD_DNS_DOMAIN overrides the domain.
set -euo pipefail
ip=$(hostname -I | grep -oE '^[0-9]+(\.[0-9]+){3}' | head -1)
if [ -z "$ip" ]; then
    echo "[monarch_ns] no IPv4 address on $(hostname)" >&2
    exit 2
fi
name="${ip//./-}.${RL_POD_DNS_DOMAIN:-tenant-slurm.pod.cluster.local}"
args=()
for a in "$@"; do
    args+=("${a//\{host\}/$name}")
done
# torchstore decides "same host as the storage volume" by comparing the HOSTNAME
# environment variable (then gethostname), and picks POSIX shared memory when they
# match. A Kubernetes container exports HOSTNAME, and sbatch --export=ALL carries
# the submitting pod's value to every node, so without this a generator on the
# second node matched the trainer's volume on the first, chose shared memory,
# and failed with "Shared memory storage not found ... on a different host".
# Per-node names make cross-node transfers resolve to RDMA.
export HOSTNAME="$name"
exec unshare --user --map-root-user --uts sh -c 'hostname "$0" && exec "$@"' "$name" "${args[@]}"
