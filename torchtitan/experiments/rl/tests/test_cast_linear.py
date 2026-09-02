# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for ``CastLinear`` and ``LMHeadCastConverter``."""

from __future__ import annotations

import pytest
import torch
import torch.nn.functional as F

from torchtitan.experiments.rl.models.cast_linear import (
    CastLinear,
    LMHeadCastConverter,
    refresh_cast_linear_inference_caches,
    set_cast_linear_inference_cache,
)
from torchtitan.models.common.nn_modules import Linear
from torchtitan.models.qwen3 import qwen3_configs


def _qwen3_config():
    """A real decoder config tree to exercise the converter's traversal."""
    return qwen3_configs["0.6B"](attn_backend="flex")


def test_converter_swaps_only_lm_head():
    cfg = _qwen3_config()
    assert isinstance(cfg.lm_head, Linear.Config)
    assert not isinstance(cfg.lm_head, CastLinear.Config)
    # The model has many plain Linears besides the lm_head (attention, ffn).
    num_linears_before = sum(1 for _ in cfg.traverse(Linear.Config))
    assert num_linears_before > 1

    LMHeadCastConverter.Config().build().convert(cfg)

    # Only the lm_head was swapped to CastLinear.
    assert isinstance(cfg.lm_head, CastLinear.Config)
    casted = [fqn for fqn, _, _, _ in cfg.traverse(CastLinear.Config)]
    assert casted == ["lm_head"]
    # The swap doesn't add or drop Linear nodes (CastLinear.Config is a
    # Linear.Config subclass, so the total count is unchanged).
    assert sum(1 for _ in cfg.traverse(Linear.Config)) == num_linears_before


def test_converter_returns_model_config():
    cfg = _qwen3_config()

    converted = LMHeadCastConverter.Config().build().convert(cfg)

    assert converted is cfg
    assert isinstance(converted.lm_head, CastLinear.Config)


def test_converter_preserves_linear_fields():
    cfg = _qwen3_config()
    before = cfg.lm_head

    LMHeadCastConverter.Config().build().convert(cfg)

    after = cfg.lm_head
    assert after.in_features == before.in_features
    assert after.out_features == before.out_features
    assert after.bias == before.bias
    assert after.param_init is before.param_init
    assert after.sharding_config is before.sharding_config
    assert after.compute_dtype == "float32"


def test_forward_matmul_runs_in_fp32():
    lm_head = CastLinear.Config(in_features=8, out_features=16).build()
    lm_head = lm_head.to(torch.bfloat16)
    x = torch.randn(2, 4, 8, dtype=torch.bfloat16)

    out = lm_head(x)

    # Output is fp32 while the stored weight stays bf16 -- the cast lives in
    # forward, so a tied embedding weight keeps its storage dtype.
    assert out.dtype == torch.float32
    assert lm_head.weight.dtype == torch.bfloat16

    # Bitwise-matches an explicit fp32 matmul of the same bf16 operands...
    ref_fp32 = F.linear(x.float(), lm_head.weight.float())
    assert torch.equal(out, ref_fp32)
    # ...and differs from a bf16-accumulated matmul, proving the cast matters.
    ref_bf16 = F.linear(x, lm_head.weight)
    assert not torch.equal(out, ref_bf16.float())


def test_compute_dtype_is_configurable():
    cfg = _qwen3_config()
    LMHeadCastConverter.Config(compute_dtype="bfloat16").build().convert(cfg)
    assert cfg.lm_head.compute_dtype == "bfloat16"

    lm_head = cfg.lm_head.build().to(torch.bfloat16)
    x = torch.randn(2, 4, cfg.lm_head.in_features, dtype=torch.bfloat16)
    out = lm_head(x)
    # compute_dtype=bfloat16 is a pure bf16 matmul (matches plain Linear).
    assert out.dtype == torch.bfloat16
    assert torch.equal(out, F.linear(x, lm_head.weight))


def test_weight_tying_is_dtype_safe():
    # Emulate Decoder weight tying: the embedding shares the lm_head weight.
    lm_head = CastLinear.Config(in_features=8, out_features=16).build()
    lm_head = lm_head.to(torch.bfloat16)
    embedding = torch.nn.Embedding(16, 8).to(torch.bfloat16)
    embedding.weight = lm_head.weight

    x = torch.randn(2, 4, 8, dtype=torch.bfloat16)
    out = lm_head(x)

    # The shared parameter is untouched (still bf16) even though the lm_head
    # matmul ran in fp32, so the embedding lookup is unaffected.
    assert out.dtype == torch.float32
    assert embedding.weight is lm_head.weight
    assert embedding.weight.dtype == torch.bfloat16


