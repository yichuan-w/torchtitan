FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Python3 and sqlite3 are already in the base image.
# Generate the SQLite database with deleted records.
# We must use PRAGMA secure_delete=OFF so deleted data persists in freeblocks.
COPY files/build_db.py /tmp/build_db.py
RUN python3 /tmp/build_db.py && rm /tmp/build_db.py

