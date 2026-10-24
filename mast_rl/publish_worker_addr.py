"""Publish this worker's Monarch address on the shared mount for the controller.

``MASTJob.from_torchx`` finds worker addresses through the MAST API, which
off-network hosts (Nebius) cannot reach: the lookup fails with
PROXY_ACL_CHECK_FAILED. With ``MastLauncher.Config.attach_via_mount`` each worker
runs this before its Monarch bootstrap and writes the address that bootstrap
will listen on -- the same ``get_task_ip`` + service ProcId it uses -- to
``$MAST_RL_HOSTS_DIR/attempt<N>/<role>.<rank>``; ``MastLauncher.acquire_hosts``
reads them back.
"""

import json
import os
from pathlib import Path

from monarch._src.job.meta.task_ip import get_task_ip
from monarch._src.job.service_identity import (
    ranked_service_proc_id_from_env,
    service_proc_addr,
)
from monarch.tools.components.meta.hyperactor import MONARCH_PORT


def main() -> None:
    hosts_dir = (
        Path(os.environ["MAST_RL_HOSTS_DIR"])
        / f"attempt{os.environ.get('MAST_HPC_JOB_ATTEMPT_INDEX', '0')}"
    )
    role = os.environ["MAST_HPC_TASK_GROUP_NAME"]
    rank = os.environ["MONARCH_SERVICE_PROC_RANK"]
    record = {
        "addr": service_proc_addr(
            f"metatls://{get_task_ip()}:{MONARCH_PORT}",
            ranked_service_proc_id_from_env(),
        ),
        "group_size": int(os.environ["MAST_HPC_TASK_GROUP_SIZE"]),
    }
    hosts_dir.mkdir(parents=True, exist_ok=True)
    tmp = hosts_dir / f".{role}.{rank}.tmp"
    tmp.write_text(json.dumps(record))
    tmp.rename(hosts_dir / f"{role}.{rank}")
    print(f"published {role}.{rank}: {record['addr']}", flush=True)


if __name__ == "__main__":
    main()
