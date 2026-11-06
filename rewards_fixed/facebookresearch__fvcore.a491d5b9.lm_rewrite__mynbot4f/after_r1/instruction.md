# to_numpy function breaks image transformations

## Description

The `to_numpy` function in `fvcore/transforms/transform_util.py` is not correctly handling tensor to numpy array conversion, which causes image transformations to fail. Specifically, the `ScaleTransform` operations are failing when applied to images and segmentation maps.

## Reproduction

The issue can be reproduced with the following code:

```python
import numpy as np
import torch
from fvcore.transforms import ScaleTransform

# Create a test image
img = np.random.rand(10, 20, 3).astype(np.float32)

# Try to scale the image
transform = ScaleTransform(10, 20, 20, 20, "nearest")
result = transform.apply_image(img)
# This will fail
```

The problem occurs during the conversion from PyTorch tensor back to numpy array in the `to_numpy` function. The function is not correctly handling the tensor shape and dimensions, causing the transformation to fail.

## Expected behavior

The transformation should correctly scale the image and return a numpy array with the expected shape and values.

## Actual behavior

The transformation fails with errors related to tensor shape mismatch or incorrect dimension handling. The issue is in the `to_numpy` function which doesn't properly convert the tensor back to the expected numpy array format.

## Environment details

- Python 3.10
- PyTorch 2.6
- fvcore latest version
