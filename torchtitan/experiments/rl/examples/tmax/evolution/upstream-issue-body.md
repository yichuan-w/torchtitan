`torchtitan/experiments/rl/actors/generator.py` sets the vLLM engine block size like this:

```python
# FA2 requires block_size to be a multiple of 256
if not has_cuda_capability(9, 0):
    engine_kwargs["block_size"] = 256
```

`has_cuda_capability(9, 0)` is a `>=` check, so SM 10.x (Blackwell) also skips the override. That is fine when FA4 kernels are present, but the RL attention adapter falls back to FA2 when they are not ("FA3/FA4 not available on this CUDA architecture, falling back to FA2"), and then the engine dies at startup:

```
RuntimeError: Paged KV cache block size must be divisible by 256
```

Hit this on an 8x B300 (SM 10.3) node without the flash-attn-3/4 wheels. We ran with

```python
if not has_cuda_capability(9, 0) or has_cuda_capability(10, 0):
    engine_kwargs["block_size"] = 256
```

which unblocked training, though keying the condition on the impl actually selected (`get_cuda_flash_attention_impl()`) seems cleaner than testing raw capability twice.
