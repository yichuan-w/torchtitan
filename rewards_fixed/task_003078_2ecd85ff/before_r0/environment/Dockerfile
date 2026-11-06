FROM hamishi740/swerl-tmax-v3:a3810b664389
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
#     apt-get update && apt-get install -y python3 python3-pip gcc libssl-dev
#     pip3 install pytest numpy scipy flask fastapi uvicorn requests pydantic
#
#     # Create /app directory
#     mkdir -p /app
#
#     # Create embed_oracle C source
#     cat << 'EOF' > /app/oracle.c
# #include <stdio.h>
# #include <stdlib.h>
# #include <string.h>
# #include <math.h>
# #include <openssl/sha.h>
#
# int main() {
#     char input[8192];
#     size_t len = fread(input, 1, sizeof(input)-1, stdin);
#
#     unsigned char hash[SHA256_DIGEST_LENGTH];
#     SHA256((unsigned char*)input, len, hash);
#
#     unsigned int seed = 0;
#     for(int i=0; i<4; i++) {
#         seed |= (hash[i] << (i*8));
#     }
#     srand(seed);
#
#     float out[64];
#     for(int i=0; i<64; i+=2) {
#         float u1 = (rand() + 1.0) / ((double)RAND_MAX + 2.0);
#         float u2 = (rand() + 1.0) / ((double)RAND_MAX + 2.0);
#         float r = sqrt(-2.0 * log(u1));
#         float theta = 2.0 * 3.14159265358979323846 * u2;
#         out[i] = r * cos(theta);
#         out[i+1] = r * sin(theta);
#     }
#     fwrite(out, sizeof(float), 64, stdout);
#     return 0;
# }
# EOF
#
#     # Compile and strip the binary
#     gcc -O2 /app/oracle.c -o /app/embed_oracle -lm -lssl -lcrypto
#     strip /app/embed_oracle
#     rm /app/oracle.c
#
#     # Create user and directories
#     useradd -m -s /bin/bash user || true
#     mkdir -p /home/user/data
#
#     # Generate raw_texts.json
#     cat << 'EOF' > /home/user/generate_texts.py
# import json
#
# sentences = [
#     f"This is sample sentence number {i} for the embedding task." for i in range(1, 51)
# ]
#
# with open('/home/user/raw_texts.json', 'w') as f:
#     json.dump(sentences, f, indent=2)
# EOF
#     python3 /home/user/generate_texts.py
#     rm /home/user/generate_texts.py
#
#     chmod -R 777 /home/user
#     chmod -R 777 /app
