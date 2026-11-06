# Hardening rationale

## Evidence from attempts

All 12 attempts passed by recreating the absent `/libsvm/heart_scale`, installing `libsvm-tools` (or building LIBSVM), taking the requested raw `head`/`tail` splits, and invoking `svm-train -t 0` followed by `svm-predict`. Attempt 01 turns 6-18 and attempt 10 turns 5-20 show the common path. None used `svm-scale` or had to preserve a fitted feature transformation between training and validation.

The seed setup is also defective: the instruction says `/libsvm/heart_scale` is located in the workspace, but attempt 01 turns 1-2 and attempt 10 turns 1-2 find neither `/libsvm` nor `heart_scale`. Every successful attempt repairs this itself by downloading or cloning data. The rewrite ships the promised, pinned LIBSVM checkout and records this repair separately.

## Primary sources and reproduced behavior

- https://raw.githubusercontent.com/cjlin1/libsvm/v336/README — the LIBSVM README says to scale training and testing data similarly, showing `svm-scale -s range train` followed by `svm-scale -r range test`; it also documents the default `[-1,+1]` bounds.
- https://github.com/cjlin1/libsvm/commit/5b7247046e10916aee0adcc0694b695e9949a1d9 — the upstream commit states that LIBSVM requires feature indices in ascending order and changes `svm-scale` behavior for features absent from training, confirming that scaling state and sparse feature handling are real parts of this tool’s workflow.
- The environment will pin LIBSVM tag `v336` at commit `485e9a02f267317f2514bcad9a75b46d3a0a19c6` and build its command-line tools.

Before editing, I reproduced the relevant operational distinction in the sandbox with the pinned commit: fitting one range on the training split and restoring it on validation produced `Accuracy = 85% (85/100)`, while independently fitting validation scaling produced `Accuracy = 81% (81/100)`. Their prediction SHA-256 values were respectively `ede81db32b1c65d5e45571c294d28154efcdd2fd4f85768bb110cc742dfdb456` and `923015965fc1ea67cefd8b969161869427301d4d2e441cbc79b68da29aa261c5`. LIBSVM reports no error for the wrong independent fit.

Reproduction command (run through `./sandbox exec`) checked out the pinned commit, built LIBSVM, created the 170/100 splits, ran `svm-scale -s` on training and `svm-scale -r` on validation, then compared that with a separate default `svm-scale` fit on validation before training and prediction.

## Proposed change

Keep the same dataset, split, linear LIBSVM model, output paths, and prediction goal, but require feature scaling to `[-1,1]` using parameters fitted only on the training split and reused for validation. The two named split deliverables contain those scaled records, making the changed behavior observable without requiring an incidental script or log.

The new decision is how to carry training-derived scaling state into validation rather than scaling each file independently or merely training the raw data. This is one modest real-system step beyond the attempts’ demonstrated ability to install/build LIBSVM, split its sparse format, train, and predict. A wrong choice remains plausible and completes successfully with lower accuracy, so the tool does not announce the fix.

## Shortcut trials and replays

I tested the scaling warning's suggested `-l 0` adjustment while otherwise fitting on training and restoring on validation. `svm-predict` completed at 85%, but `./sandbox grade` returned reward 0: both split-content checks rejected the `[0,1]` outputs because the requested range is `[-1,1]`.

I also tested the attempts' first recovery: install `libsvm-tools`, redownload `heart_scale`, regenerate the raw 170/100 splits, train `-t 0`, and predict. Training and prediction completed at 85%, but `./sandbox grade` returned reward 0: both split-content checks rejected the unscaled files.

Fresh replay of attempt 01 follows its turns 6-18 through package installation, dataset download, raw splitting, training, and prediction. It now stops at grading with reward 0; `test_training_data_split` and `test_validation_data_split` fail because the files are unscaled. Fresh replay of attempt 10 follows the same effective commands in turns 5-20 and stops at the same two failures. The image now already provides the pinned checkout, so their preliminary missing-tool/data investigation is unnecessary, but their successful seed workflow still chooses raw splits and therefore does not satisfy the changed result.

Expected next result: **6 of 12**. The repeated raw-split path fails deterministically, while an agent that notices the scaling requirement must select `svm-scale`, preserve training-derived state with `-s`/`-r`, and avoid independently fitting validation scaling; that decision is a modest extension of the students' existing LIBSVM command-line work.
