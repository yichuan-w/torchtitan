#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "pandas==2.3.3",
#   "scikit-surprise==1.1.5",
# ]
# ///
import os
import pickle

import pandas as pd
from surprise import Dataset, Reader, SVD, accuracy
from surprise.model_selection import train_test_split

ratings = pd.read_csv("/app/ratings.csv")
reader = Reader(rating_scale=(1, 5))
data = Dataset.load_from_df(ratings[["userId", "movieId", "rating"]], reader)
trainset, testset = train_test_split(data, test_size=0.2, random_state=42)

model = SVD(n_factors=20, n_epochs=20, lr_all=0.005, reg_all=0.02, random_state=42)
model.fit(trainset)
predictions = model.test(testset)
print(f"RMSE: {accuracy.rmse(predictions, verbose=False):.6f}")

with open("/app/model.pkl", "wb") as model_file:
    pickle.dump(model, model_file)
print(f"Model size: {os.path.getsize('/app/model.pkl') / 1024 / 1024:.2f} MB")
