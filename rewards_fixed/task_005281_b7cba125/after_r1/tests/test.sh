#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py
import os
import sys
import subprocess
import ast
import numpy as np
import pytest

def expected_log_likelihood(data, mu, sigma):
    if sigma <= 0: return -np.inf
    return np.sum(-0.5 * np.log(2 * np.pi * sigma**2) - ((data - mu)**2) / (2 * sigma**2))

def expected_sample(data, iters=5000, seed=42):
    np.random.seed(seed)
    mu, sigma = 0.0, 1.0
    chain = []
    ll_current = expected_log_likelihood(data, mu, sigma)
    for _ in range(iters):
        mu_prop = mu + np.random.normal(0, 0.1)
        sigma_prop = sigma + np.random.normal(0, 0.1)
        ll_prop = expected_log_likelihood(data, mu_prop, sigma_prop)
        if sigma_prop > 0:
            log_accept_ratio = ll_prop - ll_current
            accept_prob = np.exp(min(0, log_accept_ratio))
        else:
            accept_prob = 0.0
        if np.random.rand() < accept_prob:
            mu, sigma = mu_prop, sigma_prop
            ll_current = ll_prop
        chain.append((mu, sigma))
    return np.array(chain)

def test_mcmc_py_fixed():
    mcmc_path = '/home/user/mcmc.py'
    assert os.path.isfile(mcmc_path), f"{mcmc_path} is missing"
    with open(mcmc_path, 'r') as f:
        content = f.read()
    assert "set(" not in content, "The non-deterministic 'set()' bug is still present in mcmc.py"

    tree = ast.parse(content)
    likelihood = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'log_likelihood'), None)
    loop_nodes = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    assert likelihood is not None and not any(isinstance(node, loop_nodes) for node in ast.walk(likelihood)), "log_likelihood must use vectorized NumPy operations rather than a Python reduction loop"

def test_seeded_sample_uses_local_rng_and_preserves_global_state(monkeypatch):
    sys.path.insert(0, '/home/user')
    try:
        sys.modules.pop('mcmc', None)
        import mcmc
    finally:
        sys.path.pop(0)

    np.random.seed(8675309)
    state_before = np.random.get_state()
    calls_to_global_rng = []
    def reject_global_rng(*args, **kwargs):
        calls_to_global_rng.append((args, kwargs))
        raise AssertionError("sample used NumPy's process-global random stream")
    monkeypatch.setattr(np.random, 'seed', reject_global_rng)
    monkeypatch.setattr(np.random, 'normal', reject_global_rng)
    monkeypatch.setattr(np.random, 'rand', reject_global_rng)
    error = None
    try:
        mcmc.sample(np.array([4.0, 5.0, 6.0]), iters=8, seed=42)
    except Exception as exc:
        error = exc
    state_after = np.random.get_state()
    assert error is None and not calls_to_global_rng, f"seeded sample must use a call-local random stream: {error}"
    assert state_before[0] == state_after[0] and np.array_equal(state_before[1], state_after[1]) and state_before[2:] == state_after[2:], "seeded sample altered NumPy's process-global random state"

def test_only_numpy_external_dependency_is_used():
    local_modules = {'mcmc', 'run_sampler', 'test_reproducibility'}
    forbidden = set()
    for path in ('/home/user/mcmc.py', '/home/user/run_sampler.py', '/home/user/test_reproducibility.py'):
        if not os.path.isfile(path):
            continue
        with open(path, 'r') as source_file:
            tree = ast.parse(source_file.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split('.')[0]]
            else:
                continue
            forbidden.update(name for name in names if name != 'numpy' and name not in local_modules and name not in sys.stdlib_module_names)
    if forbidden:
        pytest.fail(f"External libraries other than numpy are not allowed: {sorted(forbidden)}")

def test_run_sampler_exists_and_runs():
    script_path = '/home/user/run_sampler.py'
    assert os.path.isfile(script_path), f"{script_path} is missing"

    # Remove results.csv if it exists to ensure the script generates it
    if os.path.exists('/home/user/results.csv'):
        os.remove('/home/user/results.csv')

    result = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
    assert result.returncode == 0, f"run_sampler.py failed to execute:\n{result.stderr}"
    assert os.path.isfile('/home/user/results.csv'), "run_sampler.py did not generate /home/user/results.csv"

def test_results_csv_correctness():
    data_path = '/home/user/data.npy'
    assert os.path.isfile(data_path), f"{data_path} is missing"

    data = np.load(data_path)
    chain = expected_sample(data, 5000, 42)
    expected_mu_mean = np.mean(chain[1000:, 0])
    expected_sigma_mean = np.mean(chain[1000:, 1])

    results_path = '/home/user/results.csv'
    assert os.path.isfile(results_path), f"{results_path} is missing"

    with open(results_path, 'r') as f:
        content = f.read().strip()

    parts = content.split(',')
    assert len(parts) == 2, f"results.csv should contain exactly two comma-separated values, got: {content}"

    try:
        actual_mu_mean = float(parts[0])
        actual_sigma_mean = float(parts[1])
    except ValueError:
        pytest.fail(f"Could not parse results.csv values as floats: {content}")

    assert np.isclose(actual_mu_mean, expected_mu_mean, rtol=1e-4), f"mu_mean is incorrect. Expected approx {expected_mu_mean}, got {actual_mu_mean}"
    assert np.isclose(actual_sigma_mean, expected_sigma_mean, rtol=1e-4), f"sigma_mean is incorrect. Expected approx {expected_sigma_mean}, got {actual_sigma_mean}"

def test_reproducibility_script():
    script_path = '/home/user/test_reproducibility.py'
    assert os.path.isfile(script_path), f"{script_path} is missing"

    result = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
    assert result.returncode == 0, f"test_reproducibility.py failed or chains did not match:\n{result.stderr}"

    mcmc_path = '/home/user/mcmc.py'
    with open(mcmc_path, 'rb') as source_file:
        original_mcmc = source_file.read()
    call_log = '/tmp/reproducibility_calls.txt'
    if os.path.exists(call_log):
        os.remove(call_log)
    stub = '''import numpy as np
call_count = 0
def sample(data, iters=5000, seed=None):
    global call_count
    call_count += 1
    with open("/tmp/reproducibility_calls.txt", "a") as call_file:
        call_file.write(f"{iters},{seed}\\n")
    return np.full((iters, 2), call_count, dtype=float)
'''
    try:
        with open(mcmc_path, 'w') as source_file:
            source_file.write(stub)
        mismatch = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
    finally:
        with open(mcmc_path, 'wb') as source_file:
            source_file.write(original_mcmc)
    calls = []
    if os.path.isfile(call_log):
        with open(call_log) as call_file:
            calls = [line.strip() for line in call_file if line.strip()]
    assert mismatch.returncode != 0 and calls == ['100,42', '100,42'], "test_reproducibility.py must run two seed=42, iters=100 chains and assert exact equality"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
