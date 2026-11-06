# Hardening rationale

## Prior intervention

The accepted easier revision removed corruption of the SQLite magic string and file-format version bytes, leaving only the production database's page-size field damaged. It also added direct guidance to infer that field from `data.db`'s page layout, while separately repairing the hidden requirement by stating that `/app/data.db` itself must open after repair. The intervention predicted 5 of 12 successes; the measured group solved 12 of 12.

## Evidence from the current attempts

All twelve successful attempts inspect the two headers, identify the zero page-size field, write only `0x1000` at offsets 16–17, and dump the database after it opens. Examples include attempt 01 turns 2–12, attempt 07 turns 5–16, attempt 10 turns 4–12, and attempt 11 turns 4–11. The attempts consistently use the added single-field starting state and the page-layout direction. None has to decide whether other visibly abnormal header fields must also be reconstructed, because those bytes now arrive healthy.

At the same time, the current attempts demonstrate that the formerly dominant obstacle—choosing the sister database's 1024-byte page size—is no longer limiting: all twelve derive or validate 4096 from page boundaries rather than copying 1024. Restoring only the original multi-field corruption therefore withdraws one coherent half of the implementation help while retaining guidance for the observed page-size obstacle and retaining the explicit `/app/data.db` path requirement.

## Restored portion and new decision

Restore corruption of the 16-byte SQLite magic string and the two file-format version bytes. Keep the page-layout guidance and the narrowed statement that the missing internal parameter must be inferred from `data.db`; “parameter” still refers to page size, while the agent must now distinguish that inferred field from standard header constants recoverable by comparing the intact sister header and SQLite format knowledge.

The newly restored decision is which damaged bytes can safely be copied or reconstructed from the sister database and which cannot. A plausible incorrect choice permitted by the remaining aid is to repair only the page-size field (the exact successful procedure in all current attempts), leaving `data.db` unopenable because its magic and format-version bytes remain invalid. Blindly copying the entire sister header remains another incorrect choice because its page size differs.

## Predicted effect

I predict **7 of 12** attempts will succeed. The page-size inference that caused ten of twelve original failures is still explicitly focused and is now demonstrated by every current attempt, but agents that stop after the familiar two-byte patch or fail to classify the other header differences should fail. This is stronger than merely removing the guidance sentence and preserves help with the obstacle that caused the original all-failed group.
