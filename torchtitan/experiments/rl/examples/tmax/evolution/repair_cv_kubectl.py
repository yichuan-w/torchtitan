#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Stage repairs for two seed tasks; never modify the source packages.

Run with --tasks-dir pointing at the published packages and --output at a new
directory. Validate the staged packages on Daytona before publishing them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

KUBECTL_SHA256 = "3473e14c7b024a6e5403c6401b273b3faff8e5b1fed022d633815eb3168e4516"
CV_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sample CV</title></head>
<body>
<h1>Sample CV</h1>
<p>This fictional CV is a fixed input for a document conversion task.</p>
<h2>Profile</h2>
<p>Software engineer focused on reliable data tools and accessible documentation.</p>
<h2>Experience</h2>
<h3>Data tools engineer, 2022 to 2025</h3>
<ul><li>Built a CSV validation service for research datasets.</li>
<li>Reduced nightly processing time from 40 minutes to 12 minutes.</li></ul>
<h3>Documentation maintainer, 2020 to 2022</h3>
<p>Maintained installation guides and tested examples on Linux.</p>
<h2>Education</h2>
<p>Bachelor of Science in Computer Science, 2020.</p>
<h2>Projects</h2>
<h3>Dataset Inspector</h3>
<p>A command-line tool that reports missing values and duplicate records.</p>
<h2>Skills</h2>
<p>Python, SQL, Git, Linux, technical writing.</p>
</body></html>
"""
CV_INSTRUCTION = """
Convert the supplied CV webpage at `/app/source-cv.html` into `/app/cv.pdf`.
The HTML is a fixed fictional sample, included so the task does not depend on
an external website. Preserve its headings and all profile, experience,
education, project and skills content in the PDF.

The conversion must go through a LaTeX intermediate: save it at `/app/cv.tex`
and compile it to a valid, text-readable PDF. Leave the source HTML unchanged.
"""
CV_TESTS = """

def normalized(text):
    import re
    import unicodedata
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", text).lower())


def test_pdf_preserves_cv_content():
    reader = PdfReader("/app/cv.pdf")
    text = normalized(" ".join(page.extract_text() or "" for page in reader.pages))
    required = (
        "Sample CV", "Profile", "Experience", "Education", "Projects", "Skills",
        "Software engineer focused on reliable data tools and accessible documentation",
        "Data tools engineer, 2022 to 2025",
        "Built a CSV validation service for research datasets",
        "Reduced nightly processing time from 40 minutes to 12 minutes",
        "Documentation maintainer, 2020 to 2022",
        "Maintained installation guides and tested examples on Linux",
        "Bachelor of Science in Computer Science, 2020",
        "Dataset Inspector",
        "A command-line tool that reports missing values and duplicate records",
        "Python, SQL, Git, Linux, technical writing",
    )
    for phrase in required:
        assert normalized(phrase) in text, f"Missing CV content: {phrase}"


def test_latex_intermediate():
    from pathlib import Path
    path = Path("/app/cv.tex")
    assert path.is_file(), "Missing LaTeX intermediate /app/cv.tex"
    source = path.read_text()
    assert "\\\\begin{document}" in source
    assert "\\\\end{document}" in source
    assert "Dataset Inspector" in source
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for task_id in ("tw_135505", "tw_481400"):
        source = args.tasks_dir / task_id
        target = args.output / task_id
        shutil.copytree(source, target)
        docker = target / "environment/Dockerfile"
        text = docker.read_text()
        if task_id == "tw_135505":
            start = text.index("# Install kubectl\n")
            end = text.index("# Install mock vcd", start)
            block = (
                "# Pin the binary and checksum: the v1.29 apt key expires in 2026.\n"
                "RUN curl -fsSL https://dl.k8s.io/release/v1.29.15/bin/linux/amd64/kubectl "
                "-o /usr/local/bin/kubectl \\\n"
                f" && echo '{KUBECTL_SHA256}  /usr/local/bin/kubectl' | sha256sum -c - \\\n"
                " && chmod 755 /usr/local/bin/kubectl \\\n"
                " && kubectl version --client=true\n\n"
            )
            docker.write_text(text[:start] + block + text[end:])
        else:
            assert "WORKDIR /app" in text
            docker.write_text(
                text.replace(
                    "WORKDIR /app",
                    "COPY source-cv.html /app/source-cv.html\n\nWORKDIR /app",
                )
            )
            (target / "environment/source-cv.html").write_text(CV_HTML)
            instruction = target / "instruction.md"
            header = instruction.read_text().split("\n\n", 1)[0]
            instruction.write_text(header + "\n" + CV_INSTRUCTION)
            solve = target / "solution/solve.sh"
            solve.write_text(
                solve.read_text().replace(
                    "https://p18kout.github.io/online-cv/", "/app/source-cv.html"
                )
            )
            tests = target / "tests/test_state.py"
            tests.write_text(tests.read_text() + CV_TESTS)
            (target / "tests/protected_paths.json").write_text(
                json.dumps({"paths": ["/app/source-cv.html"], "cmds": []}) + "\n"
            )
        for file in sorted(target.rglob("*")):
            if not file.is_file():
                continue
            rel = file.relative_to(target)
            before = source / rel
            new_hash = hashlib.sha256(file.read_bytes()).hexdigest()
            old_hash = (
                hashlib.sha256(before.read_bytes()).hexdigest()
                if before.exists()
                else None
            )
            if new_hash != old_hash:
                records.append(
                    {
                        "task_id": task_id,
                        "path": str(rel),
                        "before_sha256": old_hash,
                        "after_sha256": new_hash,
                    }
                )
        print(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_id": task_id,
                    "status": "staged",
                }
            ),
            flush=True,
        )
    (args.output / "changes.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()
