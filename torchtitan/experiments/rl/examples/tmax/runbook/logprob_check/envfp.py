import os
import subprocess

import torch
import triton
p = torch.cuda.get_device_properties(0)
print('gpu', p.name, 'sm', p.multi_processor_count, 'cc', f'{p.major}.{p.minor}', 'mem_GB', round(p.total_memory / 2**30))
print('driver', subprocess.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'], capture_output=True, text=True).stdout.strip().splitlines()[0])
print('torch', torch.__version__, 'cuda', torch.version.cuda, 'cudnn', torch.backends.cudnn.version(), 'triton', triton.__version__)
import vllm; print('vllm', vllm.__version__)
from triton.backends.nvidia.compiler import get_ptxas
try:
    px = get_ptxas(p.major * 10 + p.minor) if 'arch' in get_ptxas.__code__.co_varnames else get_ptxas()
    path = getattr(px, 'path', px); print('ptxas', path, subprocess.run([path, '--version'], capture_output=True, text=True).stdout.strip().splitlines()[-1])
except Exception as e: print('ptxas ?', e)
print('env', {k: v for k, v in os.environ.items() if k.startswith(('TRITON_', 'TORCHINDUCTOR', 'VLLM_', 'TORCH_', 'CUDA_', 'NVIDIA_', 'PYTORCH_')) and 'TOKEN' not in k})
