FROM hamishi740/swerl-tmax-v3:b7a9775f9c63
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
#     apt-get update && apt-get install -y python3 python3-pip gcc sqlite3 libc6-dev
#     pip3 install pytest
#
#     useradd -m -s /bin/bash user || true
#
#     cat << 'EOF' > /home/user/raw_data.csv
# DateString,Email,TransactionValue
# 2023-10-01 10:00:00,alice.smith@gmail.com,150.50
# 2023-10-01 11:30:00,bob_jones123@yahoo.com,200.00
# 2023-10-02 09:15:00,alice.smith@gmail.com,49.50
# 2023-10-02 14:00:00,invalid-email-address,5000.00
# 2023-10-03 16:45:00,charlie.brown@company.co.uk,75.25
# 2023-10-04 08:20:00,bob_jones123@yahoo.com,25.00
# 2023-10-04 10:00:00,d@short.com,10.00
# 2023-10-05 12:00:00,bad_email@missing_tld,100.00
# EOF
#
#     chmod -R 777 /home/user
