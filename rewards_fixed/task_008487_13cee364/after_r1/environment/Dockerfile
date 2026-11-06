FROM hamishi740/swerl-tmax-v3:66a979fadaf0
RUN pip3 install --no-cache-dir --disable-pip-version-check --no-deps --require-hashes \
    'scapy @ https://files.pythonhosted.org/packages/4b/34/8695b43af99d0c796e4b7933a0d7df8925f43a8abdd0ff0f6297beb4de3a/scapy-2.6.1-py3-none-any.whl#sha256=88a998572049b511a1f3e44f4aa7c62dd39c6ea2aa1bb58434f503956641789d'
COPY make_capture.py /tmp/make_capture.py
RUN test -f /tmp/make_capture.py && python3 /tmp/make_capture.py && \
    rm /tmp/make_capture.py && chmod 0644 /home/user/traffic.bin
#
# The task environment is BAKED INTO THE IMAGE above. The lines below are this task's
# setup.sh, inlined as comments so a reader -- and the breeding model, which sees only
# instruction.md, this file and solution/solve.sh -- can tell how the environment was built.
# They are COMMENTS on purpose: these scripts are not idempotent and must never run again as
# RUN steps, and a COPY/RUN here would move the image identity the pre-test hook keys on.
# The executable copy ships beside this file as tasks/<id>/setup.sh.
#
# #!/bin/bash
# set -e
# export DEBIAN_FRONTEND=noninteractive
#     apt-get update && apt-get install -y python3 python3-pip rustc cargo
#     pip3 install pytest
#
#     useradd -m -s /bin/bash user || true
#
#     cat << 'EOF' > /tmp/setup.py
# import os
# import hashlib
#
# # Ground truth parameters
# K0 = 42
# A = 17
# C = 105
#
# plaintext = b"TELE_CRITICAL_SYSTEM_ALERT_FLAG{lcg_stream_broken_1337}"
#
# # Calculate checksum
# checksum = hashlib.sha256(plaintext).digest()
#
# # Encrypt
# encrypted = bytearray()
# k = K0
# for b in plaintext:
#     encrypted.append(b ^ k)
#     k = (A * k + C) % 256
#
# # Write to file
# with open("/home/user/traffic.bin", "wb") as f:
#     f.write(checksum + encrypted)
#
# # Ensure the correct permissions
# os.chmod("/home/user/traffic.bin", 0o644)
# EOF
#
#     python3 /tmp/setup.py
#     rm /tmp/setup.py
#
#     chmod -R 777 /home/user
