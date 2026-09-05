# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Linear that runs its matmul in a fixed compute dtype, plus the converter
that swaps the decoder lm_head to it.

RL training (logprob / KL / advantage math) is sensitive to the precision of
the lm_head logits. A bf16 matmul already accumulates in fp32 on GPU, but it
rounds each output logit back to bf16 before the softmax / log-softmax. That
per-logit bf16 rounding is what we avoid here: ``CastLinear`` casts the input
and weight to a higher-precision ``compute_dtype`` (fp32 by default) so the
logits stay in that dtype, independent of the dtype the parameters are stored
/ all-gathered in.

This lives under ``experiments/rl`` because only RL configs opt into it; core
models keep the plain ``Linear`` so LoRA / quantization converters compose
with them unchanged.
"""

from dataclasses import dataclass, fields

import os

import torch
import torch.nn.functional as F

from torchtitan.config import TORCH_DTYPE_MAP
from torchtitan.models.common.nn_modules import Linear
from torchtitan.protocols.model import ModelConfigConverter


_cast_linear_inference_cache_enabled = False


def set_cast_linear_inference_cache(enabled: bool) -> None:
    """Enable a process-local compute-dtype weight cache for ``CastLinear``."""
    global _cast_linear_inference_cache_enabled
    _cast_linear_inference_cache_enabled = enabled


def refresh_cast_linear_inference_caches(module: torch.nn.Module) -> None:
    """Refresh initialized ``CastLinear`` caches after an inference weight sync."""
    if not _cast_linear_inference_cache_enabled:
        return
    for child in module.modules():
        if isinstance(child, CastLinear):
            child.refresh_inference_weight_cache()


# Dtypes whose values TF32 represents exactly: bf16 has 7 explicit mantissa bits
# and fp16 has 10, TF32 keeps 10. An operand upcast from one of these loses
# nothing on the tensor cores, so it needs no split.
_TF32_EXACT_DTYPES = (torch.bfloat16, torch.float16)


def _tf32x3_enabled() -> bool:
    """SWE_LMHEAD_TF32X3=1 runs the fp32 lm_head matmuls -- forward and both
    backward matmuls -- on TF32 tensor cores at fp32 accuracy."""
    return os.environ.get("SWE_LMHEAD_TF32X3", "0") == "1"


def _split_tf32(t: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Split an fp32 tensor into a TF32-representable high part and its remainder.

    TF32 keeps 10 explicit mantissa bits, so masking the low 13 bits of the fp32
    significand yields a value the tensor cores represent exactly; the remainder
    carries what was dropped.
    """
    hi = (t.view(torch.int32) & -0x2000).view(torch.float32)
    return hi, t - hi


# Longest reduction one TF32 GEMM is allowed to run. Measured on a B300 against an
# fp64 reference, with operands that TF32 represents exactly, the tensor-core
# kernel's own accumulation error grows linearly with K: 9.5e-6 at K=4096,
# 1.9e-5 at 8192, 1.5e-4 at 65536, 9.3e-4 at 248320 (the vocab, i.e. the grad_x
# reduction), while the IEEE fp32 CUDA-core kernel sits at 2.1e-6 regardless.
# Splitting K and adding the partials in fp32 arithmetic holds the error at the
# K=4096 level. 4096 is also the hidden size, so the forward is one GEMM.
_TF32_MAX_K = 4096


