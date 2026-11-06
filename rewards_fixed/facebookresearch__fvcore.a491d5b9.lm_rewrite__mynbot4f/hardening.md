# Hardening rationale

## Observed strategy

The 12 successful attempts all inspect `fvcore/transforms/transform_util.py`, reproduce the resize failure, and replace `to_numpy` wholesale. For example, attempt 01 does this at turns 4, 8, and 10; attempt 08 does it at turns 4, 8, and 9. Their replacement implementations restore the dimensions but convert floating-point results with NumPy `astype(np.uint8)`. That truncates fractional interpolation results rather than preserving fvcore's existing `round().byte()` conversion semantics. Several attempts also reinterpret arrays whose maximum is at most 1 as normalized images and multiply them by 255, although `to_numpy` receives interpolation output in the input image's original numeric range.

The seed verifier rewards this because its scale-transform expected result calls the submitted `to_numpy` too. Thus the observed strategy only has to make shape handling self-consistent; it is not checked against an independently expected `uint8` result. This is a reward-hack defect in the grading setup, not a defect in an otherwise intended solution.

## Upstream evidence and pinned artifact

- https://raw.githubusercontent.com/pytorch/pytorch/v2.6.0/torch/_torch_docs.py — the `torch.round` documentation says: “This function implements the \"round half to even\" to break ties when a number is equidistant from two integers (e.g. `round(2.5)` is 2).”
- https://api.github.com/repos/pytorch/pytorch/git/ref/tags/v2.6.0 — the upstream `v2.6.0` tag resolves to commit `1eba9b3aa3c43f86f4a2c807ac8e12c4a7767340`.
- https://raw.githubusercontent.com/pytorch/vision/v0.21.0/torchvision/transforms/v2/functional/_misc.py — the matching torchvision implementation restores a non-floating image result with `output.round_().to(dtype=dtype)`, demonstrating that explicit rounding is part of image-operation integer conversion rather than plain truncating conversion.

The image already contains the upstream `torch==2.6.0` distribution. The Dockerfile will pin and verify that exact installed distribution without downloading or replacing the large wheel.

## Reproduction in this environment

With the image's `testbed` environment active, `python -c 'import torch; print(torch.__version__); print(torch.tensor([0.5, 1.5, 2.5, 3.5]).round())'` printed `2.6.0+cu124` and `tensor([0., 2., 2., 4.])`. This confirms the documented half-to-even behavior in the actual runtime. The planted converter currently fails earlier with `ValueError: cannot reshape array of size 9 into shape (2,2)` when a 2×2 `uint8` image is resized to 3×3.

## Proposed change and new decision

Keep the task's wording and original source defect. Add an independently expected `uint8` conversion case to the repository's shipped transform utility tests, and make the scratch verifier run it. A student still has the same goal—repair `to_numpy` so scale transformations return the expected shapes and values—but must now decide how floating interpolation values convert back to integer images. Copying the successful attempts' NumPy cast is no longer sufficient; the student must inspect the original contract, test fractional values, or recognize PyTorch's integer image conversion semantics. This is one modest step beyond the attempts' demonstrated tensor-layout repair.

## Announced and first-attempt fixes

- On a fresh revised image, I removed only the final `reshape`, the operation named by the current exception. Grade was 0: both inherited scale tests passed, but `test_uint8_rounding` failed because NumPy's cast truncated the fractional samples.
- On another fresh revised image, I replayed attempt 01 turn 10's full transpose-plus-NumPy-cast implementation. Grade was 0 at the same independent value check. Thus the exception's immediate suggestion and the first implementation used by the measured attempts do not announce the intended fix.

## Student replays and prediction

I replayed attempt 01 turn 10 and attempt 12 turn 22 from separate fresh resets. Both reached the point where the old scale-image and scale-segmentation checks pass, then stopped at the independent `uint8` value check. The failed check was `tests/test_transform_util.py::TestTransformUtil::test_uint8_rounding` in each replay; no other check failed. Since all 12 current attempts use this conversion strategy, but several explicitly notice the original `round().byte()` implementation and could preserve it after seeing a focused value failure, I predict **6 of 12** for the next group.
