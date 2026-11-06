#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py
import pytest
import requests
import numpy as np
from scipy.spatial.distance import cdist

# Constants
HOST = "127.0.0.1"
PORT = 8050
BASE_URL = f"http://{HOST}:{PORT}"
TOKEN = "gp-data-token-2024"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# Ground truth computation
N = 40
lam = 0.005
x = np.linspace(0, 1, N)
y = np.linspace(0, 1, N)
X, Y = np.meshgrid(x, y)
points = np.column_stack((X.ravel(), Y.ravel()))

def get_expected_trace(quad_name):
    if quad_name == "bottom_left":
        mask = (points[:, 0] <= 0.5) & (points[:, 1] <= 0.5)
    elif quad_name == "bottom_right":
        mask = (points[:, 0] > 0.5) & (points[:, 1] <= 0.5)
    elif quad_name == "top_left":
        mask = (points[:, 0] <= 0.5) & (points[:, 1] > 0.5)
    elif quad_name == "top_right":
        mask = (points[:, 0] > 0.5) & (points[:, 1] > 0.5)
    else:
        raise ValueError("Invalid quadrant")

    sub_points = points[mask]
    dist_sq = cdist(sub_points, sub_points, metric='sqeuclidean')
    K = np.exp(-dist_sq / 0.1)
    K_reg = K + lam * np.eye(len(sub_points))
    L = np.linalg.cholesky(K_reg)
    return np.trace(L)

def test_unauthorized_access():
    """Verify that the service requires proper authentication."""
    url = f"{BASE_URL}/factorize"
    payload = {"quadrant": "bottom_left"}

    # Missing token
    try:
        response = requests.post(url, json=payload, timeout=5)
    except requests.exceptions.ConnectionError:
        pytest.fail("Service is not running or not reachable on 127.0.0.1:8050")

    assert response.status_code in [401, 403], f"Expected 401 or 403 for missing token, got {response.status_code}"

    # Incorrect token
    bad_headers = {"Authorization": "Bearer bad-token"}
    response = requests.post(url, json=payload, headers=bad_headers, timeout=5)
    assert response.status_code in [401, 403], f"Expected 401 or 403 for incorrect token, got {response.status_code}"

@pytest.mark.parametrize("quadrant", ["bottom_left", "top_right"])
def test_factorize_endpoint(quadrant):
    """Verify that the factorize endpoint returns the correct trace."""
    url = f"{BASE_URL}/factorize"
    payload = {"quadrant": quadrant}

    try:
        response = requests.post(url, json=payload, headers=HEADERS, timeout=5)
    except requests.exceptions.ConnectionError:
        pytest.fail("Service is not running or not reachable on 127.0.0.1:8050")

    assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}. Response: {response.text}"

    try:
        data = response.json()
    except ValueError:
        pytest.fail(f"Response is not valid JSON: {response.text}")

    assert "trace" in data, f"Response JSON missing 'trace' key: {data}"

    actual_trace = data["trace"]
    expected_trace = get_expected_trace(quadrant)

    assert isinstance(actual_trace, (int, float)), f"Expected trace to be a number, got {type(actual_trace)}"
    assert np.isclose(actual_trace, expected_trace, atol=1e-4), \
        f"Trace mismatch for {quadrant}. Expected ~{expected_trace:.4f}, got {actual_trace:.4f}"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