def _tf32_mm(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """``a @ b`` on the tensor cores: TF32 inputs, fp32 accumulation, and no single
    GEMM reducing over more than ``_TF32_MAX_K`` terms (see the note above)."""
    prev = torch.get_float32_matmul_precision()
    torch.set_float32_matmul_precision("high")
    try:
        k_total = a.shape[1]
        if k_total <= _TF32_MAX_K:
            return a @ b
        out = a[:, :_TF32_MAX_K] @ b[:_TF32_MAX_K]
        for k in range(_TF32_MAX_K, k_total, _TF32_MAX_K):
            # addmm_'s epilogue adds the partial into ``out`` in fp32.
            out.addmm_(a[:, k : k + _TF32_MAX_K], b[k : k + _TF32_MAX_K])
        return out
    finally:
        torch.set_float32_matmul_precision(prev)


def _mm_fp32_on_tensor_cores(
    a: torch.Tensor, b: torch.Tensor, a_exact: bool, b_exact: bool
) -> torch.Tensor:
    """fp32-accuracy ``a @ b`` from TF32 tensor cores, in as few matmuls as the
    operands need.

    An operand flagged ``exact`` was upcast from bf16/fp16 and is already
    TF32-representable, so it goes in whole. A genuine fp32 operand is split
    into a TF32-representable high part and the remainder, so its full
    significand still reaches the product. Two exact operands take one matmul,
    and the products are exact in fp32 (7+7 bits), so the result matches an
    IEEE fp32 matmul up to accumulation order. One exact operand takes two. Two
    fp32 operands take the 3xTF32 form ``ah*bh + ah*bl + al*bh``; ``al*bl`` is
    below the fp32 significand and is dropped.
    """
    if a_exact and b_exact:
        return _tf32_mm(a, b)
    if b_exact:
        ah, al = _split_tf32(a)
        return _tf32_mm(ah, b) + _tf32_mm(al, b)
    if a_exact:
        bh, bl = _split_tf32(b)
        return _tf32_mm(a, bh) + _tf32_mm(a, bl)
    ah, al = _split_tf32(a)
    bh, bl = _split_tf32(b)
    return _tf32_mm(ah, bh) + _tf32_mm(ah, bl) + _tf32_mm(al, bh)


class _LinearTF32(torch.autograd.Function):
    """fp32-accuracy linear on tensor cores, forward and backward.

    Running ``F.linear`` under ``set_float32_matmul_precision("high")`` moves
    only the forward matmul: autograd runs the two backward matmuls later,
    outside the context, at the default precision, and on B300 those land on
    CUDA cores (``simt_sgemm``, 325 ms a launch at the 9B lm_head chunk shape
    against 23.6 ms for the tensor-core kernel). The 2026-08-31 trainer trace
    put those two launches at 28.9% of step time. Owning the backward here
    puts all three matmuls on the tensor cores.

    The trainer's lm_head operands are both bf16 before the fp32 cast (the
    hidden state from the decoder norm, the FSDP-all-gathered weight), so the
    forward is a single exact matmul. The upstream gradient is genuine fp32 and
    is the operand that gets split in backward.

    Measured on a B300 at the 9B chunk shape, [8192,4096] x [4096,248320],
    forward plus both backward matmuls (evolution/bench_lmhead_tf32.py):
    IEEE fp32 on CUDA cores 1302 ms; the earlier forward-only 3xTF32 945 ms;
    this path 223 ms; plain TF32 127 ms. Relative error against the IEEE fp32
    result: out 7.3e-6, grad_x 1.3e-5, grad_w 9.6e-6 here, against 6.2e-4 and
    2.1e-4 on the two gradients under plain TF32.
    """

    @staticmethod
    def forward(ctx, x, weight, x_exact: bool, w_exact: bool):
        ctx.save_for_backward(x, weight)
        ctx.exact = (x_exact, w_exact)
        # Flatten to [tokens, hidden] the way backward already does. _tf32_mm
        # reads the reduction width off a.shape[1]; on a [batch, seq, hidden]
        # activation that is the sequence length, so a 3-D input took the
        # split-K branch and addmm_ rejected it ("mat1 must be a matrix, got
        # 3-D tensor"). tmax's chunked loss passes exactly that shape.
        out = _mm_fp32_on_tensor_cores(
            x.reshape(-1, x.shape[-1]), weight.t(), x_exact, w_exact
        )
        return out.reshape(*x.shape[:-1], out.shape[-1])

    @staticmethod
    def backward(ctx, grad_out):
        x, weight = ctx.saved_tensors
        x_exact, w_exact = ctx.exact
        go2 = grad_out.contiguous().reshape(-1, grad_out.shape[-1])
        grad_x = grad_w = None
        if ctx.needs_input_grad[0]:
            grad_x = _mm_fp32_on_tensor_cores(go2, weight, False, w_exact)
            grad_x = grad_x.reshape(x.shape)
        if ctx.needs_input_grad[1]:
            x2 = x.reshape(-1, x.shape[-1])
            grad_w = _mm_fp32_on_tensor_cores(go2.t(), x2, False, x_exact)
        return grad_x, grad_w, None, None


def _linear_tf32x3(x, weight, bias, x_exact: bool, w_exact: bool):
    """fp32-accuracy linear on tensor cores; see ``_LinearTF32``.

    ``x_exact`` / ``w_exact`` say whether the operand was upcast from a dtype
    TF32 represents exactly (``_TF32_EXACT_DTYPES``), which decides how many
    matmuls each product needs.
    """
    if torch.is_grad_enabled():
        out = _LinearTF32.apply(x, weight, x_exact, w_exact)
    else:
        flat = _mm_fp32_on_tensor_cores(
            x.reshape(-1, x.shape[-1]), weight.t(), x_exact, w_exact
        )
        out = flat.reshape(*x.shape[:-1], flat.shape[-1])
    return out if bias is None else out + bias


class CastLinear(Linear):
    """``Linear`` whose forward matmul runs in ``compute_dtype``.

    Inputs, weight, and bias are cast to ``compute_dtype`` before
    ``F.linear`` and the output is returned in that dtype. Because the cast
    happens in ``forward`` (not on the stored parameter), this is safe under
    weight tying -- the shared embedding/lm_head parameter keeps its original
    dtype and only the lm_head matmul sees the cast.
    """

    @dataclass(kw_only=True, slots=True)
    class Config(Linear.Config):
        compute_dtype: str = "float32"
        """Dtype for the forward matmul (key into ``TORCH_DTYPE_MAP``)."""

    def __init__(self, config: Config):
        super().__init__(config)
        self.compute_dtype = TORCH_DTYPE_MAP[config.compute_dtype]
        self.register_buffer("_inference_weight_cache", None, persistent=False)

    @torch.inference_mode()
    def refresh_inference_weight_cache(self) -> None:
        """Copy the current parameter into the stable inference cache."""
        if self._inference_weight_cache is None:
            self._inference_weight_cache = self.weight.to(self.compute_dtype)
        else:
            self._inference_weight_cache.copy_(self.weight)

    def forward(self, input: torch.Tensor) -> torch.Tensor:
        if _cast_linear_inference_cache_enabled:
            if self._inference_weight_cache is None:
                if input.is_cuda and torch.cuda.is_current_stream_capturing():
                    raise RuntimeError(
                        "CastLinear inference cache must be initialized before "
                        "CUDA graph capture"
                    )
                self.refresh_inference_weight_cache()
            weight = self._inference_weight_cache
            weight_src_dtype = self.weight.dtype
        else:
            # Training updates the parameter every step, so it must cast the live
            # parameter rather than reuse the generator-only inference cache.
            weight = self.weight.to(self.compute_dtype)
            weight_src_dtype = self.weight.dtype
        bias = None if self.bias is None else self.bias.to(self.compute_dtype)
        x = input.to(self.compute_dtype)
        if _tf32x3_enabled() and x.dtype is torch.float32:
            return _linear_tf32x3(
                x,
                weight,
                bias,
                input.dtype in _TF32_EXACT_DTYPES,
                weight_src_dtype in _TF32_EXACT_DTYPES,
            )
        return F.linear(x, weight, bias)


class LMHeadCastConverter(ModelConfigConverter):
    """Swap the decoder lm_head's ``Linear.Config`` to ``CastLinear.Config``.

    Walks the model config tree and replaces the ``lm_head`` node in place.
    Targets only the lm_head, so every other Linear stays a plain ``Linear``
    and LoRA / quantization converters are unaffected.

    Note on trainer/inference bitwise parity: because the same ``model_spec``
    backs both the trainer and the vLLM generator, the lm_head sees a matched
    cast chain on both sides. The inference weight, synced from the trainer,
    goes fp32 (trainer) -> bf16 (weight-sync) -> fp32 (lm_head cast); the
    trainer's own lm_head input follows the analogous fp32 (FSDP-sharded
    params) -> bf16 (all-gather) -> fp32 (lm_head cast). Both paths share the
    same lossy bf16 round-trip, so trainer<->inference bitwise agreement is
    preserved rather than broken. TODO: investigate whether this
    fp32->bf16->fp32 cast pair can be removed (keep the weight in fp32 end to
    end) once that path is supported on both sides.
    """

    _TARGET = "lm_head"

    @dataclass(kw_only=True, slots=True)
    class Config(ModelConfigConverter.Config):
        compute_dtype: str = "float32"
        """Forward matmul dtype for the lm_head (key into ``TORCH_DTYPE_MAP``)."""

    def __init__(self, config: Config):
        self.config = config

    def convert(self, model_config):
        found = False
        for fqn, linear_config, parent, attr in model_config.traverse(Linear.Config):
            if fqn.rsplit(".", 1)[-1] != self._TARGET:
                continue
            found = True
            shared_fields = {
                f.name: getattr(linear_config, f.name) for f in fields(linear_config)
            }
            new_config = CastLinear.Config(
                **shared_fields, compute_dtype=self.config.compute_dtype
            )
            if isinstance(parent, list):
                parent[attr] = new_config
            else:
                setattr(parent, attr, new_config)
        if not found:
            raise ValueError(
                f"LMHeadCastConverter found no Linear named {self._TARGET!r} in the "
                "model config. The torchtitan decoder names its output projection "
                f"{self._TARGET!r} (see torchtitan/models/common/decoder.py)."
            )
        return model_config
