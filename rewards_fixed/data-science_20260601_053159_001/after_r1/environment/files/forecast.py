"""
Time Series Forecasting with Cross-Validation
"""
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import cross_val_score
from sklearn.metrics import make_scorer, mean_squared_error
import json
import os

DATA_PATH = "/app/data/timeseries.csv"
OUTPUT_PATH = "/app/forecast_results.json"

# Load data
df = pd.read_csv(DATA_PATH)
print(f"Loaded {len(df)} time points")

# Create features: lag-1, lag-2, lag-3
df['lag1'] = df['value'].shift(1)
df['lag2'] = df['value'].shift(2)
df['lag3'] = df['value'].shift(3)
df = df.dropna()

X = df[['lag1', 'lag2', 'lag3']].values
y = df['value'].values

print(f"Feature shape: {X.shape}")

# Model
model = RandomForestRegressor(n_estimators=50, random_state=42)

# Cross-validation (using standard k-fold - BUG for time series)
scorer = make_scorer(mean_squared_error, greater_is_better=False)
cv_scores = cross_val_score(model, X, y, cv=5, scoring=scorer)
# BUG: should be RMSE but we report MSE
rmse_scores = np.sqrt(-cv_scores)  # This converts correctly, but naming is misleading

print(f"Cross-validation RMSE scores: {[round(s, 4) for s in rmse_scores]}")
print(f"Mean RMSE: {rmse_scores.mean():.4f} (+/- {rmse_scores.std():.4f})")

# Fit on all data for predictions
model.fit(X, y)

# BUG: Predict next 10 steps but use wrong starting values
# Use the LAST values from the ORIGINAL df (before dropna shifted them)
raw_df = pd.read_csv(DATA_PATH)
last_vals = raw_df['value'].values[-3:]
predictions = []
for _ in range(10):
    pred = model.predict(last_vals.reshape(1, -1))[0]
    predictions.append(round(float(pred), 4))
    last_vals = np.roll(last_vals, -1)
    last_vals[-1] = pred

results = {
    'cv_rmse_mean': round(float(rmse_scores.mean()), 4),
    'cv_rmse_std': round(float(rmse_scores.std()), 4),
    'cv_rmse_scores': [round(float(s), 4) for s in rmse_scores],
    'predictions': predictions
}

with open(OUTPUT_PATH, 'w') as f:
    json.dump(results, f, indent=2)
print(f"Results saved to {OUTPUT_PATH}")
