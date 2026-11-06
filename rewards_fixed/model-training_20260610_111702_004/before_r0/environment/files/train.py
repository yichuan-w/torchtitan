#!/usr/bin/env python3
"""Training script for income prediction model.

This script loads the training data, preprocesses it, trains a classifier,
and saves the model.
"""

import pandas as pd
import numpy as np
import pickle
import os
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score


def main():
    # Load data
    print("Loading data...")
    data_path = "/app/data/train_data.csv"
    df = pd.read_csv(data_path)
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    print(f"Columns: {df.columns.tolist()}")

    # Separate features and target
    target_col = df.columns[-1]
    X = df.drop(columns=[target_col])
    y = df[target_col]

    # Encode target
    le = LabelEncoder()
    y = le.fit_transform(y)
    print(f"Target classes: {le.classes_}")

    # Handle categorical features - use one-hot encoding
    categorical_cols = X.select_dtypes(include=['object']).columns.tolist()
    print(f"Categorical columns: {categorical_cols}")

    X = pd.get_dummies(X, columns=categorical_cols, drop_first=True)
    print(f"After one-hot encoding: {X.shape[1]} features")

    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Train model
    print("Training Random Forest...")
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=20,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_scaled, y)

    # Quick check
    train_pred = model.predict(X_scaled)
    train_acc = accuracy_score(y, train_pred)
    print(f"Training accuracy: {train_acc:.4f}")

    # Save model
    model_path = "/app/model.pkl"
    with open(model_path, 'wb') as f:
        pickle.dump(model, f)

    model_size = os.path.getsize(model_path) / (1024 * 1024)
    print(f"Model saved to {model_path} ({model_size:.2f} MB)")


if __name__ == "__main__":
    main()