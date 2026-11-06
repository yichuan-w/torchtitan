FROM hamishi740/swerl-tmax-v3:1bb8e73d63fd
RUN pip3 install --no-cache-dir Flask-Limiter==3.8.0
COPY setup-rate-limit.sh /tmp/setup-rate-limit.sh
RUN bash /tmp/setup-rate-limit.sh && rm /tmp/setup-rate-limit.sh
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
# apt-get update && apt-get install -y python3 python3-pip redis-server
# pip3 install pytest flask redis
#
# mkdir -p /app/services
# mkdir -p /home/user
#
# cat << 'EOF' > /app/services/api.py
# import os
# import json
# from flask import Flask, request, jsonify
# import redis
#
# app = Flask(__name__)
# # Agent must set REDIS_URL or modify this
# redis_client = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/1"))
#
# def get_score(seq):
#     # dummy SW alignment against background
#     with open('/home/user/background.fasta') as f:
#         bg = "".join(l.strip() for l in f if not l.startswith(">"))
#     # compute max matches
#     max_score = 0
#     for i in range(len(bg) - len(seq)):
#         score = sum(2 if seq[j] == bg[i+j] else -1 for j in range(len(seq)))
#         if score > max_score: max_score = score
#     return max_score
#
# @app.route('/score', methods=['POST'])
# def score():
#     data = request.json
#     seq = data['sequence']
#     cached = redis_client.get(seq)
#     if cached:
#         return jsonify({"score": int(cached)})
#     s = get_score(seq)
#     redis_client.set(seq, s)
#     return jsonify({"score": s})
#
# if __name__ == '__main__':
#     app.run(port=5000)
# EOF
#
# cat << 'EOF' > /verify_metric.py
# import csv
# import sys
#
# def get_score(seq):
#     with open('/home/user/background.fasta') as f:
#         bg = "".join(l.strip() for l in f if not l.startswith(">"))
#     max_score = 0
#     for i in range(len(bg) - len(seq)):
#         score = sum(2 if seq[j] == bg[i+j] else -1 for j in range(len(seq)))
#         if score > max_score: max_score = score
#     return max_score
#
# total_score = 0
# count = 0
# with open('/home/user/optimized_primers.csv') as f:
#     reader = csv.DictReader(f)
#     for row in reader:
#         total_score += get_score(row['Forward_Primer'])
#         total_score += get_score(row['Reverse_Primer'])
#         count += 2
#
# if count != 10:
#     print(f"Expected 10 primers, found {count}")
#     sys.exit(1)
#
# print(total_score)
# EOF
#
# python3 -c "
# import random
# random.seed(42)
# bases = ['A', 'C', 'G', 'T']
# with open('/home/user/targets.fasta', 'w') as f:
#     for i in range(5):
#         seq = ''.join(random.choices(bases, k=100))
#         f.write(f'>Target_{i+1}\n{seq}\n')
#
# with open('/home/user/background.fasta', 'w') as f:
#     seq = ''.join(random.choices(bases, k=5000))
#     f.write(f'>Background\n{seq}\n')
# "
#
# useradd -m -s /bin/bash user || true
# chmod -R 777 /home/user /app
