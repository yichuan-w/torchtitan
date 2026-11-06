"""
ML Training Script - Classifier Evaluation
Loads data from /app/data/features.csv, preprocesses, trains a classifier,
and outputs evaluation metrics.
"""
import pandas as pd
import numpy as np
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import make_scorer, accuracy_score, precision_score, recall_score, f1_score
import json
import os

DATA_PATH = "/app/data/features.csv"
OUTPUT_PATH = "/app/metrics.json"

# Load data
print("Loading data...")
df = pd.read_csv(DATA_PATH)
X = df.drop('target', axis=1).values
y = df['target'].values
print(f"Loaded {len(X)} samples, {X.shape[1]} features")

# Preprocess: standardize features
print("Standardizing features...")
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# Set up classifier
clf = RandomForestClassifier(n_estimators=100, random_state=42)

# Cross-validation
print("Running 5-fold cross-validation...")
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

scoring = {
    'accuracy': make_scorer(accuracy_score),
    'precision': make_scorer(precision_score),
    'recall': make_scorer(recall_score),
    'f1': make_scorer(f1_score),
}

results = {}
for name, scorer in scoring.items():
    scores = cross_val_score(clf, X_scaled, y, cv=cv, scoring=scorer)
    results[name] = {
        'mean': round(float(scores.mean()), 4),
        'std': round(float(scores.std()), 4),
        'scores': [round(float(s), 4) for s in scores]
    }
    print(f"  {name}: {results[name]['mean']:.4f} (+/- {results[name]['std']:.4f})")

# Save results
with open(OUTPUT_PATH, 'w') as f:
    json.dump(results, f, indent=2)
print(f"Metrics saved to {OUTPUT_PATH}")
