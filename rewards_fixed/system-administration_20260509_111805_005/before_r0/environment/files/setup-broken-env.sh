#!/bin/bash
# Setup script to create the broken git server environment

set -e

# Create git user for repository ownership
useradd -r -s /usr/sbin/nologin git 2>/dev/null || true

# Create repository directory
mkdir -p /srv/git

# Create a test repository with some initial content
mkdir -p /tmp/project-alpha-init
cd /tmp/project-alpha-init
git init
git config user.email "admin@example.com"
git config user.name "Admin"
echo "# Project Alpha" > README.md
echo "Initial content for project alpha." > alpha.txt
git add .
git commit -m "Initial commit"

# Create bare repository
git clone --bare /tmp/project-alpha-init /srv/git/project-alpha.git
cd /srv/git/project-alpha.git
git config --local receive.denyCurrentBranch ignore

# Issue: Set http.receivepack to false (prevents push)
git config --local http.receivepack false

# Clean up temp directory
rm -rf /tmp/project-alpha-init

# Issue: Wrong ownership - owned by root:root instead of git:www-data
# This causes "dubious ownership" errors and permission issues
chown -R root:root /srv/git
chmod -R 755 /srv/git

# Create the nginx configuration with multiple issues
cat > /etc/nginx/sites-available/git-http << 'NGINX_EOF'
# Git HTTP server configuration
server {
    listen 80 default_server;
    server_name _;

    root /srv/git;

    location ~ \.git(/.*)?$ {
        # Wrong socket path
        fastcgi_pass unix:/var/run/fcgiwrap.socket;
        
        fastcgi_param SCRIPT_FILENAME /usr/lib/git-core/git-http-backend;
        fastcgi_param PATH_INFO $uri;
        
        include fastcgi_params;
    }
}
NGINX_EOF

# Remove default site and enable git-http
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/git-http /etc/nginx/sites-enabled/git-http

# Issue: Don't create htpasswd file - solver needs to create it
# Issue: Don't start fcgiwrap socket - solver needs to enable it
# Issue: Don't create safe.directory config for www-data - solver needs to fix ownership

echo "Broken git server environment created."
