I'm training a text classifier using PyTorch with weight tying between the embedding and output projection layers. The model is defined in `model.py` and the training script is `train.py`.

The training script shows that weight tying isn't working correctly - the weights don't share the same storage after initialization. The model is supposed to share parameters between these two layers, but they're diverging during training.

Fix the issue so that the embedding and output projection layers properly share parameters, then train the model for a few epochs and save it to `/app/model.pt`.
