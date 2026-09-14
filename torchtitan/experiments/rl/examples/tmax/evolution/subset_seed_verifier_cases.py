#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Select unchanged frozen controls for a separate diagnostic attempt."""

import argparse
import json

from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", required=True, action="append")
    parser.add_argument("--concurrency", default=1, type=int)
    args = parser.parse_args()
    config = json.loads(args.source.read_text())
    selected = [case for case in config["cases"] if case["case_id"] in args.case]
    if {case["case_id"] for case in selected} != set(args.case):
        raise ValueError("requested case is absent from frozen input")
    config["cases"] = selected
    config["concurrency"] = args.concurrency
    with args.output.open("x") as stream:
        json.dump(config, stream)
        stream.write("\n")
    print(json.dumps({"cases": len(selected), "concurrency": args.concurrency}))


if __name__ == "__main__":
    main()
