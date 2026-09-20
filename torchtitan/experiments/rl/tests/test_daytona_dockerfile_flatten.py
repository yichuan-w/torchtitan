# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Comments inside a continued RUN must not end the continuation.

The Dockerfile a task ships is flattened textually before the Daytona SDK sees
it, and Docker's own rule is that whole-line comments are removed BEFORE
continuations are joined. Getting that order wrong is silent at flatten time and
only surfaces server-side as ``unknown instruction: <first word of the next
line>``, after the create retries are already spent -- and the rollout lands in
its group as a reward-0 infra failure rather than an error.
"""

from __future__ import annotations

import re

from torchtitan.experiments.rl.harness.sandbox.daytona import (
    _prepare_dockerfile_for_daytona,
    _strip_comments_in_continuation,
)


def _flatten(dockerfile: str) -> str:
    """The call site's flatten, applied to the stripped source."""
    return re.sub(r"\\\r?\n[ \t]*", " ", _strip_comments_in_continuation(dockerfile))


def test_comment_inside_continuation_keeps_the_run_on_one_line() -> None:
    dockerfile = (
        "FROM debian:bookworm\n"
        "RUN mkdir -p /repo && \\\n"
        "    cd /repo && \\\n"
        "    # Populate with the files the recording shows as pre-existing\n"
        "    mkdir -p Scripts docs && \\\n"
        "    touch README.md\n"
    )
    lines = _flatten(dockerfile).splitlines()
    assert len(lines) == 2, lines
    assert lines[1].startswith("RUN mkdir -p /repo")
    assert "touch README.md" in lines[1]
    assert "# Populate" not in lines[1]


def test_comment_outside_continuation_survives() -> None:
    dockerfile = (
        "# syntax=docker/dockerfile:1\n"
        "# Install required tools\n"
        "FROM debian:bookworm\n"
        "RUN apt-get update\n"
    )
    assert _flatten(dockerfile) == dockerfile


def test_continuation_state_resets_after_the_run_ends() -> None:
    """A comment following a finished RUN is outside the continuation."""
    dockerfile = (
        "FROM debian:bookworm\n"
        "RUN a && \\\n"
        "    b\n"
        "# a standalone comment\n"
        "RUN c\n"
    )
    assert "# a standalone comment" in _flatten(dockerfile)


def test_unqualified_from_images_use_explicit_docker_hub_names() -> None:
    dockerfile = (
        "FROM --platform=linux/amd64 alpine:3.20 AS build\n"
        "FROM build AS assembled\n"
        "FROM hamishi740/ubuntu-systemd:latest\n"
        "FROM ghcr.io/example/tool:1\n"
        "FROM localhost:5000/example/tool:2\n"
        "FROM scratch AS empty\n"
        "FROM ${BASE_IMAGE}\n"
    )
    assert _prepare_dockerfile_for_daytona(dockerfile).splitlines() == [
        "FROM --platform=linux/amd64 docker.io/library/alpine:3.20 AS build",
        "FROM build AS assembled",
        "FROM docker.io/hamishi740/ubuntu-systemd:latest",
        "FROM ghcr.io/example/tool:1",
        "FROM localhost:5000/example/tool:2",
        "FROM scratch AS empty",
        "FROM ${BASE_IMAGE}",
    ]
