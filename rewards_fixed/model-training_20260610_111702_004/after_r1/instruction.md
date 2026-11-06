There's a training script at /app/train.py that's supposed to train a binary classifier on the census income data in /app/data/train_data.csv. The script has some issues — it doesn't work correctly out of the box.

Your task is to get a working model trained and saved as /app/model.pkl. The model must achieve at least 0.865 accuracy on a held-out test set drawn from the same distribution, and the saved model file must be under 2 MB.

Loading `/app/model.pkl` must produce one fitted object with a `predict` method that accepts census feature rows in the original schema used by the training file. Keep any preprocessing needed at inference behind that prediction interface rather than requiring a caller to reconstruct it from separately saved components.

You can either fix the existing script or write your own solution from scratch. The training data is the only data you have access to — the test set is private.
