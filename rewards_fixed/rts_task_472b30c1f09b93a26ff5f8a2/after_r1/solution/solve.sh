#!/usr/bin/env bash
set -euo pipefail

BINARY=/data/Crackme-Monologix-Linux
UPX_URL=https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-amd64_linux.tar.xz
UPX_SHA256=75cab4e57ab72fb4585ee45ff36388d280c7afd72aa03e8d4b9c3cbddb474193

echo "=== Inspecting /data ==="
ls -la /data

if [[ ! -f "$BINARY" ]]; then
    echo "ERROR: Binary not found at $BINARY"
    exit 1
fi

curl -fsSL "$UPX_URL" -o /tmp/upx.tar.xz
printf '%s  %s\n' "$UPX_SHA256" /tmp/upx.tar.xz | sha256sum -c -
rm -rf /tmp/upx-4.2.4-amd64_linux
tar -xJf /tmp/upx.tar.xz -C /tmp
/tmp/upx-4.2.4-amd64_linux/upx -d "$BINARY"

echo "=== Extracting password via GDB strcmp breakpoint ==="
echo "dummy" > /tmp/dummy_input
GDB_OUT=$(gdb -batch \
    -ex "set pagination off" \
    -ex "set confirm off" \
    -ex "break strcmp" \
    -ex "run < /tmp/dummy_input" \
    -ex "info registers rsi" \
    -ex "x/s \$rsi" \
    "$BINARY" 2>&1)

echo "$GDB_OUT"

PASSWORD=$(printf '%s\n' "$GDB_OUT" | grep -oP '"([^"]+)"' | tr -d '"' | tail -1)

if [[ -z "$PASSWORD" ]]; then
    echo "ERROR: could not extract password from GDB output."
    exit 1
fi

echo ""
echo "=== Discovered password: '$PASSWORD' ==="

echo "=== Verifying password by running the binary ==="
RESULT=$(printf '%s\n' "$PASSWORD" | "$BINARY" 2>/dev/null || true)
echo "$RESULT"

if printf '%s\n' "$RESULT" | grep -q "Bravo"; then
    echo "Password accepted."
else
    echo "ERROR: binary did not print success message."
    exit 1
fi

echo "$PASSWORD" > /data/solution.txt
echo "Password written to /data/solution.txt"

echo "=== Cleaning up workspace ==="
# Remove all files under /data except solution.txt, then write cleanup log
mapfile -t TO_REMOVE < <(find /data -maxdepth 1 -type f ! -name solution.txt)
if [[ ${#TO_REMOVE[@]} -gt 0 ]]; then
    for f in "${TO_REMOVE[@]}"; do
        rm -f "$f"
    done
    printf '%s\n' "${TO_REMOVE[@]}" > /data/cleanup.log
    echo "Cleanup report written to /data/cleanup.log:"
    cat /data/cleanup.log
else
    echo "Warning: no files to clean up. Creating empty cleanup log."
    :> /data/cleanup.log
fi

echo "=== Final state of /data ==="
ls -la /data
