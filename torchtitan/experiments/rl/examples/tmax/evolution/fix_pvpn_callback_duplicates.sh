#!/bin/bash
# Make the callback bundle id resolve to the live helper, not a backup of it.
#
# `globalprotectcallback:` has one registered handler, chosen by bundle id. The
# installer keeps dated backups, and each backup contains a full callback.app
# with the same bundle id — so LaunchServices sees four apps claiming one
# identity and picks whichever it likes. It picked a backup, which is the
# documented trap: renewal appears to proceed while the SAML token is written
# somewhere nothing reads.
#
# The repo's own claim_callback_handler unregisters the official GlobalProtect
# app before claiming the scheme, but not copies of itself, which is the gap
# this closes.
#
# Backups are archived rather than deleted: a zip is not an app bundle, so
# LaunchServices stops seeing it while the bytes stay recoverable.
set -euo pipefail

LIVE="$HOME/Applications/PrincetonVPNCallback.app"
BACKUP_ROOT="/Library/Application Support/PrincetonVPN/backups"
BUNDLE=io.github.andylizf.princeton-vpn.callback
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

[ -d "$LIVE" ] || { echo "live callback app missing at $LIVE"; exit 1; }

echo "=== before ==="
duti -x globalprotectcallback 2>&1 | sed 's/^/  /'

archived=0
while IFS= read -r app; do
  [ -n "$app" ] || continue
  echo "archiving $app"
  # Unregister first, so the record goes away even if the directory lingers.
  sudo "$LSREGISTER" -u "$app" >/dev/null 2>&1 || true
  sudo ditto -c -k --sequesterRsrc --keepParent "$app" "${app%.app}.app.zip"
  # Only remove the bundle once the archive it was copied into exists.
  if [ -s "${app%.app}.app.zip" ]; then
    sudo rm -rf "$app"
    archived=$((archived + 1))
  else
    echo "  archive missing, leaving $app in place"
  fi
done < <(find "$BACKUP_ROOT" -maxdepth 2 -name "callback.app" -type d 2>/dev/null)

echo "archived $archived backup copies"

# Re-register the live app and claim the scheme, the same way the browser agent
# does, so this leaves the system in the state that routine claims expect.
"$LSREGISTER" -f "$LIVE" >/dev/null 2>&1 || true
duti -s "$BUNDLE" globalprotectcallback all

echo "=== after ==="
duti -x globalprotectcallback 2>&1 | sed 's/^/  /'

remaining=$(mdfind "kMDItemFSName == 'callback.app'" 2>/dev/null | wc -l | tr -d ' ')
echo "callback.app bundles still indexed: $remaining"
