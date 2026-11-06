FROM hamishi740/swerl-tmax-v3:b3b04193971c
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
#     apt-get update && apt-get install -y python3 python3-pip cron qemu-system-x86
#     pip3 install pytest
#
#     useradd -m -s /bin/bash user || true
#
#     cat << 'EOF' > /home/user/legacy_users.txt
# alice:wheel:/bin/bash
# bob:developers:/bin/bash
# charlie:interns:/bin/sh
# david:wheel:/bin/zsh
# eve:contractors:/bin/bash
# EOF
#
#     chmod -R 777 /home/user
