#!/usr/bin/env bash
# Download source datasets for the rebench/TB2.1 training mix.
#
#   download_rebench_sources.sh /absolute/path/to/data/sources
#
# The destination differs per host, so it has no default: writing a Centralia
# path on the B300 box (or the reverse) fails late, after the first download.
# Export HF_HOME to a data disk as well; the B300 box's root filesystem cannot
# hold the download cache.
set -euo pipefail

OUT=${1:-${TMAX_REBENCH_SOURCE_DIR:-}}
if [ -z "$OUT" ]; then
    echo "usage: $0 /absolute/path/to/data/sources (or set TMAX_REBENCH_SOURCE_DIR)" >&2
    exit 2
fi

mkdir -p "$OUT"

if command -v hf >/dev/null 2>&1; then
    HFCLI=(hf download)
elif command -v huggingface-cli >/dev/null 2>&1; then
    HFCLI=(huggingface-cli download)
else
    echo "missing hf; install huggingface_hub in the active env" >&2
    exit 127
fi

failed=()

# Downloads resume from what is already on disk, so a retry costs only the tail
# of the interrupted file. One dataset failing must not skip the others.
download_dataset() {
    local repo=$1
    local name=$2
    for attempt in 1 2 3; do
        if "${HFCLI[@]}" "$repo" --repo-type dataset --local-dir "$OUT/$name"; then
            return 0
        fi
        echo "[download] $repo attempt $attempt failed" >&2
    done
    failed+=("$repo")
}

download_dataset Fzz1/SWE-Smith-Seeds-Clean SWE-Smith-Seeds-Clean
download_dataset Fzz1/SWE-Rebench-Tasks-Clean SWE-Rebench-Tasks-Clean
download_dataset andylizf/TerminalWorld-Seeds-Clean TerminalWorld-Seeds-Clean
download_dataset Fzz1/Tmax-Tasks-Clean Tmax-Tasks-Clean

if [ ${#failed[@]} -gt 0 ]; then
    echo "failed: ${failed[*]}" >&2
    exit 1
fi

echo "downloaded sources under $OUT"
