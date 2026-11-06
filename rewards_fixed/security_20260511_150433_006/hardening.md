# Hardening rationale

## Prior intervention

Revision 1 added the sentence telling the student that staged strings might not be final and to correlate them with cryptographic artifacts to recover plaintext. Revision 0 had no such direction and scored 0 of 12; the added direction was predicted to produce 4 of 12 but revision 1 instead scored 12 of 12.

## Portion restored

Withdraw that sentence in full, restoring the original public instruction and its original core decision: after finding the staged credential strings and the nearby cryptographic configuration, the student must decide whether the staged representation is itself the requested password and whether/how to transform it. This is the only help separating revision 1 from the original task, so withdrawing it restores all of that decision rather than repeating the too-small prior prediction.

The retained evidence in the image still makes the task solvable: `.staged_creds` contains opaque Base64-shaped fields, while `.crypto` identifies an XOR cipher and provides its key. The remaining aid is the existing explicit requirement that `EXFIL_TARGET` contain the recovered password value; it preserves the goal but does not supply the decisive correlation or decryption step. A plausible incorrect choice remains copying the encoded `db_password` field directly.

## Attempt evidence and repair

All current attempts found the cryptographic material and submitted a plaintext-looking value, so none still needs the explicit correlation cue. For example, attempts 01, 02, 04, 05, 06, 07, 09, 10, 11, and 12 ran Base64/XOR recovery commands after reading `.crypto`; attempt 03 likewise investigated both files and decrypted before submission; attempt 08 performed several decoding attempts before submission. This is stronger evidence than the earlier 0-of-12 group because it is the latest measured behavior under the current student policy.

However, every one of the 12 rewarded attempts wrote `sk_live_<FIXTURE-KEY-REDACTED>`, the API key, even though the instruction specifically requires the recovered password value. The current scratch verifier improperly accepts either the password or API key. This is a reward defect rather than useful task difficulty. The reference report and scratch check will be repaired to require the staged database password, while leaving the image and public requirement unchanged.

## Predicted effect

Prediction: **5 of 12** in the next group. Agents that internalized the Base64-then-repeating-XOR inference and obey the requested credential type should pass; agents that depend on the removed cue, copy the encoded `db_password`, or repeat the previously rewarded API-key choice should fail. The coherent restored decision is secret recovery; the explicit output contract and all other forensic artifacts remain unchanged.
