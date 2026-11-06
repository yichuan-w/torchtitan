The file `/app/model.py` contains a custom PyTorch implementation of a transformer encoder. It defines a standard multi-layer transformer encoder with:

- Token embeddings
- Learnable positional encodings
- Multi-head scaled dot-product self-attention
- Position-wise feed-forward networks (GELU activation)
- Layer normalization
- Residual connections

The model runs without crashing, but it produces incorrect outputs due to several bugs in the implementation. Your task is to find and fix all bugs so that the model behaves as a correct transformer encoder should.

You may only edit `/app/model.py`. The model must remain compatible with the following constructor signature:

```python
TransformerEncoder(
    vocab_size=100, d_model=64, n_heads=4, d_ff=128,
    n_layers=2, max_len=64, dropout=0.0
)
```

The model takes token indices of shape `(batch, seq_len)` and returns hidden states of shape `(batch, seq_len, d_model)`. It also accepts an optional attention mask argument.
