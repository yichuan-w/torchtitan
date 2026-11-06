I need you to build a specialized ETL pipeline and serving layer for a new semantic analysis project. 

We have a proprietary, closed-source legacy tool that generates specialized document embeddings. The tool has been compiled into a stripped binary located at `/app/embed_oracle`. It takes a plain text string via standard input and outputs a raw stream of float32 bytes representing a 64-dimensional embedding vector.

Your task consists of the following steps:

1. **Analysis Environment Setup:** First, set up a local SQLite database in `/home/user/data/embeddings.db` to act as our large-scale data storage manager. Create a table `docs` with columns `id` (INTEGER PRIMARY KEY), `text` (TEXT), and `embedding` (BLOB).

2. **Embedding Computation & ETL:** Write a Python script to process a dataset of raw texts located at `/home/user/raw_texts.json` (a list of strings). For each string, use the `/app/embed_oracle` binary to compute the 64-dimensional embedding. Store the ID, original text, and the binary embedding in the SQLite database.

3. **Linear Algebra & Statistical Analysis:** Once the data is loaded, you must perform a statistical analysis. Calculate the pairwise cosine similarity between all embeddings in the database. Perform a one-sample t-test to determine if the mean cosine similarity differs significantly from 0.0. Calculate the 95% confidence interval for the mean similarity.

4. **Service Deployment:** Finally, build and start a Python web service (using Flask, FastAPI, or similar) that listens on `127.0.0.1:8080`. The service must expose the following endpoints:
   - `GET /stats`: Returns a JSON object containing the results of your statistical analysis: `{"mean_similarity": <float>, "p_value": <float>, "ci_lower": <float>, "ci_upper": <float>}`.
   - `POST /search`: Accepts a JSON payload `{"query": "<text>", "top_k": <int>}`. It must compute the embedding for the query using `/app/embed_oracle`, use linear algebra to find the `top_k` most similar documents in the database based on cosine similarity, and return a JSON list of dictionaries: `[{"id": <int>, "text": "<text>", "score": <float>}, ...]`.

The service must remain running in the background so our integration test suite can send HTTP requests to it.