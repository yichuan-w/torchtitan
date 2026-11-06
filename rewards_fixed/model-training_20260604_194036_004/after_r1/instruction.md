Train a movie rating prediction model on the ratings data in `/app/ratings.csv`.

The file is comma-separated with a header row: `userId,movieId,rating,timestamp`. Ratings are on a 1–5 scale. There are 100,000 ratings from 943 users on 1,682 movies.

Your model must:

- Achieve RMSE below 0.914 on a held-out 20% test split (use `random_state=42`)
- Be saved as `/app/model.pkl`
- The saved model file must be under 3 MB

Use matrix factorization (SVD) for collaborative filtering. The `scikit-surprise` library is recommended but not pre-installed.

A runnable starter is provided at `/app/train_model.py`. It already handles the
training pipeline and reports both constraints; tune its SVD configuration until
the saved model meets them.
