FROM jyangballin/swesmith.x86_64.erikrose_1776_parsimonious.0d3f5f93
WORKDIR /testbed
COPY bug.patch /tmp/swesmith_bug.patch
RUN set -e; cd /testbed \
 && url=https://raw.githubusercontent.com/erikrose/parsimonious/eb79639859a9697a86c0992a045174a8856b5fb0/parsimonious/expressions.py \
 && curl -fL "$url" -o /tmp/upstream-expressions.py \
 && echo '9667cd963b4334cb9f0c7f07b31410745c942b25dfd2b8f2cbd92e4e5ab29d0e  /tmp/upstream-expressions.py' | sha256sum -c - \
 && install -m 0644 /tmp/upstream-expressions.py parsimonious/expressions.py \
 && old="$(git rev-parse HEAD)" \
 && git apply --whitespace=nowarn /tmp/swesmith_bug.patch \
 && git add -A \
 && git -c user.name=swesmith -c user.email=swesmith@localhost -c commit.gpgsign=false commit -q --amend --no-edit --no-verify \
 && git for-each-ref --points-at "$old" --format='%(refname)' | xargs -r -n1 git update-ref --no-deref -d \
 && git remote | xargs -r -n1 git remote remove \
 && git reflog expire --expire=now --expire-unreachable=now --all \
 && git gc --prune=now --quiet \
 && rm -f /tmp/swesmith_bug.patch /tmp/upstream-expressions.py
