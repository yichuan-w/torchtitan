I'm training a neural network for a classification task, but the model isn't learning properly. After 10 epochs of training, it only achieves around 20% accuracy (random guessing for 5 classes) when it should reach 80%+.

The training code is in `/app/train.py` and the model architecture is in `/app/model.py`. The synthetic dataset is generated within the training script.

I've tried:
- Increasing the learning rate
- Training for more epochs
- Changing batch sizes

Nothing seems to help. The gradients are non-zero and the loss does decrease slightly, but accuracy doesn't improve.

Can you fix the training script so the model achieves **at least 80% validation accuracy**? The training script should save the trained model to `/app/trained_model.pth` and print the final validation accuracy.

The saved model should also support inference on one sample at a time after it is loaded in evaluation mode.

The training should complete within 10 epochs.
