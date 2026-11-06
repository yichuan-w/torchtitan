You are a Linux systems engineer tasked with hardening a legacy application environment. You need to automate the configuration of a user list, schedule a recurring task, and prepare a secure virtual machine launch script. You must implement this using Bash.

Please perform the following tasks:

1. Write an idempotent Bash script at `/home/user/setup_env.sh`. When executed, this script must:
   - Read a legacy user list provided at `/home/user/legacy_users.txt`. This file has the format `username:group:shell`.
   - Generate a new file at `/home/user/hardened_users.txt`. For any user whose group is NOT `wheel`, their shell must be changed to `/usr/sbin/nologin`. Users in the `wheel` group should retain their original shell. The output must maintain the same `username:group:shell` format.
   - Be fully idempotent (running it multiple times must result in the exact same `/home/user/hardened_users.txt` file without appending duplicates).
   - Ensure `/home/user/hardened_users.txt` has read-only permissions (`0444`).

2. Create a VM wrapper script at `/home/user/start_vm.sh`. This script must contain a single `qemu-system-x86_64` command that:
   - Allocates 512 MB of RAM.
   - Uses `/home/user/disk.qcow2` as the primary hard drive (`-hda`).
   - Disables standard graphical output (`-nographic`).
   - Starts a VNC server listening *only* on localhost (`127.0.0.1`), display number 2, with password authentication required.
   - Make sure `/home/user/start_vm.sh` is executable (`0755`).

3. Configure a scheduled task for the current user using `cron`.
   - The cron job must execute `/home/user/setup_env.sh` every day at exactly 3:15 AM.
   - Do not remove any existing cron jobs (if they exist); only add this new one.

Make sure all scripts and files are placed exactly at the absolute paths specified.