"""Score dumped vLLM sequences with the TorchTitan trainer model (same model spec as the
tmax 9B trainer: qwen3_5 9B, LMHeadCast fp32 lm_head, varlen/FA4 attention, fla GDN) and
compare per-token logprobs against vLLM decode/prefill. Ablations via --attn / --gdn / --dtype."""
import argparse, dataclasses, glob, json, statistics as st, torch
from safetensors.torch import load_file
ap = argparse.ArgumentParser()
ap.add_argument('seqs'); ap.add_argument('--model', required=True); ap.add_argument('--signed', type=int, default=1); ap.add_argument('--attn', default='varlen'); ap.add_argument('--gdn', default='fla_chunked')
ap.add_argument('--tf32', type=int, default=1); ap.add_argument('--fp32_keys', default=''); ap.add_argument('--pack', type=int, default=0); ap.add_argument('--n', type=int, default=0)
a = ap.parse_args()
if a.tf32:
    try: torch.backends.cuda.matmul.fp32_precision = 'tf32'
    except Exception: torch.backends.cuda.matmul.allow_tf32 = True
from torchtitan.experiments.rl.examples.swe_r2e.config_registry import _qwen3_5_rl_model_registry
M = a.model
spec = _qwen3_5_rl_model_registry('9B', attn_backend=a.attn)
def walk(o, seen=None):
    seen = set() if seen is None else seen
    if id(o) in seen or not dataclasses.is_dataclass(o): return
    seen.add(id(o))
    for f in dataclasses.fields(o):
        v = getattr(o, f.name)
        if f.name == 'rope' and v is not None and hasattr(v, 'max_seq_len'): v.max_seq_len = 65536
        if f.name == 'kernel' and hasattr(v, 'backend'): v.backend = a.gdn
        if isinstance(v, (list, tuple)):
            for x in v: walk(x, seen)
        else: walk(v, seen)
walk(spec.model)
with torch.device('meta'):
    model = spec.model.build()
model.to_empty(device='cuda')
with torch.no_grad():
    model.init_weights(buffer_device=None)   # same order as the trainer: buffers (rope caches) first, then weights
hf = {}
for f in sorted(glob.glob(M + '/*.safetensors')): hf.update(load_file(f))
sd = spec.state_dict_adapter(spec.model, M).from_hf(hf)
missing, unexpected = model.load_state_dict(sd, strict=False)
print('missing (non-vision):', [k for k in missing if 'vision' not in k][:10], 'unexpected:', unexpected[:5], flush=True)
model = model.to(torch.bfloat16).eval()
if a.fp32_keys:
    pats = a.fp32_keys.split(',')
    kept = []
    for n_, p_ in model.named_parameters():
        if any(x in n_ for x in pats) and n_ in sd:
            p_.data = sd[n_].to('cuda', torch.float32); kept.append(n_)
    print('kept fp32:', len(kept), kept[:3], 'ckpt dtypes:', {str(sd[k].dtype) for k in kept}, flush=True)
seqs = [json.loads(l) for l in open(a.seqs)]
if a.n: seqs = seqs[:a.n]
dd, dp, big = [], [], []
def batches():
    if not a.pack:
        for s in seqs: yield [s]
        return
    cur, n = [], 0
    for s in seqs:
        if cur and n + len(s['tokens']) > a.pack: yield cur; cur, n = [], 0
        cur.append(s); n += len(s['tokens'])
    if cur: yield cur
with torch.no_grad():
  for grp in batches():
    toks = sum((s['tokens'] for s in grp), [])
    pos_l = sum((list(range(len(s['tokens']))) for s in grp), [])
    if a.pack: pad = a.pack - len(toks); toks += [0] * pad; pos_l += list(range(pad))
    ids = torch.tensor([toks], device='cuda'); pos = torch.tensor([pos_l], device='cuda')
    masks = model.get_attention_masks(pos) if a.attn != 'sdpa' else None
    out = model(ids, attention_masks=masks, positions=pos)
    all_logits = (out.logits if hasattr(out, 'logits') else out)[0]
    off = 0
    for s in grp:
        logits = all_logits[off:off + len(s['tokens'])]; off += len(s['tokens'])
        spans = s['spans'] if 'spans' in s else [dict(start=s['prompt_len'], decode_lp=s['decode_lp'], prefill_lp=s['prefill_lp'])]
        for sp in spans:
            pl = sp['start']
            gen = torch.tensor(s['tokens'][pl:pl + len(sp['decode_lp'])], device='cuda')
            lp = torch.log_softmax(logits[pl - 1:pl - 1 + len(gen)].float(), -1).gather(1, gen[:, None])[:, 0].tolist()
            for k, h in enumerate(lp):
                d, p = sp['decode_lp'][k], sp['prefill_lp'][k]
                if d is not None: dd.append(d - h)
                if p is not None: dp.append(p - h)
                if d is not None and abs(d - h) > 1: big.append((abs(d - h), d, p, h, pl + k))
    del out, all_logits
A = lambda xs: (st.mean(abs(x) for x in xs), max(abs(x) for x in xs), 100 * sum(abs(x) > 1 for x in xs) / len(xs))
print(f'CONFIG pack={a.pack} attn={a.attn} gdn={a.gdn} tf32={a.tf32} seqs={len(seqs)} tokens={len(dd)}')
print('RESULT vLLM decode  vs TT: mean|d|=%.4f max=%.2f frac>1=%.3f%%' % A(dd), 'signed_mean(k1)=%.5f' % st.mean(dd))
print('RESULT vLLM prefill vs TT: mean|d|=%.4f max=%.2f frac>1=%.3f%%' % A(dp))
for b in sorted(big, reverse=True)[:8]: print('  |d|=%.2f decode=%.2f vllm_prefill=%s tt=%.2f pos=%d' % b)