def test_inference_cache_refreshes_in_place_after_weight_sync():
    set_cast_linear_inference_cache(True)
    try:
        lm_head = CastLinear.Config(in_features=8, out_features=16).build()
        lm_head = lm_head.to(torch.bfloat16)
        x = torch.randn(2, 4, 8, dtype=torch.bfloat16)

        # vLLM initializes the cache while profiling under inference_mode.
        with torch.inference_mode():
            initial = lm_head(x)
        cache = lm_head._inference_weight_cache
        assert cache is not None
        assert cache.dtype == torch.float32
        assert "_inference_weight_cache" not in lm_head.state_dict()

        with torch.no_grad():
            lm_head.weight.add_(1)
        assert torch.equal(lm_head(x), initial)

        refresh_cast_linear_inference_caches(lm_head)
        assert lm_head._inference_weight_cache.data_ptr() == cache.data_ptr()
        assert torch.equal(lm_head(x), F.linear(x.float(), lm_head.weight.float()))
    finally:
        set_cast_linear_inference_cache(False)


def test_converter_raises_when_lm_head_absent():
    # A config tree with no lm_head should be a hard error rather than a
    # silent no-op (e.g. if the output projection were ever renamed).
    cfg = _qwen3_config()
    cfg.lm_head = None
    with pytest.raises(ValueError, match="lm_head"):
        LMHeadCastConverter.Config().build().convert(cfg)


# --- SWE_LMHEAD_TF32X3: fp32-accuracy lm_head on tensor cores, fwd and bwd ---

from torchtitan.experiments.rl.models.cast_linear import (  # noqa: E402
    _LinearTF32,
    _mm_fp32_on_tensor_cores,
    _split_tf32,
    _tf32_mm,
)

_needs_cuda = pytest.mark.skipif(
    not torch.cuda.is_available(), reason="needs TF32 tensor cores"
)


def _rel_err(got: torch.Tensor, ref: torch.Tensor) -> float:
    return ((got - ref).norm() / ref.norm()).item()


def test_split_tf32_is_exact_for_bf16_upcast():
    # The premise behind the single-matmul forward: a value that came from bf16
    # (7 mantissa bits) is already TF32-representable (10 bits), so its
    # remainder is exactly zero. A genuine fp32 value leaves a remainder.
    t = torch.randn(64, 64, dtype=torch.bfloat16).float()
    hi, lo = _split_tf32(t)
    assert torch.equal(hi, t)
    assert torch.count_nonzero(lo) == 0
    t32 = torch.randn(64, 64, dtype=torch.float32)
    hi, lo = _split_tf32(t32)
    assert torch.equal(hi + lo, t32)
    assert torch.count_nonzero(lo) > 0


def _ieee_fp32_reference(x32, w32, go):
    """Forward and both backward matmuls at IEEE fp32 (no TF32), via autograd."""
    prev = torch.get_float32_matmul_precision()
    torch.set_float32_matmul_precision("highest")
    try:
        xr = x32.detach().clone().requires_grad_(True)
        wr = w32.detach().clone().requires_grad_(True)
        out = F.linear(xr, wr)
        out.backward(go)
        return out.detach(), xr.grad, wr.grad
    finally:
        torch.set_float32_matmul_precision(prev)


@_needs_cuda
@pytest.mark.parametrize("x_src,w_src", [
    (torch.bfloat16, torch.bfloat16),  # the trainer's case: both operands from bf16
    (torch.float32, torch.bfloat16),   # fp32 hidden state against a bf16 weight
    (torch.float32, torch.float32),    # nothing exact: the 3xTF32 split on both
])
def test_linear_tf32_matches_ieee_fp32_forward_and_backward(x_src, w_src):
    torch.manual_seed(0)
    dev = "cuda"
    x32 = torch.randn(2, 128, 256, device=dev, dtype=x_src).float().requires_grad_(True)
    w32 = torch.randn(1024, 256, device=dev, dtype=w_src).float().requires_grad_(True)
    go = torch.randn(2, 128, 1024, device=dev, dtype=torch.float32)  # full-mantissa fp32
    x_exact = x_src in (torch.bfloat16, torch.float16)
    w_exact = w_src in (torch.bfloat16, torch.float16)

    out = _LinearTF32.apply(x32, w32, x_exact, w_exact)
    out.backward(go)
    ref_out, ref_gx, ref_gw = _ieee_fp32_reference(x32, w32, go)

    # fp32-accuracy from the tensor cores: within 1e-5 of the IEEE fp32 result
    # on the forward and on BOTH backward matmuls. Plain TF32 sits near 1e-3
    # here (see test below), so this bound is what separates the two.
    assert _rel_err(out, ref_out) < 1e-5
    assert _rel_err(x32.grad, ref_gx) < 1e-5
    assert _rel_err(w32.grad, ref_gw) < 1e-5


