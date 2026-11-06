FROM jyangballin/swesmith.x86_64.lepture_1776_mistune.bf54ef67
WORKDIR /testbed
COPY markupsafe-3.0.3-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl /tmp/
RUN wheel=/tmp/markupsafe-3.0.3-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl \
 && echo "f42d0984e947b8adf7dd6dde396e720934d12c506ce84eea8476409563607591  $wheel" | sha256sum -c - \
 && /opt/miniconda3/envs/testbed/bin/pip install --no-cache-dir "$wheel" && rm "$wheel"
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
