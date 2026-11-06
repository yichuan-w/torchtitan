"""
Tests for the fixed transformer encoder implementation.
Verifies that the model behaves correctly using property-based checks
and comparison against a reference transformer implementation.
"""
import sys
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, '/app')

# ---------------------------------------------------------------------------
# Reference: a known-correct transformer encoder built from the same components
# ---------------------------------------------------------------------------

class RefMultiHeadAttention(nn.Module):
    def __init__(self, d_model, n_heads, dropout=0.1):
        super().__init__()
        assert d_model % n_heads == 0
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.W_q = nn.Linear(d_model, d_model)
        self.W_k = nn.Linear(d_model, d_model)
        self.W_v = nn.Linear(d_model, d_model)
        self.W_o = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, mask=None):
        B, S, D = x.shape
        q = self.W_q(x).view(B, S, self.n_heads, self.d_k).transpose(1, 2)
        k = self.W_k(x).view(B, S, self.n_heads, self.d_k).transpose(1, 2)
        v = self.W_v(x).view(B, S, self.n_heads, self.d_k).transpose(1, 2)
        attn_scores = torch.matmul(q, k.transpose(-2, -1)) / math.sqrt(self.d_k)
        if mask is not None:
            attn_scores = attn_scores.masked_fill(mask == 0, float('-inf'))
        attn_weights = F.softmax(attn_scores, dim=-1)
        attn_weights = self.dropout(attn_weights)
        out = torch.matmul(attn_weights, v)
        out = out.transpose(1, 2).contiguous().view(B, S, D)
        out = self.W_o(out)
        return out, attn_weights  # also return weights for testing


class RefFeedForward(nn.Module):
    def __init__(self, d_model, d_ff, dropout=0.1):
        super().__init__()
        self.linear1 = nn.Linear(d_model, d_ff)
        self.linear2 = nn.Linear(d_ff, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        return self.linear2(self.dropout(F.gelu(self.linear1(x))))


class RefTransformerEncoderLayer(nn.Module):
    def __init__(self, d_model, n_heads, d_ff, dropout=0.1):
        super().__init__()
        self.self_attn = RefMultiHeadAttention(d_model, n_heads, dropout)
        self.ffn = RefFeedForward(d_model, d_ff, dropout)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, mask=None):
        attn_out, _ = self.self_attn(x, mask)
        x = self.norm1(x + self.dropout(attn_out))
        ffn_out = self.ffn(x)
        x = self.norm2(x + self.dropout(ffn_out))
        return x


class RefPositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=512):
        super().__init__()
        self.pos_embedding = nn.Parameter(torch.randn(max_len, d_model))

    def forward(self, x):
        B, S, D = x.shape
        return x + self.pos_embedding[:S, :]


class RefTransformerEncoder(nn.Module):
    def __init__(self, vocab_size, d_model, n_heads, d_ff, n_layers,
                 max_len=512, dropout=0.1):
        super().__init__()
        self.token_embedding = nn.Embedding(vocab_size, d_model)
        self.pos_encoding = RefPositionalEncoding(d_model, max_len)
        self.dropout = nn.Dropout(dropout)
        self.layers = nn.ModuleList([
            RefTransformerEncoderLayer(d_model, n_heads, d_ff, dropout)
            for _ in range(n_layers)
        ])

    def forward(self, x, mask=None):
        x = self.token_embedding(x)
        x = self.pos_encoding(x)
        x = self.dropout(x)
        for layer in self.layers:
            x = layer(x, mask)
        return x


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_KWARGS = dict(
    vocab_size=100, d_model=64, n_heads=4, d_ff=128,
    n_layers=2, max_len=64, dropout=0.0,
)

SEED = 42


def _copy_matching_weights(src_model, dst_model):
    """Copy weights from src to dst for parameters with matching names and shapes."""
    src_state = src_model.state_dict()
    dst_state = dst_model.state_dict()
    copied = 0
    for key in dst_state:
        if key in src_state and dst_state[key].shape == src_state[key].shape:
            dst_state[key] = src_state[key].clone()
            copied += 1
    dst_model.load_state_dict(dst_state)
    return copied


