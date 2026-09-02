# FA4 hang probes

Diagnostics from the FlashAttention-4 backward-pass hang investigation on the
B300 training box (sm103), run by hand on della-tridao. Each script carries its
own header saying what it measures; the `_fa4_*.py` files are helpers the
others import by path. `rl_restore_working_env.sh` and `rl_settle_env.sh` put
the training venv back to a chosen state after these experiments.

They lived in `terminalworld-seeds/scripts/` until 2026-09-02 (seeds e9b7763)
and moved here when that repository stopped holding code. Nothing in the
training recipe imports them.
