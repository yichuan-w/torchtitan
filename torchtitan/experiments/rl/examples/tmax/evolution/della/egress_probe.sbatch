#!/bin/bash
#SBATCH -A pli_xl
#SBATCH -p pli-lc
#SBATCH --qos=pli-lc
#SBATCH --gres=gpu:2
#SBATCH -t 00:05:00
#SBATCH -c 16
#SBATCH --mem=64G
#SBATCH -J egress-probe
#SBATCH -o /home/al9080/egress_probe_%j.out

PROXY=http://172.17.2.45:3129
RESULT=/home/al9080/egress_probe_result.txt
{
  echo "=== egress probe $(date -Is) host=$(hostname) job=$SLURM_JOB_ID ==="
  echo -n "tcp 172.17.2.45:3129: "
  timeout 5 bash -c 'echo > /dev/tcp/172.17.2.45/3129' && echo OK || echo BLOCKED
  echo -n "https://api.daytona.io via proxy: "
  curl -x $PROXY https://api.daytona.io -sS -o /dev/null -w "%{http_code}" --max-time 30; echo " (exit $?)"
  echo -n "https://app.daytona.io via proxy: "
  curl -x $PROXY https://app.daytona.io -sS -o /dev/null -w "%{http_code}" --max-time 30; echo " (exit $?)"
  echo -n "https://huggingface.co via proxy: "
  curl -x $PROXY https://huggingface.co -sS -o /dev/null -w "%{http_code}" --max-time 30; echo " (exit $?)"
  echo -n "https://api.wandb.ai via proxy: "
  curl -x $PROXY https://api.wandb.ai -sS -o /dev/null -w "%{http_code}" --max-time 30; echo " (exit $?)"
  echo -n "DENIED https://example.com via proxy (expect 403-from-proxy): "
  curl -x $PROXY https://example.com -sS -o /dev/null -w "%{http_code}" --max-time 30 2>&1 | head -1
  echo -n "direct no-proxy https://api.daytona.io (expect fail): "
  curl https://api.daytona.io -sS -o /dev/null -w "%{http_code}" --max-time 10 2>&1 | head -1
  echo "=== done $(date -Is) ==="
} >> "$RESULT" 2>&1