@_needs_cuda
def test_plain_tf32_is_not_fp32_accurate_on_fp32_operands():
    # Shows the split is doing work: the same matmul with plain TF32 inputs
    # (no split) is ~1e-3 off, two orders above the bound the split meets.
    torch.manual_seed(0)
    a = torch.randn(512, 256, device="cuda")
    b = torch.randn(256, 1024, device="cuda")
    prev = torch.get_float32_matmul_precision()
    try:
        torch.set_float32_matmul_precision("highest")
        ref = a @ b
        torch.set_float32_matmul_precision("high")
        plain = a @ b
    finally:
        torch.set_float32_matmul_precision(prev)
    split = _mm_fp32_on_tensor_cores(a, b, False, False)
    assert _rel_err(plain, ref) > 1e-4
    assert _rel_err(split, ref) < 1e-5


@_needs_cuda
def test_cast_linear_tf32x3_end_to_end_with_bf16_parameter(monkeypatch):
    # The trainer's shape of things: bf16 parameter, bf16 hidden state, fp32
    # loss gradient. Output is fp32 and matches the IEEE fp32 lm_head; the
    # gradient reaches the bf16 parameter.
    monkeypatch.setenv("SWE_LMHEAD_TF32X3", "1")
    torch.manual_seed(0)
    lm_head = CastLinear.Config(in_features=256, out_features=1024, bias=False).build()
    lm_head = lm_head.to("cuda", torch.bfloat16)
    x = torch.randn(2, 128, 256, device="cuda", dtype=torch.bfloat16, requires_grad=True)
    out = lm_head(x)
    assert out.dtype == torch.float32
    go = torch.randn_like(out)
    out.backward(go)
    ref_out, ref_gx, ref_gw = _ieee_fp32_reference(x.float(), lm_head.weight.float(), go)
    assert _rel_err(out, ref_out) < 1e-5
    assert lm_head.weight.grad is not None and lm_head.weight.grad.dtype == torch.bfloat16
    # The parameter gradient is rounded to bf16 by the cast either way; compare
    # against the reference rounded the same way.
    assert _rel_err(lm_head.weight.grad.float(), ref_gw.to(torch.bfloat16).float()) < 1e-2
    assert _rel_err(x.grad.float(), ref_gx.to(torch.bfloat16).float()) < 1e-2


@_needs_cuda
def test_cast_linear_tf32x3_no_grad_path(monkeypatch):
    monkeypatch.setenv("SWE_LMHEAD_TF32X3", "1")
    lm_head = CastLinear.Config(in_features=256, out_features=1024, bias=False).build()
    lm_head = lm_head.to("cuda", torch.bfloat16)
    x = torch.randn(2, 128, 256, device="cuda", dtype=torch.bfloat16)
    with torch.no_grad():
        out = lm_head(x)
    ref_out, _, _ = _ieee_fp32_reference(
        x.float().requires_grad_(True), lm_head.weight.float().requires_grad_(True),
        torch.zeros(2, 128, 1024, device="cuda"),
    )
    assert out.dtype == torch.float32
    assert _rel_err(out, ref_out) < 1e-5


@_needs_cuda
def test_tf32_mm_long_reduction_stays_fp32_accurate():
    # grad_x reduces over the vocab (248320 terms). One TF32 GEMM over that K
    # measured 9.3e-4 off an fp64 reference on B300 even with TF32-exact inputs;
    # _tf32_mm splits the reduction so the result stays at the K=4096 level.
    torch.manual_seed(0)
    a = torch.randn(64, 248320, device="cuda", dtype=torch.bfloat16).float()
    b = (torch.randn(248320, 256, device="cuda", dtype=torch.bfloat16) * 0.02).float()
    ref = a.double() @ b.double()
    got = _tf32_mm(a, b)
    assert ((got.double() - ref).norm() / ref.norm()).item() < 3e-5
