"""Multi-turn replay: N trajectories advance turn by turn (prompt_k+1 = prompt_k + completion_k +
env delta), so each turn reuses the previous turn's prefix cache (GDN 'align' mode state
checkpoints), like a tmax rollout. Decode-time logprobs are then compared against a fresh
single-pass prefill (prefix cache reset) of the final sequence. Sequences are dumped for TT scoring."""
import json, random, argparse, statistics as st, collections
ap = argparse.ArgumentParser()
ap.add_argument('--prefix_cache', type=int, default=1); ap.add_argument('--eager', type=int, default=0)
ap.add_argument('--n', type=int, default=64); ap.add_argument('--turns', type=int, default=8)
ap.add_argument('--gen', type=int, default=384); ap.add_argument('--env_max', type=int, default=1500)
ap.add_argument('--fp32_head', type=int, default=1); ap.add_argument('--attn', default='')
ap.add_argument('--out', required=True); ap.add_argument('--compile_json', default='')
ap.add_argument('--model', required=True); ap.add_argument('--rollouts', required=True)
a = ap.parse_args()
from transformers import AutoTokenizer
from vllm import LLM, SamplingParams
M = a.model
tok = AutoTokenizer.from_pretrained(M)
src = a.rollouts
trajs = []
for line in open(src):
    d = json.loads(line)
    if d.get('is_validation') or len(d['turns']) < a.turns: continue
    p0 = d['turns'][0]['prompt_messages']
    envs = []
    for t in d['turns'][:a.turns]:
        envs.append('\n'.join(m['content'] if isinstance(m['content'], str) else str(m['content']) for m in t.get('env_messages', [])))
    trajs.append((p0, envs))
random.seed(0); random.shuffle(trajs); trajs = trajs[:a.n]
IM_END = tok.convert_tokens_to_ids('<|im_end|>')
state = []
for p0, envs in trajs:
    ids = tok(tok.apply_chat_template(p0, add_generation_prompt=True, tokenize=False), add_special_tokens=False)['input_ids']
    state.append(dict(ids=ids, envs=envs, gen=[]))   # gen: list of (start, token_ids, logprobs, turn)
print('trajectories', len(state), 'first-prompt lens', sorted(len(s['ids']) for s in state)[::8], flush=True)
kw = dict(model=M, dtype='bfloat16', max_model_len=65536, gpu_memory_utilization=0.6, enable_prefix_caching=bool(a.prefix_cache),
          trust_remote_code=True, seed=0, enforce_eager=bool(a.eager), max_num_batched_tokens=8192, max_num_seqs=512)
if a.attn:
    from vllm.config import AttentionConfig
    from vllm.v1.attention.backends.registry import AttentionBackendEnum
    kw['attention_config'] = AttentionConfig(backend=AttentionBackendEnum[a.attn])
if a.fp32_head:
    from torchtitan.experiments.rl.actors.generator import _install_fp32_native_lm_head
    _install_fp32_native_lm_head()
if a.compile_json:
    kw['compilation_config'] = json.loads(a.compile_json)
print('compilation_config arg:', kw.get('compilation_config'), flush=True)
llm = LLM(**kw)
import time; t_gen = 0.0; n_gen = 0
sp = SamplingParams(temperature=1.0, top_p=1.0, max_tokens=a.gen, logprobs=0, seed=1)
for k in range(a.turns):
    t0 = time.time(); outs = llm.generate([{'prompt_token_ids': s['ids']} for s in state], sp, use_tqdm=False); t_gen += time.time() - t0; n_gen += sum(len(o.outputs[0].token_ids) for o in outs)
    for s, o in zip(state, outs):
        c = o.outputs[0]; ids = list(c.token_ids)
        lps = [next(iter(lp.values())).logprob for lp in c.logprobs]
        s['gen'].append((len(s['ids']), ids, lps, k)); s['ids'] = s['ids'] + ids
        env = tok(s['envs'][k], add_special_tokens=False)['input_ids'][:a.env_max]
        tail = ([] if ids and ids[-1] == IM_END else [IM_END])
        delta = tail + tok('\n<|im_start|>user\n', add_special_tokens=False)['input_ids'] + env + \
                tok('<|im_end|>\n<|im_start|>assistant\n', add_special_tokens=False)['input_ids']
        s['ids'] = s['ids'] + delta
    print(f'turn {k} done; mean len {st.mean(len(s["ids"]) for s in state):.0f}', flush=True)
llm.reset_prefix_cache()
per_turn = collections.defaultdict(list); allv = []; big = []
with open(a.out.replace('.json', '.seqs.jsonl'), 'w') as fh:
    for s in state:
        full = s['ids']
        r = llm.generate([{'prompt_token_ids': full}], SamplingParams(temperature=1.0, max_tokens=1, prompt_logprobs=0), use_tqdm=False)[0]
        llm.reset_prefix_cache()
        rec = dict(tokens=full, spans=[])
        for (st0, ids, lps, k) in s['gen']:
            pre = [(r.prompt_logprobs[st0 + j] or {}).get(t) for j, t in enumerate(ids)]
            pre = [x.logprob if x else None for x in pre]
            rec['spans'].append(dict(start=st0, turn=k, decode_lp=lps, prefill_lp=pre))
            for j, (d, p) in enumerate(zip(lps, pre)):
                if p is None: continue
                per_turn[k].append(d - p); allv.append(d - p)
                if abs(d - p) > 1: big.append((abs(d - p), d, p, k, st0 + j, tok.decode(ids[max(0, j - 8):j]), tok.decode([ids[j]])))
        fh.write(json.dumps(rec) + '\n')
A = lambda xs: dict(n=len(xs), mean_abs=round(st.mean(abs(x) for x in xs), 5), max_abs=round(max(abs(x) for x in xs), 3), frac_gt1_pct=round(100 * sum(abs(x) > 1 for x in xs) / len(xs), 4))
res = dict(compile=a.compile_json, gen_seconds=round(t_gen, 1), gen_tokens=n_gen, gen_tok_per_s=round(n_gen / t_gen, 1), prefix_cache=a.prefix_cache, eager=a.eager, attn=a.attn or 'auto', fp32_head=a.fp32_head, all=A(allv), per_turn={k: A(v) for k, v in sorted(per_turn.items())})
print('RESULT', json.dumps(res), flush=True)
for b in sorted(big, reverse=True)[:12]: print(f'  |d|={b[0]:.2f} decode={b[1]:.2f} prefill={b[2]:.2f} turn={b[3]} pos={b[4]} ...{b[5]!r}[[{b[6]!r}]]')
json.dump(res, open(a.out, 'w'))
