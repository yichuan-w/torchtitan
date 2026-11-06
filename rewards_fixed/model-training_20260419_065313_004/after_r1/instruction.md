I'm training a text classifier using PyTorch with weight tying between the embedding and output projection layers. The model is defined in `model.py` and the training script is `train.py`.

The training script shows that weight tying isn't working correctly. The model is supposed to use one shared parameter for these two layers, but the tie does not survive the warm-start checkpoint load and the weights diverge during training.

Fix the issue so that the embedding and output projection layers remain the same `Parameter` with shared storage through initialization, checkpoint loading, and training. Then train the model for a few epochs and save it to `/app/model.pt`.
