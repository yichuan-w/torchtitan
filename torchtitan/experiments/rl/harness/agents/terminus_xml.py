# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import re

from harbor.agents.terminus_2.terminus_xml_plain_parser import TerminusXMLPlainParser


class SafeTerminusXMLParser(TerminusXMLPlainParser):
    """Skip empty tag names in diagnostics while preserving raw command text."""

    def _find_top_level_tags(self, content: str) -> list[str]:
        # Match the upstream scanner's first '<' through next '>' boundaries.
        diagnostic_content = re.sub(
            r"<[^>]*>",
            lambda match: match[0] if match[0][1:-1].strip() else "",
            content,
        )
        return super()._find_top_level_tags(diagnostic_content)
