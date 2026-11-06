#!/bin/bash
set -e

source env/bin/activate

python << 'EOF'
from cade.cade import CADE
from scipy.spatial.distance import cosine
import json

# Train compass model and two slice models
aligner = CADE(size=50)
aligner.train_compass("compass.txt")
slice_one = aligner.train_slice("sample_text_1.txt")
slice_two = aligner.train_slice("sample_text_2.txt")

# Compute cosine similarity for "like"
similarity = 1 - cosine(slice_one.wv["like"], slice_two.wv["like"])

# Write result file
with open("/app/result.txt", "w") as f:
    f.write(str(float(similarity)) + "\n")

# Gather metadata for training summary
dimensions = slice_one.wv.vector_size  # typically 50

# Count non-empty lines in compass.txt as an approximation of word count
compass_word_count = sum(1 for line in open("compass.txt", "r") if line.strip())

summary = {
    "similarity": float(similarity),
    "dimensions": dimensions,
    "compass_words": compass_word_count,
    "word": "like"
}

# Write training summary as JSON
with open("/app/training_summary.txt", "w") as f:
    json.dump(summary, f)
EOF