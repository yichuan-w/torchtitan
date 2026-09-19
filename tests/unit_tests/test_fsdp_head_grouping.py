# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""norm and lm_head are sharded as two FSDP groups, and the chunked lm-head
loss backward works over that layout.

One group holding both trips FSDP2's uniform-gradient-dtype check on torch
2.12: the chunked loss runs several backward passes over lm_head with
gradient sync off, which accumulates its gradient in reduce_dtype (fp32),
while norm's single-pass gradient stays in param_dtype (bf16). Newer CUDA
builds happen not to raise, so this test pins the layout on every backend
and exercises the sync-off/sync-on sequence the loss actually runs.
"""

import torch
from torch.distributed.device_mesh import init_device_mesh
from torch.distributed.fsdp import (
    FSDPModule,
    MixedPrecisionPolicy,
    fully_shard,
)
from torch.testing._internal.distributed._tensor.common_dtensor import (
    DTensorTestBase,
    with_comms,
)
from torchtitan.distributed.fsdp import apply_fsdp_to_decoder
from torchtitan.models.qwen3.model import Qwen3Model


def _build_tiny_dense_model() -> Qwen3Model:
    from torchtitan.models.common import CosSinRoPE, Embedding, Linear, RMSNorm
    from torchtitan.models.qwen3 import _build_qwen3_layers

    dim, head_dim, vocab_size = 256, 128, 2048
    return Qwen3Model(
        Qwen3Model.Config(
            vocab_size=vocab_size,
            dim=dim,
            norm=RMSNorm.Config(normalized_shape=dim),
            tok_embeddings=Embedding.Config(
                num_embeddings=vocab_size, embedding_dim=dim
            ),
            lm_head=Linear.Config(in_features=dim, out_features=vocab_size),
            layers=_build_qwen3_layers(
                n_layers=2,
                dim=dim,
                n_heads=16,
                n_kv_heads=8,
                head_dim=head_dim,
                hidden_dim=512,
                attn_backend="flex",
                rope=CosSinRoPE.Config(
                    dim=head_dim, max_seq_len=4096, theta=1000000.0
                ),
            ),
        )
    )


class TestFsdpHeadGrouping(DTensorTestBase):
    @property
    def world_size(self):
        return 2

    @with_comms
    def test_norm_and_lm_head_are_separate_fsdp_groups(self):
        dp_mesh = init_device_mesh(self.device_type, (self.world_size,))
        model = _build_tiny_dense_model().to(self.device_type)
        apply_fsdp_to_decoder(
            model,
            dp_mesh,
            param_dtype=torch.bfloat16,
            reduce_dtype=torch.float32,
            pp_enabled=False,
        )
        self.assertIsInstance(model.norm, FSDPModule)
        self.assertIsInstance(model.lm_head, FSDPModule)
        # Two groups, not one: each module holds its own parameters.
        norm_params = {id(p) for p in model.norm.parameters()}
        head_params = {id(p) for p in model.lm_head.parameters()}
        self.assertTrue(norm_params and head_params)
        self.assertFalse(norm_params & head_params)

    @with_comms
    def test_chunked_head_backward_over_two_groups(self):
        """The sequence ChunkedLossWrapper runs: gradient sync off for every
        chunk but the last, several lm_head backwards, then the norm's single
        backward. With norm and lm_head in one group this is where FSDP2
        reduce-scatters an fp32 and a bf16 gradient together.

        The decoder is not needed to exercise it, and a real one would drag in
        flex attention's block mask, so this shards a root holding just the two
        modules the way apply_fsdp_to_decoder shards them.
        """
        dp_mesh = init_device_mesh(self.device_type, (self.world_size,))
        fsdp_config = {
            "mesh": dp_mesh,
            "mp_policy": MixedPrecisionPolicy(
                param_dtype=torch.bfloat16, reduce_dtype=torch.float32
            ),
        }

        class Head(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.norm = torch.nn.LayerNorm(256)
                self.lm_head = torch.nn.Linear(256, 2048, bias=False)

            def forward(self, x):
                return self.lm_head(self.norm(x))

        model = Head().to(self.device_type)
        for module in (model.norm, model.lm_head):
            fully_shard(module, **fsdp_config)
        fully_shard(model, **fsdp_config)

        # One ordinary forward/backward first: it is what initializes the FSDP
        # root, exactly as the trainer's model forward does before the loss.
        model(torch.randn(2, 8, 256, device=self.device_type)).float().pow(
            2
        ).mean().backward()
        for p in model.parameters():
            p.grad = None

        hidden = torch.randn(
            2, 8, 256, device=self.device_type, requires_grad=True
        )
        normed = model.norm(hidden)
        detached = [c.detach().requires_grad_(True) for c in normed.chunk(4, dim=1)]

        model.lm_head.set_reshard_after_forward(False)
        model.lm_head.set_reshard_after_backward(False)
        model.lm_head.set_requires_gradient_sync(False, recurse=False)
        grads = []
        for i, chunk in enumerate(detached):
            if i == len(detached) - 1:
                model.lm_head.set_requires_gradient_sync(True, recurse=False)
            model.lm_head(chunk).float().pow(2).mean().backward()
            self.assertIsNotNone(chunk.grad)
            grads.append(chunk.grad)
        model.lm_head.set_reshard_after_forward(True)
        model.lm_head.set_reshard_after_backward(True)
        model.lm_head.set_requires_gradient_sync(True, recurse=False)
        model.lm_head.reshard()

        normed.backward(torch.cat(grads, dim=1).to(normed.dtype))

        head_grads = [p.grad for p in model.lm_head.parameters()]
        norm_grads = [p.grad for p in model.norm.parameters()]
        self.assertTrue(head_grads and all(g is not None for g in head_grads))
        self.assertTrue(norm_grads and all(g is not None for g in norm_grads))
        for g in head_grads + norm_grads:
            self.assertTrue(torch.isfinite(g.to_local()).all())


if __name__ == "__main__":
    import unittest

    unittest.main()