def _compute_attention_entropy(attn_weights):
    """Compute average entropy of attention distributions. Returns float."""
    # attn_weights shape: (B, n_heads, S, S)
    # Clip for numerical stability, compute entropy
    eps = 1e-10
    p = attn_weights.clamp(min=eps)
    entropy = -(p * p.log()).sum(dim=-1).mean().item()
    return entropy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_model_outputs_match_reference():
    """The solver's model must produce identical outputs to the reference
    when initialized with the same random seed and weights."""
    from model import TransformerEncoder

    # Create both models with the same seed
    torch.manual_seed(SEED)
    ref = RefTransformerEncoder(**_MODEL_KWARGS)
    ref.eval()

    torch.manual_seed(SEED)
    solver_model = TransformerEncoder(**_MODEL_KWARGS)
    solver_model.eval()

    # Copy reference weights into solver model where shapes match
    n_copied = _copy_matching_weights(ref, solver_model)

    # If fewer than expected parameters were copied, the architecture is wrong
    expected_params = sum(1 for _ in ref.state_dict())
    assert n_copied >= expected_params - 1, (
        f"Only {n_copied} parameters matched between reference and solver model "
        f"(expected at least {expected_params - 1}). "
        f"Architecture mismatch: check parameter shapes and names."
    )

    # Test on multiple inputs
    test_inputs = [
        torch.tensor([[1, 2, 3, 4, 5, 0, 0, 0],
                       [6, 7, 8, 9, 2, 3, 1, 0]], dtype=torch.long),
        torch.tensor([[10, 20, 30, 40, 50, 60, 70, 80]], dtype=torch.long),
        torch.tensor([[5, 10, 15, 20, 25, 30, 35, 40],
                       [8, 16, 24, 32, 40, 48, 56, 64],
                       [1, 3, 5, 7, 9, 11, 13, 15]], dtype=torch.long),
        torch.tensor([[42], [42]], dtype=torch.long),
    ]

    with torch.no_grad():
        for i, inp in enumerate(test_inputs):
            ref_out = ref(inp)
            solver_out = solver_model(inp)
            diff = (ref_out - solver_out).abs().max().item()
            assert diff < 1e-5, (
                f"Output mismatch on test input {i}: max diff = {diff:.6f}. "
                f"Bugs remain in the model."
            )


def test_masked_output_matches_reference():
    """Test that the solver's model handles attention masks correctly."""
    from model import TransformerEncoder

    torch.manual_seed(SEED)
    ref = RefTransformerEncoder(**_MODEL_KWARGS)
    ref.eval()

    torch.manual_seed(SEED)
    solver_model = TransformerEncoder(**_MODEL_KWARGS)
    solver_model.eval()

    _copy_matching_weights(ref, solver_model)

    inp = torch.tensor([[1, 2, 3, 4, 5, 0, 0, 0],
                         [6, 7, 8, 9, 2, 3, 1, 0]], dtype=torch.long)
    mask = torch.ones(2, 1, 1, 8)
    mask[:, :, :, 5:] = 0

    with torch.no_grad():
        ref_out = ref(inp, mask)
        solver_out = solver_model(inp, mask)
        diff = (ref_out - solver_out).abs().max().item()
        assert diff < 1e-5, (
            f"Masked output mismatch: max diff = {diff:.6f}."
        )


def test_positional_encoding_is_per_position():
    """Verify the model uses per-position encodings, not a single shared vector."""
    from model import TransformerEncoder

    torch.manual_seed(SEED)
    model = TransformerEncoder(**_MODEL_KWARGS)
    model.eval()

    # Same token 42 at different positions
    x_pos0 = torch.tensor([[42, 0, 0, 0, 0]], dtype=torch.long)
    x_pos1 = torch.tensor([[0, 42, 0, 0, 0]], dtype=torch.long)

    with torch.no_grad():
        out0 = model(x_pos0)
        out1 = model(x_pos1)

    # Hidden state for token 42 should differ between position 0 and position 1
    diff = (out0[0, 0] - out1[0, 1]).abs().max().item()
    assert diff > 0.01, (
        f"Token 42 at position 0 vs position 1 produces nearly identical hidden states "
        f"(diff: {diff:.6f}). Positional encoding is not per-position."
    )


def test_layer_norm_applied_after_residual():
    """Verify layer norm is applied after the residual connection (post-norm),
    not before the sublayer (pre-norm).

    We use forward hooks to inspect what the LayerNorm module receives.
    In post-norm, LayerNorm receives (x + sublayer_output).
    In pre-norm, LayerNorm receives x (just the sublayer input).
    """
    from model import TransformerEncoderLayer

    torch.manual_seed(SEED)
    layer = TransformerEncoderLayer(
        d_model=64, n_heads=4, d_ff=128, dropout=0.0
    )
    layer.eval()

    # Capture inputs to norm1 using a forward hook (receives module, args, output)
    norm1_inputs = []

    def hook(module, args, output):
        # args is a tuple of input tensors; args[0] is the actual input
        norm1_inputs.append(args[0].clone())

    handle = layer.norm1.register_forward_hook(hook)

    x = torch.randn(2, 8, 64)
    with torch.no_grad():
        _ = layer(x)

    handle.remove()

    assert len(norm1_inputs) > 0, "Failed to capture LayerNorm input."

    norm_inp = norm1_inputs[0]

    # In post-norm: LayerNorm receives (x + attn_out), which is different from x
    # In pre-norm: LayerNorm receives x directly, so norm_inp ≈ x
    diff_from_x = (norm_inp - x).abs().mean().item()

    assert diff_from_x > 0.01, (
        f"LayerNorm input is nearly identical to the layer input "
        f"(diff: {diff_from_x:.6f}). This indicates pre-norm where "
        f"LayerNorm receives x directly. Should use post-norm where "
        f"LayerNorm receives (x + attention_output)."
    )
