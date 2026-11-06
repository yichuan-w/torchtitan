FROM jyangballin/swesmith.x86_64.erikrose_1776_parsimonious.0d3f5f93
WORKDIR /testbed
COPY bug.patch /tmp/swesmith_bug.patch
RUN set -e; cd /testbed \
 && old="$(git rev-parse HEAD)" \
 && git apply --whitespace=nowarn /tmp/swesmith_bug.patch \
 && git add -A \
 && git -c user.name=swesmith -c user.email=swesmith@localhost -c commit.gpgsign=false commit -q --amend --no-edit --no-verify \
 && git for-each-ref --points-at "$old" --format='%(refname)' | xargs -r -n1 git update-ref --no-deref -d \
 && git remote | xargs -r -n1 git remote remove \
 && git reflog expire --expire=now --expire-unreachable=now --all \
 && git gc --prune=now --quiet \
 && rm -f /tmp/swesmith_bug.patch
