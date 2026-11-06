We're migrating our infrastructure from manually-managed SSH host keys to certificate-based host authentication. The current setup with individual known_hosts entries for every server has become unmanageable as we scale.

The server inventory is in `/app/inventory.txt`. It lists each server's short name, FQDN, IP address, and the desired host key type.

Your task is to set up the SSH Certificate Authority and sign host certificates for all servers. All output goes under `/app/ssh-ca/`.

## Requirements

### 1. Certificate Authority
Create a passphrase-less ed25519 SSH CA key pair. The private key must have mode 0600. Save them as:
- `/app/ssh-ca/ca`
- `/app/ssh-ca/ca.pub`

### 2. Host Keys and Certificates
For every server in the inventory, generate a passphrase-less host key of the type specified. Sign each one with the CA to produce an SSH **host** certificate (not a user certificate). Each certificate must:

- Use the server's short name as the certificate identity
- Include exactly three principals: the FQDN, the short name, and the IP address
- Be valid for 52 weeks
- Have a unique, sequential serial number starting from 1 for the first server and incrementing for each subsequent one

Name the key files as `/app/ssh-ca/{name}_host_key` and `/app/ssh-ca/{name}_host_key.pub`. After signing, the certificate will appear at `/app/ssh-ca/{name}_host_key-cert.pub`.

### 3. Certificate Revocation
Server `db2` was recently decommissioned. Revoke its host certificate by creating a Key Revocation List at `/app/ssh-ca/revoked.krl`. The KRL must list `db2`'s certificate (by its public key or certificate file) so that SSH clients and servers can detect it as revoked.

### 4. Known Hosts
Create `/app/ssh-ca/known_hosts` with a `@cert-authority` directive that trusts the CA for all hosts matching `*.prod.internal`. Also add a `@revoked` directive referencing the KRL for the same host pattern.

### 5. Automated Verification Report
Write a shell script at `/app/ssh-ca/generate_report.sh` that, when executed, produces `/app/ssh-ca/verification.txt`. The report must list, for each **non-revoked** server:
- The certificate identity
- All principals
- Validity dates in YYYY-MM-DD format
- Confirmation that the CA signature is valid (match the CA fingerprint)

### 6. Python Check Script
Write `/app/check_certs.py` that:
- Reads the inventory from `/app/inventory.txt`
- Inspects each certificate using `ssh-keygen`
- Confirms each certificate is a valid host certificate signed by the CA
- Checks each certificate against the KRL and flags revoked ones
- Prints a summary line per server with principals, expiration date, and revocation status
- Exits with code 0 only if all active (non-revoked) certificates pass and the revoked one is properly identified

Use `ssh-keygen` for all key operations, certificate signing, KRL management, and inspection.
