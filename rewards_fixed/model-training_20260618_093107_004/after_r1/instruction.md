I need a binary classifier trained on the census data in /app/data/train.csv. The target column is "income" (0 for <=50K, 1 for >50K).

Requirements:
- The trained model must be saved as /app/model.txt
- Achieve at least 0.872 accuracy on a held-out test set drawn from the same distribution
- The saved model file must be under 500 KB
- Use LightGBM for training

The dataset has a mix of numerical and categorical features. Some categorical columns have missing values.