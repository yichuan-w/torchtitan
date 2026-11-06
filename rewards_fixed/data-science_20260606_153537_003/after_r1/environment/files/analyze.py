#!/usr/bin/env python3
"""Benchmark analysis script - finds the best ML model.

WARNING: This script has several known issues that need to be fixed
before it produces correct results. Read through it carefully.
"""

import csv
import json
import re
import os
import gzip
from collections import defaultdict

RESULTS_DIR = "/app/results"
OUTPUT_FILE = "/app/best_model.txt"

# Model name normalization (INCOMPLETE - some variants are missing)
MODEL_NORM = {
    'XGBoost': 'XGBoost',
    'LightGBM': 'LightGBM',
    'CatBoost': 'CatBoost',
    'RandomForest': 'RandomForest',
    'MLP': 'MLP',
    'FT-Transformer': 'FT-Transformer',
    'ResNet': 'ResNet',
    'SAINT': 'SAINT',
    # Missing: XGB, LGBM, CB, RF, FTT, resnet, saint, xgboost, lightgbm, etc.
}

# Dataset name normalization (INCOMPLETE)
DATASET_NORM = {
    'adult': 'adult', 'credit_g': 'credit_g', 'electricity': 'electricity',
    'bank_marketing': 'bank_marketing', 'gesture': 'gesture', 'jungle': 'jungle',
    'phoneme': 'phoneme', 'helena': 'helena', 'jannis': 'jannis',
    'volkert': 'volkert', 'mini_boone': 'mini_boone', 'albert': 'albert',
    # Missing: Adult, Electricity, Gesture, credit-g, elec, bank_mkt, etc.
}


def parse_all_results():
    """Parse all result files and return list of (model, dataset, accuracy, rows, features)."""
    all_entries = []

    for team_dir in sorted(os.listdir(RESULTS_DIR)):
        team_path = os.path.join(RESULTS_DIR, team_dir)
        if not os.path.isdir(team_path):
            continue

        for fname in os.listdir(team_path):
            fpath = os.path.join(team_path, fname)
            if fname.endswith('.csv'):
                parse_csv(fpath, all_entries)
            elif fname.endswith('.tsv'):
                parse_tsv(fpath, all_entries)
            elif fname.endswith('.json'):
                parse_json(fpath, all_entries)
            elif fname.endswith('.txt') or fname.endswith('.log'):
                parse_log(fpath, all_entries)
            elif fname.endswith('.gz'):
                parse_gzip(fpath, all_entries)

    return all_entries


def parse_csv(fpath, entries):
    """Parse a CSV file. BUG: doesn't handle BOM or Latin-1 encoding."""
    with open(fpath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            model = MODEL_NORM.get(row['model'], row['model'])
            dataset = DATASET_NORM.get(row['dataset'], row['dataset'])
            entries.append({
                'model': model,
                'dataset': dataset,
                'accuracy': float(row['accuracy']),
                'rows': int(row['rows']),
                'features': int(row['features']),
            })


def parse_tsv(fpath, entries):
    """Parse a TSV file."""
    with open(fpath, 'r') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            model = MODEL_NORM.get(row['algo'], row['algo'])
            dataset = DATASET_NORM.get(row['data'], row['data'])
            entries.append({
                'model': model,
                'dataset': dataset,
                'accuracy': float(row['score']),
                'rows': int(row['size_rows']),
                'features': int(row['num_features']),
            })


def parse_json(fpath, entries):
    """Parse a JSON file."""
    with open(fpath) as f:
        data = json.load(f)
    for row in data:
        model = MODEL_NORM.get(row['model_name'], row['model_name'])
        dataset = DATASET_NORM.get(row['dataset_name'], row['dataset_name'])
        entries.append({
            'model': model,
            'dataset': dataset,
            'accuracy': row['acc'],
            'rows': row['n_rows'],
            'features': row['n_features'],
        })


def parse_log(fpath, entries):
    """Parse a benchmark log file. BUG: doesn't filter out F1 entries."""
    with open(fpath) as f:
        for line in f:
            m = re.match(
                r'Model: (\S+) \| Dataset: (\S+) \| (Acc|F1): ([\d.]+) \| Rows: (\d+) \| Feats: (\d+)',
                line
            )
            if m:
                model_raw, dataset_raw, metric, acc_str, rows_str, feats_str = m.groups()
                model = MODEL_NORM.get(model_raw, model_raw)
                dataset = DATASET_NORM.get(dataset_raw, dataset_raw)
                entries.append({
                    'model': model,
                    'dataset': dataset,
                    'accuracy': float(acc_str),
                    'rows': int(rows_str),
                    'features': int(feats_str),
                })


def parse_gzip(fpath, entries):
    """Parse a gzipped CSV file."""
    with gzip.open(fpath, 'rt', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            model = MODEL_NORM.get(row['algo'], row['algo'])
            dataset = DATASET_NORM.get(row['data'], row['data'])
            entries.append({
                'model': model,
                'dataset': dataset,
                'accuracy': float(row['score']),
                'rows': int(row['size_rows']),
                'features': int(row['num_features']),
            })


def compute_best_model(entries):
    """Find the best model."""
    # Filter datasets
    qualifying = [
        e for e in entries
        if e['rows'] >= 1000 and e['features'] <= 50
    ]

    # Compute per-model average (simple approach - may double count)
    model_scores = defaultdict(list)
    for e in qualifying:
        model_scores[e['model']].append(e['accuracy'])

    # Compute averages
    model_avgs = {}
    for model, scores in model_scores.items():
        if len(scores) < 5:
            continue
        model_avgs[model] = sum(scores) / len(scores)

    if not model_avgs:
        return None

    return max(model_avgs, key=model_avgs.get)


def main():
    entries = parse_all_results()
    print(f"Parsed {len(entries)} total entries")

    best = compute_best_model(entries)
    if best:
        print(f"Best model: {best}")
        with open(OUTPUT_FILE, 'w') as f:
            f.write(best + '\n')
    else:
        print("No models found!")


if __name__ == '__main__':
    main()
