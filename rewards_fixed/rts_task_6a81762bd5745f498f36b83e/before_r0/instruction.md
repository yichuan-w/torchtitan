Use the CADE library in `/app` to measure how the word "like" has shifted in meaning between two corpora.
Train a compass model on `compass.txt`, then train two slice models on `sample_text_1.txt` and `sample_text_2.txt`.
Compute the cosine similarity for "like" and write it as a single float (followed by a newline) to `/app/result.txt`.
Additionally, write a JSON summary at `/app/training_summary.txt` with the following keys: "similarity" (the computed cosine similarity), "dimensions" (the embedding dimension), "compass_words" (the number of non-empty lines in compass.txt), and "word" (the string "like").
