#!/bin/bash
# Create the initial broken state.
set -e

# ---- Step 1: Configure nginx (but DON'T start it) ----
cat > /etc/nginx/sites-available/repo << 'NGINXEOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    root /var/www/repo;
    server_name _;
    location / {
        autoindex on;
        try_files $uri $uri/ =404;
    }
}
NGINXEOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/repo /etc/nginx/sites-enabled/repo

# ---- Step 2: Create partial repo with WRONG directory structure ----
# The dists directory structure exists but the pool is flat (missing subdirs)
mkdir -p /var/www/repo/dists/stable/main/binary-amd64
mkdir -p /var/www/repo/pool

# ---- Step 3: Place hello-deb in the flat pool (WRONG layout) ----
cp /app/packages/hello-deb_1.0-1_amd64.deb /var/www/repo/pool/

# ---- Step 4: Generate a CORRUPTED Packages file ----
# We write a deliberately broken Packages file (truncated, invalid)
echo "Package: hello-deb" > /var/www/repo/dists/stable/main/binary-amd64/Packages
echo "Version: 1.0-1" >> /var/www/repo/dists/stable/main/binary-amd64/Packages
echo "Filename: pool/hello-deb_1.0-1_amd64.deb" >> /var/www/repo/dists/stable/main/binary-amd64/Packages
# Missing: Architecture, Size, MD5sum, SHA256, Description fields
# This will cause apt to reject the package

# ---- Step 5: Generate a deliberately WRONG Release file ----
# Write a Release file that references non-existent paths
cat > /var/www/repo/dists/stable/Release << 'EOF'
Origin: Local Repo
Label: Local Repo
Suite: stable
Codename: stable
Architectures: amd64
Components: main
Description: Local APT Repository
MD5Sum:
 deadbeefdeadbeefdeadbeefdeadbeef              100 main/binary-amd64/Packages
SHA256:
 deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef              100 main/binary-amd64/Packages
EOF
# Checksums are obviously wrong

# ---- Step 6: Create a STALE InRelease signed with a key ----
# Generate a GPG key for signing
cat > /tmp/gpg-batch << 'EOF'
%echo Generating GPG key
Key-Type: RSA
Key-Length: 2048
Subkey-Type: RSA
Subkey-Length: 2048
Name-Real: Stale Repo Key
Name-Email: stale@repo.local
Expire-Date: 0
%no-protection
%commit
%echo Done
EOF
gpg --batch --generate-key /tmp/gpg-batch
rm -f /tmp/gpg-batch

KEY_ID=$(gpg --list-keys --with-colons | grep '^pub' | cut -d: -f5)
FINGERPRINT=$(gpg --list-keys --with-colons --fingerprint | grep '^fpr' | head -1 | cut -d: -f10)

# Sign the Release file
gpg --clearsign --local-user "$KEY_ID" --output /var/www/repo/dists/stable/InRelease /var/www/repo/dists/stable/Release

# NOW modify the Release file to make InRelease stale
echo "X-Broken: true" >> /var/www/repo/dists/stable/Release

# Export public key
gpg --armor --export "$KEY_ID" > /usr/share/keyrings/local-repo.gpg

# Delete the secret key
gpg --batch --yes --delete-secret-keys "$FINGERPRINT" 2>/dev/null || true

# ---- Step 7: Configure apt sources WITHOUT GPG key trust ----
# Points to the correct URL but without [signed-by=...], so GPG verification fails
echo "deb http://localhost/ stable main" > /etc/apt/sources.list.d/local-repo.list

# Result:
# - nginx NOT running
# - Wrong pool layout (flat instead of pool/main/...)
# - Corrupted Packages file (missing required fields)
# - Release file with fake checksums
# - InRelease signed with key whose secret is deleted
# - apt sources point to wrong port
# - useful-util and hello-app NOT in the repo
