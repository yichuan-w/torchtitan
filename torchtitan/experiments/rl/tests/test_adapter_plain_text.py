"""The adapter's plain-text reply mode (``plain_text`` in the request body), used
by text-only harnesses such as miniswe. Needs the Qwen3.5 tokenizer locally."""

import pytest

transformers = pytest.importorskip("transformers")
renderers = pytest.importorskip("renderers")

from torchtitan.experiments.rl.harness.adapters.anthropic import (  # noqa: E402
    _completion_to_blocks,
    _plain_text_blocks,
)


@pytest.fixture(scope="module")
def renderer():
    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            "hamishivi/Qwen3.5-9B", local_files_only=True
        )
    except OSError:
        pytest.skip("Qwen3.5 tokenizer not cached")
    return renderers.create_renderer(tokenizer, renderers.config_from_name("qwen3.5"))


def _ids(renderer, text):
    return renderer._tokenizer.encode(text, add_special_tokens=False)


def test_a_bash_block_after_a_stray_tool_call_token_stays_in_the_text(renderer):
    ids = _ids(renderer, "<think>\nplan\n</think>\n\n<tool_call>\n```bash\nls /app\n```<|im_end|>")
    assert _completion_to_blocks(renderer, ids, None)[0] == [
        {"type": "thinking", "thinking": "plan"}
    ]
    assert _plain_text_blocks(renderer, ids) == [
        {"type": "thinking", "thinking": "plan"},
        {"type": "text", "text": "<tool_call>\n```bash\nls /app\n```"},
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("<think>\nx\n</think>\n\nThoughts.\n\n```bash\npwd\n```<|im_end|>", "Thoughts.\n\n```bash\npwd\n```"),
        ("<think>\nthinking that never closes", ""),
        ("Okay<|im_end|>", "Okay"),
    ],
)
def test_plain_text_matches_the_parser_without_a_tool_call(renderer, text, expected):
    blocks = _plain_text_blocks(renderer, _ids(renderer, text))
    assert blocks[-1] == {"type": "text", "text": expected}
