#!/bin/bash
set -e
python3 - <<'PY'
from pathlib import Path

path = Path('/app/services/api.py')
if not path.is_file():
    raise SystemExit(f'missing service: {path}')
source = path.read_text()
source = source.replace(
    'import redis\n',
    'import redis\nfrom flask_limiter import Limiter\nfrom flask_limiter.util import get_remote_address\n',
)
source = source.replace(
    'app = Flask(__name__)\n',
    'app = Flask(__name__)\n'
    'limiter = Limiter(get_remote_address, app=app, default_limits=["12 per day"], '
    'storage_uri=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))\n',
)
source = source.replace(
    "    seq = data['sequence']\n",
    "    if 'sequences' in data:\n"
    "        sequences = data['sequences']\n"
    "        if not isinstance(sequences, list) or not sequences:\n"
    "            return jsonify({'error': 'sequences must be a non-empty list'}), 400\n"
    "        return jsonify({'scores': [get_score(sequence) for sequence in sequences]})\n"
    "    seq = data['sequence']\n",
)
path.write_text(source)
PY
